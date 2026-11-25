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
