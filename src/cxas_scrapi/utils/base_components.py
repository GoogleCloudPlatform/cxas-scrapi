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

"""Core abstract visual base Component elements for cxas_scrapi

HTML reporting.
"""

from __future__ import annotations

import abc
import functools
import html
import os
import pathlib
import re
import string
from collections.abc import Sequence
from typing import Any

CURRENT_DIR = pathlib.Path(__file__).parent
COMPONENTS_DIR = (CURRENT_DIR / "../resources/components").resolve()

_SNAKE_CASE_RE = re.compile(r"(?<!^)(?=[A-Z])")

_TEMPLATE_PATH_BY_NAME: dict[str, str] = {}


def build_template_registry() -> None:
    """Indexes template file paths lazily on first template resolution."""
    for root, _, files in os.walk(COMPONENTS_DIR):
        for file in files:
            if file.endswith(".html"):
                name_without_ext, _ = os.path.splitext(file)
                rel_path = os.path.relpath(
                    os.path.join(root, file), COMPONENTS_DIR
                )
                _TEMPLATE_PATH_BY_NAME[name_without_ext] = rel_path


def _convert_to_snake_case(name: str) -> str:
    """Converts a CamelCase string to snake_case using pre-compiled regex."""
    return _SNAKE_CASE_RE.sub("_", name).lower()


@functools.cache
def load_component(relative_path: str) -> str:
    """Loads raw component text from the resources directory with caching."""
    full_path = COMPONENTS_DIR / relative_path
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()


def escape(text: Any) -> str:
    """HTML-escapes a string safely."""
    if text is None:
        return ""
    return html.escape(str(text))


def fmt_duration(seconds: float | None) -> str:
    """Formats seconds into a human-readable duration string."""
    seconds_per_minute = 60
    if seconds is None:
        return ""
    if seconds >= seconds_per_minute:
        return f"{seconds / seconds_per_minute:.1f}m"
    return f"{seconds:.1f}s"


class Component(abc.ABC):
    """A base for declarative UI components with template auto-discovery.

    Attributes:
      template: Raw template HTML or relative template file path string.
    """

    template: str = ""

    def get_resolved_template(self) -> str:
        """Gets the resolved template HTML string resolved lazily on first

        render.

        Returns:
          The resolved raw template HTML content string.

        Raises:
          FileNotFoundError: If the template file cannot be found in the
            registry.
        """
        if self.template:
            if self.template.endswith(".html"):
                return load_component(self.template)
            return self.template
        if not _TEMPLATE_PATH_BY_NAME:
            build_template_registry()
        snake_name = _convert_to_snake_case(self.__class__.__name__)
        template_path = _TEMPLATE_PATH_BY_NAME.get(snake_name)
        if template_path:
            return load_component(template_path)
        raise FileNotFoundError(
            f"Template file for {self.__class__.__name__!r} "
            f"(resolved as {snake_name!r}.html) "
            "was not found in components template registry!"
        )

    @abc.abstractmethod
    def render(self) -> str:
        """Renders the component tree recursively into raw HTML."""
        pass

    def substitute(self, **kwargs: Any) -> str:
        """Substitutes template variables dynamically while HTML-escaping

        strings.

        Args:
          **kwargs: Variable mappings passed to template placeholders.

        Returns:
          The rendered HTML markup string.
        """
        escaped_kwargs = {}
        for k, v in kwargs.items():
            if isinstance(v, Component):
                escaped_kwargs[k] = v.render()
            elif (
                isinstance(v, Sequence)
                and not isinstance(v, str)
                and all(isinstance(item, Component) for item in v)
            ):
                escaped_kwargs[k] = "\n".join(item.render() for item in v)
            elif v is None:
                escaped_kwargs[k] = ""
            else:
                escaped_kwargs[k] = escape(v)
        return string.Template(self.get_resolved_template()).substitute(
            **escaped_kwargs
        )


class EmptyComponent(Component):
    """A safe presentational placeholder representing empty/no-op elements."""

    def render(self) -> str:
        """Returns an empty string representing no markup."""
        del self
        return ""


class Raw(Component):
    """A raw pre-rendered HTML wrapper preventing escaping.

    Attributes:
      content: Pre-rendered HTML markup string.
    """

    def __init__(self, content: str) -> None:
        """Initializes the instance.

        Args:
          content: Pre-rendered HTML markup string.
        """
        super().__init__()
        self.content = content

    def render(self) -> str:
        """Returns the pre-rendered HTML string directly."""
        return self.content


class ComponentGroup(Component):
    """A visual container grouping child components recursively.

    Attributes:
      children: Sub-nodes representing sequence of visual child components.
    """

    def __init__(self, children: Sequence[Component]) -> None:
        """Initializes the instance.

        Args:
          children: Sequence of child component nodes.
        """
        super().__init__()
        self.children = children

    def render(self) -> str:
        """Renders all visual children joined recursively by newlines."""
        return "\n".join(child.render() for child in self.children)
