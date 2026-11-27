# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared YAML 1.2 parsing helpers."""

from __future__ import annotations

import re
from typing import Any

import yaml

_YAML12_BOOL_RE = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")


class Yaml12SafeLoader(yaml.SafeLoader):
    """YAML 1.2-compatible SafeLoader (only ``true``/``false`` are booleans).

    Prevents YAML 1.1 implicit boolean coercion on values such as country code
    ``NO`` (Norway) or ``ON`` / ``OFF`` / ``YES``.
    """


Yaml12SafeLoader.yaml_implicit_resolvers = {
    ch: [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag != "tag:yaml.org,2002:bool"
    ]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for _bool_lead in "tTfF":
    Yaml12SafeLoader.add_implicit_resolver(
        "tag:yaml.org,2002:bool", _YAML12_BOOL_RE, [_bool_lead]
    )


class UniqueKeyYaml12SafeLoader(Yaml12SafeLoader):
    """YAML 1.2 SafeLoader rejecting duplicate explicit mapping keys.

    Allows standard ``<<: *anchor`` merge-key overrides and nested anchors
    while failing fast on explicit duplicate keys (including inside inline
    merge targets).
    """

    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self._checked_nodes: set[int] = set()

    def _check_merge_target(self, val_node: yaml.Node, *, deep: bool) -> None:
        if isinstance(val_node, yaml.MappingNode):
            self._check_mapping_node(val_node, deep=deep)
        elif isinstance(val_node, yaml.SequenceNode):
            for sub_node in val_node.value:
                if isinstance(sub_node, yaml.MappingNode):
                    self._check_mapping_node(sub_node, deep=deep)

    def _check_mapping_node(
        self, node: yaml.MappingNode, *, deep: bool
    ) -> None:
        node_id = id(node)
        if node_id in self._checked_nodes:
            return
        self._checked_nodes.add(node_id)
        seen: set[Any] = set()
        for key_node, val_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                self._check_merge_target(val_node, deep=deep)
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found unhashable key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    f"Duplicate mapping key '{key}'",
                    key_node.start_mark,
                )
            seen.add(key)

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Any, Any]:
        self._check_mapping_node(node, deep=deep)
        return super().construct_mapping(node, deep=deep)
