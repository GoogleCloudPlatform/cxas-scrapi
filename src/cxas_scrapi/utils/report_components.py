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

"""Concrete visual components library for cxas_scrapi HTML reporting."""

from __future__ import annotations

from collections.abc import Sequence

from cxas_scrapi.utils import base_components


class BaseShell(base_components.Component):
    """A presentational envelope scaffolding the entire HTML report document.

    Attributes:
      template: Scaffolding layout relative template file path string.
      title: Scaffolding page document title string.
      body_content: Sequence containing visual child component tree contents.
    """

    template = "base/base_shell.html"

    def __init__(
        self,
        title: str,
        body_content: Sequence[base_components.Component],
    ) -> None:
        """Initializes the instance.

        Args:
          title: Scaffolding page document title string.
          body_content: Sequence containing visual child component tree
            contents.
        """
        super().__init__()
        self.title = title
        self.body_content = body_content

    def render(self) -> str:
        """Renders the complete visual page envelope.

        Embeds base styles, interactions, and body content.
        """
        return self.substitute(
            TITLE=self.title,
            CSS_CONTENT=base_components.Raw(
                base_components.load_component("base/base.css")
            ),
            BODY_CONTENT=self.body_content,
            JS_CONTENT=base_components.Raw(
                base_components.load_component("base/interaction.js")
            ),
        )
