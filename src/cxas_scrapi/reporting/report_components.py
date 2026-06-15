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

from __future__ import annotations

"""Concrete visual components library for cxas_scrapi HTML reporting."""

from enum import Enum
import functools
import html
import json
import os
import re
import string
from typing import Any
import urllib.parse

from cxas_scrapi.reporting.base_components import (
    Component,
    ComponentGroup,
    EmptyComponent,
    Raw,
    escape,
    fmt_duration,
    load_component,
)
from dataclasses import dataclass
from typing import Any

@dataclass
class CategoryStats:
    passed: int
    total: int
    pct: float
    pct_str: str
    value_class: str
    duration_s: float
    modality: str

@dataclass
class EvaluationStats:
    passed_sum: int
    total_sum: int
    overall_pct: float
    golden: CategoryStats
    sim: CategoryStats
    tool: CategoryStats
    callback: CategoryStats
    failure_groups: dict[str, Any]

@dataclass
class ExpectationDetail:
    raw: dict[str, Any]
    @property
    def expectation(self) -> str: return str(self.raw.get("expectation", "?"))
    @property
    def status(self) -> str: return str(self.raw.get("status", "?"))
    @property
    def is_met(self) -> bool: return self.status == "Met"
    @property
    def justification(self) -> str: return str(self.raw.get("justification", ""))

@dataclass
class SimStepDetail:
    raw: dict[str, Any]
    @property
    def goal(self) -> str: return str(self.raw.get("goal", "?"))
    @property
    def success_criteria(self) -> str: return str(self.raw.get("success_criteria", "?"))
    @property
    def status(self) -> str: return str(self.raw.get("status", "?"))
    @property
    def justification(self) -> str: return str(self.raw.get("justification", ""))

@dataclass
class TraceEntry:
    raw: tuple[str, ...]
    @property
    def kind(self) -> str: return self.raw[0]
    @property
    def text(self) -> str: return self.raw[1]
    @property
    def result(self) -> str: return self.raw[2] if len(self.raw) > 2 else ""

@dataclass
class GoldenRunResult:
    raw: dict[str, Any]
    @property
    def name(self) -> str: return str(self.raw.get("name", "?"))
    @property
    def passed(self) -> bool: return bool(self.raw.get("passed", False))
    @property
    def status(self) -> str: return "PASS" if self.passed else "FAIL"
    @property
    def duration_s(self) -> float: return float(self.raw.get("duration_s", 0.0))
    @property
    def modality(self) -> str: return str(self.raw.get("modality", "text"))
    @property
    def expectation_details(self) -> list[ExpectationDetail]:
        return [ExpectationDetail(raw=x) for x in (self.raw.get("expectation_details", []) or [])]
    @property
    def expectations(self) -> list[ExpectationDetail]:
        return [ExpectationDetail(raw=x) for x in (self.raw.get("expectations", []) or [])]
    @property
    def turns(self) -> list[dict[str, Any]]: return self.raw.get("turns", []) or []

@dataclass
class SimulationRunResult:
    raw: dict[str, Any]
    @property
    def name(self) -> str: return str(self.raw.get("name", "?"))
    @property
    def passed(self) -> bool: return bool(self.raw.get("passed", False))
    @property
    def duration_s(self) -> float: return float(self.raw.get("duration_s", 0.0))
    @property
    def sim_wall_clock_s(self) -> float: return float(self.raw.get("sim_wall_clock_s", 0.0))
    @property
    def modality(self) -> str: return str(self.raw.get("modality", "text"))
    @property
    def run_number(self) -> int: return int(self.raw.get("run", 1))
    @property
    def session_id(self) -> str: return str(self.raw.get("session_id") or self.raw.get("evaluation", ""))
    @property
    def goals(self) -> int: return int(self.raw.get("goals", 0))
    @property
    def expectations(self) -> int: return int(self.raw.get("expectations", 0))
    @property
    def turns(self) -> int: return int(self.raw.get("turns", 0))
    @property
    def session_parameters(self) -> dict[str, Any]: return self.raw.get("session_parameters", {}) or {}
    @property
    def step_details(self) -> list[SimStepDetail]:
        return [SimStepDetail(raw=s) for s in (self.raw.get("step_details", []) or [])]
    @property
    def expectation_details(self) -> list[ExpectationDetail]:
        return [ExpectationDetail(raw=x) for x in (self.raw.get("expectation_details", []) or [])]
    @property
    def processed_trace(self) -> list[TraceEntry]:
        return [TraceEntry(raw=t) for t in (self.raw.get("_processed_trace", []) or [])]
    @property
    def error(self) -> str: return str(self.raw.get("error", ""))

@dataclass
class ToolRunResult:
    raw: dict[str, Any]
    @property
    def name(self) -> str: return str(self.raw.get("name", "?"))
    @property
    def passed(self) -> bool: return bool(self.raw.get("passed", False))
    @property
    def status(self) -> str: return str(self.raw.get("status", "?"))
    @property
    def tool(self) -> str: return str(self.raw.get("tool", "?"))
    @property
    def latency_ms(self) -> float: return float(self.raw.get("latency_ms", 0.0))
    @property
    def errors(self) -> str: return str(self.raw.get("errors", ""))

@dataclass
class CallbackRunResult:
    raw: dict[str, Any]
    @property
    def name(self) -> str: return str(self.raw.get("name", "?"))
    @property
    def passed(self) -> bool: return bool(self.raw.get("passed", False))
    @property
    def status(self) -> str: return str(self.raw.get("status", "?"))
    @property
    def agent(self) -> str: return str(self.raw.get("agent", "?"))
    @property
    def callback_type(self) -> str: return str(self.raw.get("callback_type", "?"))
    @property
    def error(self) -> str: return str(self.raw.get("error", ""))


class Outcome(Enum):
    """Enumeration representing evaluation outcomes."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNSPECIFIED = "UNSPECIFIED"


class BaseShell(Component):
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
        body_content: list[Component],
    ) -> None:
        """Initializes the instance.

        Args:
          title: Scaffolding page document title string.
          body_content: Sequence containing visual child component tree contents.
        """
        super().__init__()
        self.title = title
        self.body_content = body_content

    @property
    def css_content(self) -> str:
        return load_component("base/base.css")

    @property
    def js_interaction(self) -> str:
        return load_component("base/interaction.js")

    def render(self) -> str:
        body_html = "\n".join(child.render() for child in self.body_content)
        return self.substitute(
            TITLE=escape(self.title),
            CSS_CONTENT=Raw(self.css_content),
            BODY_CONTENT=Raw(body_html),
            JS_CONTENT=Raw(self.js_interaction),
        )


class Header(Component):
    """Report top heading component."""

    def __init__(self, title: str):
        self.title = title

    def render(self) -> str:
        return self.substitute(TITLE=escape(self.title))


class Scorecard(Component):
    """Overall metrics display container scorecard component."""

    def __init__(
        self,
        *,
        ts: str,
        summary_cards: list[SummaryCard],
        stats: EvaluationStats,
        model: str | None = None,
        report_title: str = "Combined Eval Report",
    ):
        self.ts = ts
        self.summary_cards = summary_cards
        self.stats = stats
        self.model = model
        self.report_title = report_title

    def render(self) -> str:
        # Calculate overall combined metrics cleanly via strongly-typed wrapper properties.
        passed = self.stats.passed_sum
        total = self.stats.total_sum
        pct = self.stats.overall_pct

        big_pct_cls = "pass" if pct >= 90 else "fail"
        pct_str = (
            f"{pct:.1f}"
            if self.report_title == "Simulation Eval Report"
            else f"{pct:.0f}"
        )
        ts_str = self.ts
        if self.model:
            ts_str = f"{self.ts} | model: {self.model}"

        return self.substitute(
            BIG_PCT_CLASS=big_pct_cls,
            PCT=pct_str,
            PASSED=passed,
            TOTAL=total,
            TS=ts_str,
            SUMMARY_CARDS=ComponentGroup(self.summary_cards),
        )


class SummaryCard(Component):
    """Single metric block card widget component."""

    def __init__(
        self,
        section_id: str,
        label: str,
        value_class: str,
        passed: int,
        total: int,
        duration: Component = EmptyComponent(),
    ):
        self.section_id = section_id
        self.label = label
        self.value_class = value_class
        self.passed = passed
        self.total = total
        self.duration = duration

    def render(self) -> str:
        return self.substitute(
            SECTION_ID=escape(self.section_id),
            LABEL=escape(self.label),
            VALUE_CLASS=escape(self.value_class),
            PASSED=str(self.passed),
            TOTAL=str(self.total),
            DURATION_HTML=self.duration,
        )


class Controls(Component):
    """Interactive scorecard UI accordion controls."""

    def render(self) -> str:
        return self.substitute()


class ResultsTable(Component):
    """Evals index searchable master table component."""

    def __init__(self, unified: list[dict[str, Any]]):
        self.unified = unified

    def render(self) -> str:
        if not self.unified:
            return ""
        rows = []
        for u in self.unified:
            type_cls = u["type"]
            safe_name = escape(u["name"].replace("'", "'"))
            passed_str = "true" if u["passed"] else "false"

            # Status Badge
            if u["type"] == "sim" and "run_results" in u:
                runs = u["run_results"]
                passed_runs = sum(1 for r in runs if r.passed)
                total_runs = len(runs)
                if passed_runs == total_runs:
                    status_comp = StatusBadge(Outcome.PASS)
                elif passed_runs == 0:
                    status_comp = StatusBadge(Outcome.FAIL)
                else:
                    status_comp = StatusMixed(str(passed_runs), str(total_runs))
            elif u["passed"]:
                status_comp = StatusBadge(Outcome.PASS)
            else:
                status_comp = StatusBadge(Outcome.FAIL)

            # Run Dots
            if u["type"] == "sim" and "run_results" in u:
                dots = []
                for idx, r in enumerate(u["run_results"]):
                    dot_cls = "p" if r.passed else "f"
                    run_lbl = r.run_number
                    dots.append(
                        RunDot(dot_cls, str(run_lbl), safe_name, str(idx))
                    )
                detail_comp = ComponentGroup(dots)
            else:
                detail_comp = Raw(escape(u.get("duration_str", "-")))

            rows.append(
                ResultsRow(
                    passed_str=passed_str,
                    type_str=u["type"].upper(),
                    safe_name=safe_name,
                    status=status_comp,
                    type_cls=type_cls,
                    eval_name=u["name"],
                    detail=detail_comp,
                )
            )

        return self.substitute(TABLE_ROWS=ComponentGroup(rows))


class ResultsRow(Component):
    """Results index list row component."""

    def __init__(
        self,
        passed_str: str,
        type_str: str,
        safe_name: str,
        status: Component,
        type_cls: str,
        eval_name: str,
        detail: Component,
    ):
        self.passed_str = passed_str
        self.type_str = type_str
        self.safe_name = safe_name
        self.status = status
        self.type_cls = type_cls
        self.eval_name = eval_name
        self.detail = detail

    def render(self) -> str:
        return self.substitute(
            PASSED_STR=escape(self.passed_str),
            TYPE=escape(self.type_str),
            SAFE_NAME=self.safe_name,
            STATUS_HTML=self.status,
            TYPE_CLS=escape(self.type_cls),
            EVAL_NAME=escape(self.eval_name),
            DETAIL_HTML=self.detail,
        )


class StatusBadge(Component):
    """Result status badge component."""

    def __init__(self, outcome: Outcome):
        self.outcome = outcome

    def render(self) -> str:
        outcome_cls = "pass" if self.outcome == Outcome.PASS else "fail"
        outcome_lbl = "PASS" if self.outcome == Outcome.PASS else "FAIL"
        return self.substitute(CLASS=outcome_cls, LABEL=outcome_lbl)


class StatusMixed(Component):
    """Mixed outcome status badge component."""

    def __init__(self, passed_count: str, total_count: str):
        self.passed_count = passed_count
        self.total_count = total_count

    def render(self) -> str:
        return self.substitute(PASSED=self.passed_count, TOTAL=self.total_count)


class RunDot(Component):
    """Clickable dot link representing single run execution component."""

    def __init__(
        self, dot_class: str, label: str, target_id: str, run_index: str
    ):
        self.dot_class = dot_class
        self.label = label
        self.target_id = target_id
        self.run_index = run_index

    def render(self) -> str:
        return self.substitute(
            CLASS=escape(self.dot_class),
            RUN=escape(self.label),
            SAFE_NAME=self.target_id,
            IDX=escape(self.run_index),
        )


class MetadataBadge(Component):
    """Presentational link/badge displaying metadata values."""

    def __init__(self, value: str):
        self.value = value

    def render(self) -> str:
        return self.substitute(VALUE=escape(self.value))


class SectionHeader(Component):
    """Section title heading component."""

    def __init__(
        self, section_id: str, title: str, passed: int, total: int, pct: str
    ):
        self.section_id = section_id
        self.title = title
        self.passed = passed
        self.total = total
        self.pct = pct

    def render(self) -> str:
        return self.substitute(
            SECTION_ID=escape(self.section_id),
            TITLE=escape(self.title),
            PASSED=str(self.passed),
            TOTAL=str(self.total),
            PCT=escape(self.pct),
        )


class SessionLink(Component):
    """Dialogflow CX console active session trace link component."""

    def __init__(self, ces_base: str, session_id: str):
        self.ces_base = ces_base
        self.session_id = session_id

    def render(self) -> str:
        link = f"{self.ces_base}?panel=conversation_list&id={self.session_id}&source=EVAL"
        return self.substitute(
            LINK=link,
            SESSION_ID=self.session_id,
        )


class SessionLinkSimple(Component):
    """Plain text fallback session identifier badge component."""

    def __init__(self, session_id: str):
        self.session_id = session_id

    def render(self) -> str:
        return self.substitute(SESSION_ID=self.session_id)


class SessionParameters(Component):
    """Session parameters details block component."""

    def __init__(self, session_params: dict[str, Any]):
        self.session_params = session_params

    def render(self) -> str:
        if not self.session_params:
            return ""

        return self.substitute(JSON=json.dumps(self.session_params, indent=2))


class Justification(Component):
    """Reasoning justification component."""

    def __init__(self, justification: str):
        self.justification = justification

    def render(self) -> str:
        if not self.justification:
            return ""
        return self.substitute(JUSTIFICATION=self.justification)


class StepDetails(Component):
    """Simulation step execution detail component."""

    def __init__(
        self,
        goal: str,
        criteria: str,
        status: str,
        status_class: str,
        justification: Component,
    ):
        self.goal = goal
        self.criteria = criteria
        self.status = status
        self.status_class = status_class
        self.justification = justification

    def render(self) -> str:
        return self.substitute(
            GOAL=self.goal,
            CRITERIA=self.criteria,
            STATUS=self.status,
            STATUS_CLASS=self.status_class,
            JUSTIFICATION_HTML=self.justification,
        )


class TurnRow(Component):
    """Conversation exchange detail turn row component."""

    def __init__(
        self,
        row_class: str,
        turn_index: int,
        semantic_badge: Component,
        user_input: Component,
        comparisons: Component,
    ):
        self.row_class = row_class
        self.turn_index = turn_index
        self.semantic_badge = semantic_badge
        self.user_input = user_input
        self.comparisons = comparisons

    def render(self) -> str:
        return self.substitute(
            ROW_CLASS=escape(self.row_class),
            TURN_INDEX=str(self.turn_index),
            SEMANTIC_BADGE_HTML=self.semantic_badge,
            USER_INPUT_HTML=self.user_input,
            COMPARISONS_HTML=self.comparisons,
        )


class GoldenCard(Component):
    """Collapsible golden test case validation execution card component."""

    def __init__(
        self,
        eval_name_id: str,
        passed_str: str,
        bg_class: str,
        eval_name: str,
        status_badge_cls: str,
        status: str,
        failed_str: str,
        status_class: str,
        turn_count: int,
        duration: Component,
        session_link: Component,
        session_params: Component,
        turns: Component,
        expectations: Component,
    ):
        self.eval_name_id = eval_name_id
        self.passed_str = passed_str
        self.bg_class = bg_class
        self.eval_name = eval_name
        self.status_badge_cls = status_badge_cls
        self.status = status
        self.failed_str = failed_str
        self.status_class = status_class
        self.turn_count = turn_count
        self.duration = duration
        self.session_link = session_link
        self.session_params = session_params
        self.turns = turns
        self.expectations = expectations

    def render(self) -> str:
        return self.substitute(
            EVAL_NAME_ID=self.eval_name_id,
            PASSED_STR=escape(self.passed_str),
            BG_CLASS=escape(self.bg_class),
            EVAL_NAME=escape(self.eval_name),
            STATUS_BADGE_CLASS=escape(self.status_badge_cls),
            STATUS=escape(self.status),
            FAILED_STR=escape(self.failed_str),
            STATUS_CLASS=escape(self.status_class),
            TURN_COUNT=str(self.turn_count),
            DURATION_HTML=self.duration,
            SESSION_LINK_HTML=self.session_link,
            SESSION_PARAMS_HTML=self.session_params,
            TURNS_HTML=self.turns,
            EXPECTATIONS_HTML=self.expectations,
        )


class SimRunDetail(Component):
    """Collapsible simulated run outcome details block component."""

    def __init__(
        self,
        failed_str: str,
        run_num: int,
        run_status_class: str,
        run_status: str,
        goals: str,
        expectations: str,
        turns: str,
        duration: Component,
        session_link: Component,
        session_params: Component,
        error: Component,
        steps: Component,
        expectation_details: Component,
        trace: Component,
    ):
        self.failed_str = failed_str
        self.run_num = run_num
        self.run_status_class = run_status_class
        self.run_status = run_status
        self.goals = goals
        self.expectations = expectations
        self.turns = turns
        self.duration = duration
        self.session_link = session_link
        self.session_params = session_params
        self.error = error
        self.steps = steps
        self.expectation_details = expectation_details
        self.trace = trace

    def render(self) -> str:
        return self.substitute(
            FAILED_STR=escape(self.failed_str),
            RUN_NUM=str(self.run_num),
            RUN_STATUS_CLASS=escape(self.run_status_class),
            RUN_STATUS=escape(self.run_status),
            GOALS=escape(self.goals),
            EXPECTATIONS=escape(self.expectations),
            TURNS=escape(self.turns),
            DURATION_HTML=self.duration,
            SESSION_LINK_HTML=self.session_link,
            SESSION_PARAMS_HTML=self.session_params,
            ERROR_HTML=self.error,
            STEPS_HTML=self.steps,
            EXPECTATION_DETAILS_HTML=self.expectation_details,
            TRACE_HTML=self.trace,
        )


class SimCard(Component):
    """Collapsible simulated test case CUJ validation card component."""

    def __init__(
        self,
        eval_name_id: str,
        passed_str: str,
        bg_class: str,
        eval_name: str,
        score: str,
        run_details: Component,
    ):
        self.eval_name_id = eval_name_id
        self.passed_str = passed_str
        self.bg_class = bg_class
        self.eval_name = eval_name
        self.score = score
        self.run_details = run_details

    def render(self) -> str:
        return self.substitute(
            EVAL_NAME_ID=self.eval_name_id,
            PASSED_STR=escape(self.passed_str),
            BG_CLASS=escape(self.bg_class),
            EVAL_NAME=escape(self.eval_name),
            SCORE=escape(self.score),
            RUN_DETAILS_HTML=self.run_details,
        )


class SimTrace(Component):
    """Conversation trace log display wrapper component."""

    def __init__(self, turns: str, items: Component):
        self.turns = turns
        self.items = items

    def render(self) -> str:
        return self.substitute(
            TURNS=self.turns,
            ITEMS=self.items,
        )


class UserBubble(Component):
    """User dialogue bubble component."""

    def __init__(self, content: str):
        self.content = content

    def render(self) -> str:
        return self.substitute(CONTENT=self.content)


class AgentBubble(Component):
    """Agent dialogue bubble component."""

    def __init__(self, content: str):
        self.content = content

    def render(self) -> str:
        return self.substitute(CONTENT=self.content)


class SystemBubble(Component):
    """System dialogue event bubble component."""

    def __init__(self, content: str):
        self.content = content

    def render(self) -> str:
        return self.substitute(CONTENT=self.content)


class ToolCall(Component):
    """Trace tool invocation accordion details block component."""

    def __init__(self, icon: str, label: str, content: Component):
        self.icon = icon
        self.label = label
        self.content = content

    def render(self) -> str:
        return self.substitute(
            ICON=self.icon,
            LABEL=self.label,
            CONTENT_HTML=self.content,
        )


class ToolInput(Component):
    """Trace tool input arguments preformatted component."""

    def __init__(self, input_val: str):
        self.input_val = input_val

    def render(self) -> str:
        return self.substitute(INPUT=self.input_val)


class ToolOutput(Component):
    """Trace tool response output preformatted component."""

    def __init__(self, output_val: str):
        self.output_val = output_val

    def render(self) -> str:
        return self.substitute(OUTPUT=self.output_val)


class GoldenSectionCard(Component):
    """Collapsible card displaying golden test executions."""

    def __init__(
        self,
        golden_results: list[GoldenRunResult],
        stats: EvaluationStats,
        ces_base: str | None = None,
    ):
        self.golden_results = golden_results
        self.stats = stats
        self.ces_base = ces_base

    def get_summary_card(self) -> SummaryCard:
        """Compile its own SummaryCard widget dynamically."""
        dur = self.stats.golden.duration_s
        dur_badge = (
            MetadataBadge(value=fmt_duration(dur))
            if dur > 0
            else EmptyComponent()
        )
        return SummaryCard(
            section_id="section-goldens",
            label=f"Goldens ({self.stats.golden.modality})",
            value_class=self.stats.golden.value_class,
            passed=self.stats.golden.passed,
            total=self.stats.golden.total,
            duration=dur_badge,
        )

    def render(self) -> str:
        if not self.golden_results:
            return ""
        cards = []
        for r in self.golden_results:
            passed_str = "true" if r.passed else "false"
            status_class = "pass" if r.passed else "fail"
            status_badge = r.status
            dur = r.duration_s

            session_link = (
                SessionLink(ces_base=self.ces_base, session_id=r.name)
                if self.ces_base and r.name
                else (
                    SessionLinkSimple(session_id=r.name)
                    if r.name
                    else EmptyComponent()
                )
            )

            turns = []
            for i, turn in enumerate(r.turns):
                comparisons = []
                for comp in turn.get("comparisons", []):
                    c_type = comp.get("type")
                    c_met = comp.get("outcome") == "PASS"
                    outcome_cls = "met" if c_met else "not-met"
                    if c_type == "text":
                        comparisons.append(
                            ExpectationOutcome(
                                label=f"Agent Response matched expectation",
                                status_class=outcome_cls,
                                status="Met" if c_met else "Not Met",
                                details=(
                                    f"Expected: {comp.get('expected')}\nActual:"
                                    f" {comp.get('actual')}"
                                ),
                            )
                        )
                    elif c_type == "tool_call":
                        comparisons.append(
                            ExpectationOutcome(
                                label=f"Tool call expectation met",
                                status_class=outcome_cls,
                                status="Met" if c_met else "Not Met",
                                details=(
                                    f"Expected: {comp.get('expected')} with"
                                    f" {comp.get('expected_args')}\nActual:"
                                    f" {comp.get('actual')} with {comp.get('actual_args')}"
                                ),
                            )
                        )
                    elif c_type == "transfer":
                        comparisons.append(
                            ExpectationOutcome(
                                label=f"Agent transfer matched expectation",
                                status_class=outcome_cls,
                                status="Met" if c_met else "Not Met",
                                details=(
                                    f"Expected: {comp.get('expected')}\nActual:"
                                    f" {comp.get('actual')}"
                                ),
                            )
                        )

                text_val = turn.get("user_input") or "(Event/System turn)"
                badge = Raw(f'<span class="turn-badge user">USER</span>')
                turns.append(
                    TurnRow(
                        row_class="turn-user",
                        turn_index=i + 1,
                        semantic_badge=badge,
                        user_input=Raw(text_val),
                        comparisons=ComponentGroup(comparisons),
                    )
                )

            expectations = (
                ExpectationOutcome(
                    label=e.expectation,
                    status_class="met" if e.is_met else "not-met",
                    status="Met" if e.is_met else "Not Met",
                    details=e.justification,
                )
                for e in r.expectations
            )

            cards.append(
                GoldenCard(
                    eval_name_id=urllib.parse.quote(r.name),
                    passed_str=passed_str,
                    bg_class="pass-bg" if r.passed else "fail-bg",
                    eval_name=r.name,
                    status_badge_cls=status_class,
                    status=status_badge,
                    failed_str="false" if r.passed else "true",
                    status_class=status_class,
                    turn_count=len(r.turns),
                    duration=MetadataBadge(value=fmt_duration(r.duration_s)),
                    session_link=session_link,
                    session_params=EmptyComponent(),
                    turns=ComponentGroup(turns),
                    expectations=ComponentGroup(list(expectations)),
                )
            )

        header = SectionHeader(
            "section-goldens",
            "Goldens",
            self.stats.golden.passed,
            self.stats.golden.total,
            self.stats.golden.pct_str,
        )
        return ComponentGroup([header] + cards).render()


class SimSectionCard(Component):
    """Collapsible card displaying simulated test executions."""

    def __init__(
        self,
        sim_results: list[SimulationRunResult],
        stats: EvaluationStats,
        ces_base: str | None = None,
    ):
        self.sim_results = sim_results
        self.stats = stats
        self.ces_base = ces_base

    def get_summary_card(self) -> SummaryCard:
        """Compile its own SummaryCard widget dynamically."""
        dur = self.stats.sim.duration_s
        dur_badge = (
            MetadataBadge(value=fmt_duration(dur))
            if dur > 0
            else EmptyComponent()
        )
        return SummaryCard(
            section_id="section-sims",
            label=f"Sims ({self.stats.sim.modality})",
            value_class=self.stats.sim.value_class,
            passed=self.stats.sim.passed,
            total=self.stats.sim.total,
            duration=dur_badge,
        )

    def render(self) -> str:
        if not self.sim_results:
            return ""

        grouped = {}
        for r in self.sim_results:
            name = r.name
            if name not in grouped:
                grouped[name] = {"pass": 0, "total": 0, "runs": []}
            grouped[name]["total"] += 1
            if r.passed:
                grouped[name]["pass"] += 1
            grouped[name]["runs"].append(r)

        cards = []
        sorted_sims = sorted(
            grouped.items(), key=lambda x: (x[1]["pass"] / x[1]["total"], x[0])
        )

        for name, s in sorted_sims:
            score = f"{s['pass']}/{s['total']}"
            cls = "pass-bg" if s["pass"] == s["total"] else "fail-bg"
            passed_str = "true" if s["pass"] == s["total"] else "false"

            runs = []
            for r in s["runs"]:
                run_cls = "pass" if r.passed else "fail"
                failed_str = "false" if r.passed else "true"

                session_link = (
                    SessionLink(ces_base=self.ces_base, session_id=r.session_id)
                    if self.ces_base and r.session_id
                    else (
                        SessionLinkSimple(session_id=r.session_id)
                        if r.session_id
                        else EmptyComponent()
                    )
                )

                expectations = (
                    ExpectationOutcome(
                        label=x.expectation,
                        status_class="met" if x.is_met else "not-met",
                        status=x.status,
                        details=x.justification,
                    )
                    for x in r.expectation_details
                )

                trace_items = []
                for turn in r.processed_trace:
                    if turn.kind == "user":
                        trace_items.append(UserBubble(content=turn.text))
                    elif turn.kind == "agent":
                        trace_items.append(AgentBubble(content=turn.text))
                    elif turn.kind in ("tool_call", "tool_pair"):
                        lbl, _, args = turn.text.partition(" with args ")
                        lbl = (
                            lbl.replace("Tool Call: ", "")
                            .replace("Tool Call (Output): ", "")
                            .split("/")[-1]
                        )
                        tool_contents = []
                        if args:
                            tool_contents.append(
                                ToolInput(input_val=args.strip())
                            )
                        if turn.kind == "tool_pair" and turn.result:
                            _, _, result_val = turn.result.partition(
                                " with result "
                            )
                            if result_val:
                                tool_contents.append(
                                    ToolOutput(output_val=result_val.strip())
                                )
                        trace_items.append(
                            ToolCall(
                                icon="🔧",
                                label=lbl,
                                content=ComponentGroup(tool_contents),
                            )
                        )
                    elif turn.kind == "tool_resp":
                        lbl, _, result_val = turn.text.partition(
                            " with result "
                        )
                        lbl = lbl.replace("Tool Response: ", "").split("/")[-1]
                        tool_contents = []
                        if result_val:
                            tool_contents.append(
                                ToolOutput(output_val=result_val.strip())
                            )
                        trace_items.append(
                            ToolCall(
                                icon="📤",
                                label=lbl,
                                content=ComponentGroup(tool_contents),
                            )
                        )
                    else:
                        trace_items.append(SystemBubble(content=turn.text))

                trace_comp = (
                    SimTrace(
                        turns=str(r.turns), items=ComponentGroup(trace_items)
                    )
                    if trace_items
                    else EmptyComponent()
                )

                dur_comp = (
                    MetadataBadge(value=fmt_duration(r.duration_s))
                    if r.duration_s
                    else EmptyComponent()
                )

                session_params = (
                    SessionParameters(r.session_parameters)
                    if r.session_parameters
                    else EmptyComponent()
                )

                steps = []
                for step in r.step_details:
                    step_cls = "pass" if step.status == "Completed" else "fail"
                    badge_cls = step_cls.replace("pass", "met").replace(
                        "fail", "not-met"
                    )
                    justification = (
                        Justification(step.justification)
                        if step.justification
                        else EmptyComponent()
                    )
                    steps.append(
                        StepDetails(
                            goal=step.goal,
                            criteria=step.success_criteria,
                            status=step.status,
                            status_class=badge_cls,
                            justification=justification,
                        )
                    )
                steps_comp = (
                    ComponentGroup(steps) if steps else EmptyComponent()
                )

                error_comp = (
                    ErrorDisplay(error=r.error) if r.error else EmptyComponent()
                )

                runs.append(
                    SimRunDetail(
                        failed_str=failed_str,
                        run_num=r.run_number,
                        run_status_class=run_cls,
                        run_status="PASS" if r.passed else "FAIL",
                        goals=str(r.goals),
                        expectations=str(r.expectations),
                        turns=str(r.turns),
                        duration=dur_comp,
                        session_link=session_link,
                        session_params=session_params,
                        error=error_comp,
                        steps=steps_comp,
                        expectation_details=ComponentGroup(list(expectations)),
                        trace=trace_comp,
                    )
                )

            cards.append(
                SimCard(
                    eval_name_id=urllib.parse.quote(name.replace("'", "'")),
                    passed_str=passed_str,
                    bg_class=cls,
                    eval_name=name,
                    score=score,
                    run_details=ComponentGroup(runs),
                )
            )

        header = SectionHeader(
            "section-sims",
            "Simulations",
            self.stats.sim.passed,
            self.stats.sim.total,
            self.stats.sim.pct_str,
        )
        return ComponentGroup([header] + cards).render()


class ToolCard(Component):
    """Tool evaluation outcome scorecard table component."""

    def __init__(
        self, tool_results: list[ToolRunResult], stats: EvaluationStats
    ):
        self.tool_results = tool_results
        self.stats = stats

    def get_summary_card(self) -> SummaryCard:
        """Compile its own SummaryCard widget dynamically."""
        return SummaryCard(
            section_id="section-tools",
            label="Tool Tests",
            value_class=self.stats.tool.value_class,
            passed=self.stats.tool.passed,
            total=self.stats.tool.total,
        )

    def render(self) -> str:
        if not self.tool_results:
            return ""

        rows = (
            ToolRow(
                passed=r.passed,
                status_class="pass" if r.passed else "fail",
                status=r.status,
                tool_name=r.tool,
                test_name=r.name,
                latency_ms=r.latency_ms,
                errors=r.errors[:100],
            )
            for r in sorted(self.tool_results, key=lambda x: x.passed)
        )

        return self.substitute(
            PASSED=self.stats.tool.passed,
            TOTAL=self.stats.tool.total,
            PCT=self.stats.tool.pct_str,
            TOOL_ROWS=ComponentGroup(list(rows)),
        )


class ToolRow(Component):
    """Single Tool execution outcome row component."""

    def __init__(
        self,
        passed: bool,
        status_class: str,
        status: str,
        tool_name: str,
        test_name: str,
        latency_ms: float,
        errors: str,
    ):
        self.passed = passed
        self.status_class = status_class
        self.status = status
        self.tool_name = tool_name
        self.test_name = test_name
        self.latency_ms = latency_ms
        self.errors = errors

    def render(self) -> str:
        lat = f"{self.latency_ms:.0f}ms" if self.latency_ms else "-"
        err_str = self.errors[:100] if self.errors else ""
        passed_str = "true" if self.passed else "false"
        return self.substitute(
            PASSED_STR=passed_str,
            STATUS_CLASS=escape(self.status_class),
            STATUS=escape(self.status),
            TOOL_NAME=escape(self.tool_name),
            TEST_NAME=escape(self.test_name),
            LATENCY=escape(lat),
            ERRORS=escape(err_str),
        )


class CallbackCard(Component):
    """Callback evaluation outcome scorecard table component."""

    def __init__(
        self, callback_results: list[CallbackRunResult], stats: EvaluationStats
    ):
        self.callback_results = callback_results
        self.stats = stats

    def get_summary_card(self) -> SummaryCard:
        """Compile its own SummaryCard widget dynamically."""
        return SummaryCard(
            section_id="section-callbacks",
            label="Callback Tests",
            value_class=self.stats.callback.value_class,
            passed=self.stats.callback.passed,
            total=self.stats.callback.total,
        )

    def render(self) -> str:
        if not self.callback_results:
            return ""

        rows = (
            CallbackRow(
                passed=r.passed,
                status_class="pass" if r.passed else "fail",
                status=r.status,
                agent_name=r.agent,
                callback_type=r.callback_type,
                test_name=r.name,
                error=r.error[:100],
            )
            for r in sorted(self.callback_results, key=lambda x: x.passed)
        )

        return self.substitute(
            PASSED=self.stats.callback.passed,
            TOTAL=self.stats.callback.total,
            PCT=self.stats.callback.pct_str,
            CALLBACK_ROWS=ComponentGroup(list(rows)),
        )


class CallbackRow(Component):
    """Single Callback execution outcome row component."""

    def __init__(
        self,
        passed: bool,
        status_class: str,
        status: str,
        agent_name: str,
        callback_type: str,
        test_name: str,
        error: str,
    ):
        self.passed = passed
        self.status_class = status_class
        self.status = status
        self.agent_name = agent_name
        self.callback_type = callback_type
        self.test_name = test_name
        self.error = error

    def render(self) -> str:
        err_str = self.error[:100] if self.error else ""
        passed_str = "true" if self.passed else "false"
        return self.substitute(
            PASSED_STR=passed_str,
            STATUS_CLASS=escape(self.status_class),
            STATUS=escape(self.status),
            AGENT_NAME=escape(self.agent_name),
            CALLBACK_TYPE=escape(self.callback_type),
            TEST_NAME=escape(self.test_name),
            ERROR=escape(err_str),
        )


class ExpectationOutcome(Component):
    """Collapsible outcome details indicating Met/Not Met status component."""

    template = "subcomponents/expectation.html"

    def __init__(
        self, label: str, status_class: str, status: str, details: str
    ):
        self.label = label
        self.status_class = status_class
        self.status = status
        self.details = details

    def render(self) -> str:
        return self.substitute(
            EXPECTATION=escape(self.label),
            STATUS_CLASS=escape(self.status_class),
            STATUS=escape(self.status),
            JUSTIFICATION_HTML=escape(self.details),
        )


class ErrorDisplay(Component):
    """Component to display simulation run execution error trace."""

    template = "subcomponents/error_display.html"

    def __init__(self, error: str):
        self.error = error

    def render(self) -> str:
        return self.substitute(ERROR=self.error)


class FailurePatterns(Component):
    """Failure Patterns consolidated groupings layout component."""

    def __init__(self, failure_groups: dict[str, set[tuple[str, str]]]):
        self.failure_groups = failure_groups

    def render(self) -> str:
        if not self.failure_groups:
            return ""
        rows = []
        sorted_groups = sorted(
            self.failure_groups.items(),
            key=lambda item: len(item[1]),
            reverse=True,
        )
        for reason, instances in sorted_groups:
            links = (
                AffectedItem(
                    type_str=inst[0].upper(),
                    safe_name=urllib.parse.quote(inst[1]),
                    eval_name=inst[1],
                )
                for inst in sorted(instances, key=lambda x: x[1])
            )
            rows.append(
                FailureGroup(
                    reason=reason,
                    affected_count=len(instances),
                    affected_items=ComponentGroup(list(links)),
                )
            )

        return self.substitute(FAILURE_GROUPS=ComponentGroup(rows))


class FailureGroup(Component):
    """Single failure reason block container component."""

    def __init__(
        self,
        reason: str,
        affected_count: int,
        affected_items: Component,
    ):
        self.reason = reason
        self.affected_count = affected_count
        self.affected_items = affected_items

    def render(self) -> str:
        return self.substitute(
            REASON=escape(self.reason),
            AFFECTED_COUNT=str(self.affected_count),
            AFFECTED_ITEMS=self.affected_items,
        )


class AffectedItem(Component):
    """Clickable visual badge link representing a single failure eval test component."""

    def __init__(self, type_str: str, safe_name: str, eval_name: str):
        self.type_str = type_str
        self.safe_name = safe_name
        self.eval_name = eval_name

    def render(self) -> str:
        badge_cls = "golden" if self.type_str == "GOLD" else "sim"
        type_lbl = "GOLDEN" if self.type_str == "GOLD" else "SIM"
        return self.substitute(
            BADGE_CLASS=badge_cls,
            TYPE=type_lbl,
            SAFE_NAME=self.safe_name,
            EVAL_NAME=escape(self.eval_name),
        )


class Report(Component):
    """The visual document envelope root component."""

    def __init__(self, title: str, body: Component):
        self.title = title
        self.body = body

    def render(self) -> str:
        shell = BaseShell(title=self.title, body_content=self.body.children)
        return shell.render()