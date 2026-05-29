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

import dataclasses
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


@dataclasses.dataclass(kw_only=True)
class ToolRow(base_components.Component):
    """A single row displaying tool test metrics.

    Attributes:
      passed_str: Indicates whether the evaluation passed ("true" or "false").
      status_class: CSS class name reflecting test status.
      status: Text badge reflecting test outcome status.
      tool_name: The name of the tool being tested.
      test_name: The visual scenario test case name.
      latency: Formatted latency duration in milliseconds.
      errors: Raw test execution error trace messages if any..
    """

    passed_str: str
    status_class: str
    status: str
    tool_name: str
    test_name: str
    latency: str
    errors: str

    template = "tables/tool_row.html"

    def render(self) -> str:
        """Renders the HTML markup for a single tool evaluation row."""
        return self.substitute(
            PASSED_STR=self.passed_str,
            STATUS_CLASS=self.status_class,
            STATUS=self.status,
            TOOL_NAME=self.tool_name,
            TEST_NAME=self.test_name,
            LATENCY=self.latency,
            ERRORS=self.errors,
        )


@dataclasses.dataclass(kw_only=True)
class ToolCard(base_components.Component):
    """A metrics scorecard summarizing overall tool evaluation statistics.

    Attributes:
      passed: Number of successful tool test cases..
      total: Total number of tool test cases executed..
      pct_str: Successful tool test percentage formatting string..
      tool_rows: Pre-rendered HTML rows of individual tool evaluations..
    """

    passed: int
    total: int
    pct_str: str
    tool_rows: base_components.Component

    template = "cards/tool_card.html"

    def render(self) -> str:
        """Renders HTML for the overall tool evaluation scorecard."""
        return self.substitute(
            PASSED=self.passed,
            TOTAL=self.total,
            PCT=self.pct_str,
            TOOL_ROWS=self.tool_rows,
        )


@dataclasses.dataclass(kw_only=True)
class CallbackRow(base_components.Component):
    """A single row displaying callback test metrics.

    Attributes:
      passed_str: Indicates whether the evaluation passed ("true" or "false").
      status_class: CSS class name reflecting test status.
      status: Text badge reflecting test outcome status.
      agent_name: The name of the agent responding to callback.
      callback_type: The exact callback namespace class name.
      test_name: The visual scenario test case name.
      error: Raw test execution error trace messages if any..
    """

    passed_str: str
    status_class: str
    status: str
    agent_name: str
    callback_type: str
    test_name: str
    error: str

    template = "tables/callback_row.html"

    def render(self) -> str:
        """Renders the HTML markup for a single callback evaluation row."""
        return self.substitute(
            PASSED_STR=self.passed_str,
            STATUS_CLASS=self.status_class,
            STATUS=self.status,
            AGENT_NAME=self.agent_name,
            CALLBACK_TYPE=self.callback_type,
            TEST_NAME=self.test_name,
            ERROR=self.error,
        )


@dataclasses.dataclass(kw_only=True)
class CallbackCard(base_components.Component):
    """A metrics scorecard summarizing overall callback evaluation statistics.

    Attributes:
      passed: Number of successful callback test cases..
      total: Total number of callback test cases executed..
      pct_str: Successful callback test percentage formatting string..
      callback_rows: Pre-rendered HTML rows of individual callback evaluations..
    """

    passed: int
    total: int
    pct_str: str
    callback_rows: base_components.Component

    template = "cards/callback_card.html"

    def render(self) -> str:
        """Renders HTML for the overall callback evaluation scorecard."""
        return self.substitute(
            PASSED=self.passed,
            TOTAL=self.total,
            PCT=self.pct_str,
            CALLBACK_ROWS=self.callback_rows,
        )
