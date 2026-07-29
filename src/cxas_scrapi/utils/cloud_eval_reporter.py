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

"""In-product Cloud CES / CXAS evaluation diagnostic reporter.

Parses 100% of evaluation schema fields across six diagnostic dimensions,
suppresses tool-order false alarms on passing invocations, audits live cloud
project architecture over REST API, and formats interactive Tailwind HTML
dashboards or structured JSON telemetry for AI coding assistants.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime
from typing import Any

from cxas_scrapi.utils.eval_utils import EvalUtils

logger = logging.getLogger(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CXAS Evaluation Report</title>
    <!-- Tailwind CSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .collapsible-content {{ display: none; }}
        .expanded .collapsible-content {{ display: block; }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        .dimension-drawer {{
            max-height: 0;
            opacity: 0;
            overflow: hidden;
            transition: all 0.4s ease-in-out;
            margin-top: 0;
        }}
        .dimension-drawer.expanded-drawer {{
            max-height: 20000px;
            opacity: 1;
            margin-top: 0;
        }}
    </style>
    <script>
        function toggleCollapse(id) {{
            document.getElementById(id).classList.toggle('expanded');
        }}
        function toggleDimension(dimId) {{
            const drawer = document.getElementById('drawer-' + dimId);
            const card = document.getElementById('card-' + dimId);
            if (!drawer) return;
            const isExpanded = drawer.classList.contains('expanded-drawer');

            document.querySelectorAll('.dimension-drawer').forEach(el => {{
                el.classList.remove('expanded-drawer');
            }});
            document.querySelectorAll('.dim-card').forEach(el => {{
                el.classList.remove('ring-2', 'ring-indigo-500');
            }});

            if (!isExpanded) {{
                drawer.classList.add('expanded-drawer');
                if (card) card.classList.add('ring-2', 'ring-indigo-500');
            }}
        }}
        function showTab(tabId) {{
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');

            document.querySelectorAll('.tab-btn').forEach(el => {{
                el.classList.remove('border-indigo-500', 'text-indigo-600');
                el.classList.add('border-transparent', 'text-gray-500');
            }});
            const btn = document.getElementById('btn-' + tabId);
            if(btn) {{
                btn.classList.remove('border-transparent', 'text-gray-500');
                btn.classList.add('border-indigo-500', 'text-indigo-600');
            }}
        }}
    </script>
</head>
<body class="bg-gray-50 text-gray-800 font-sans p-8">
    <div class="max-w-6xl mx-auto">
        <h1 class="text-4xl font-bold mb-2 text-indigo-700">CXAS Evaluation Report</h1>
        <p class="text-gray-600 mb-8">Generated on: {timestamp}</p>

        <!-- Tabs Navigation -->
        {tab_btns_html}

        <!-- TAB: SUMMARY -->
        <div id="tab-summary" class="tab-content active">
            <!-- Summary Cards -->
            <div class="grid grid-cols-3 gap-4 mb-6">
                <div class="bg-white p-4 rounded-lg shadow-sm border border-gray-200 border-l-4 border-l-blue-500 flex items-center justify-between">
                    <span class="text-xs font-semibold text-gray-500 uppercase">Total Tests</span>
                    <span class="text-2xl font-bold text-gray-800">{total}</span>
                </div>
                <div class="bg-white p-4 rounded-lg shadow-sm border border-gray-200 border-l-4 border-l-green-500 flex items-center justify-between">
                    <span class="text-xs font-semibold text-gray-500 uppercase">Passed</span>
                    <span class="text-2xl font-bold text-green-600">{passed}</span>
                </div>
                <div class="bg-white p-4 rounded-lg shadow-sm border border-gray-200 border-l-4 border-l-red-500 flex items-center justify-between">
                    <span class="text-xs font-semibold text-gray-500 uppercase">Failed</span>
                    <span class="text-2xl font-bold text-red-600">{failed}</span>
                </div>
            </div>

            <!-- Performance & Telemetry Executive Dashboard -->
            {performance_summary_html}

            <div class="bg-white p-6 rounded-lg shadow-md mb-8">
                <h2 class="text-xl font-bold text-gray-800 mb-4">Diagnostic Dimensions Overview</h2>
                <div class="space-y-4">
                    {overview_cards_html}
                </div>
            </div>
        </div>

        <!-- TAB: TOOL CALLS -->
        <div id="tab-tool-calls" class="tab-content">
            <h2 class="text-2xl font-bold text-red-700 mb-4">Tool Calls ({count_tool_calls})</h2>
            {tool_cards_html}
        </div>

        <!-- TAB: STATE & VARIABLES -->
        <div id="tab-variables" class="tab-content">
            <h2 class="text-2xl font-bold text-yellow-700 mb-4">State & Variables ({count_variables})</h2>
            {variable_cards_html}
        </div>

        <!-- TAB: GENERATIVE & PHRASING -->
        <div id="tab-semantic" class="tab-content">
            <h2 class="text-2xl font-bold text-purple-700 mb-4">Generative & Phrasing ({count_semantic})</h2>
            {semantic_cards_html}
        </div>

        <!-- TAB: AGENT HANDOVERS -->
        <div id="tab-handovers" class="tab-content">
            <h2 class="text-2xl font-bold text-indigo-700 mb-4">Agent Handovers ({count_handovers})</h2>
            {handover_cards_html}
        </div>

        <!-- TAB: SYSTEM & INFRASTRUCTURE -->
        <div id="tab-system" class="tab-content">
            <h2 class="text-2xl font-bold text-gray-700 mb-4">System & Infrastructure ({count_system})</h2>
            {system_cards_html}
        </div>

        <!-- TAB: PROJECT LINTER AUDIT -->
        <div id="tab-linter" class="tab-content">
            <h2 class="text-2xl font-bold text-indigo-700 mb-4">Project Architecture & Prompt Linter Audit</h2>
            <div class="bg-white p-6 rounded-lg shadow-md mb-8">
                <h3 class="text-lg font-bold text-gray-800 mb-2">Live Cloud Application Agents Audit</h3>
                <p class="text-sm text-gray-600 mb-4">Checked via live REST API query across all active application agents.</p>
                {cloud_linter_html}
            </div>
            {static_linter_html}
        </div>
    </div>
</body>
</html>
"""

ISSUE_CARD_TEMPLATE = """
<div class="bg-white rounded-lg shadow-md mb-4 overflow-hidden border border-gray-200">
    <div class="p-4 bg-gray-50 flex justify-between items-center cursor-pointer" onclick="toggleCollapse('{test_id}')">
        <div>
            <h3 class="text-base font-bold text-gray-900">{test_name}</h3>
            <div class="mt-1 flex items-center gap-2">
                <span class="inline-block px-2 py-1 text-xs font-semibold rounded {badge_color}">{status}</span>
                {failure_summary_pill}
            </div>
            {scores_badge_bar}
        </div>
        <div class="flex items-center space-x-4">
            <a href="{eval_url}" target="_blank" class="text-sm text-indigo-600 hover:underline font-medium" onclick="event.stopPropagation();">Inspect in Console &rarr;</a>
            <span class="text-gray-400 font-bold">&darr;</span>
        </div>
    </div>
    <div id="{test_id}" class="collapsible-content p-6 border-t border-gray-100">
        <h4 class="text-sm font-bold text-gray-700 uppercase mb-3">Failure Traces & Diagnostic Evidence:</h4>
        <ul class="space-y-2">
            {issues_list}
        </ul>
    </div>
</div>
"""

SCORE_BADGES_TEMPLATE = """
<div class="mt-2 flex flex-wrap gap-2 text-xs">
    {badges}
</div>
"""

SCORE_BADGE_ITEM = """
<span class="inline-block px-2 py-0.5 rounded border {border_color} font-mono">{label}: {value}</span>
"""


def build_score_badges(telemetry: dict[str, Any]) -> str:
    """Renders score badge bar for issue cards."""
    if not telemetry:
        return ""
    badges = []
    sem_score = telemetry.get("semanticSimilarityScore")
    if sem_score is not None:
        color = (
            "border-green-300 bg-green-50 text-green-800"
            if sem_score >= 3
            else "border-red-300 bg-red-50 text-red-800"
        )
        badges.append(
            SCORE_BADGE_ITEM.format(
                border_color=color, label="Semantic Consistency", value=f"{sem_score}/4"
            )
        )

    param_score = telemetry.get("parameterCorrectnessScore")
    if param_score is not None:
        color = (
            "border-green-300 bg-green-50 text-green-800"
            if param_score == 1.0
            else "border-red-300 bg-red-50 text-red-800"
        )
        val_str = f"{int(param_score * 100)}%"
        badges.append(
            SCORE_BADGE_ITEM.format(
                border_color=color, label="Tool Param Accuracy", value=val_str
            )
        )

    order_score = telemetry.get("toolOrderedInvocationScore")
    if order_score is not None:
        color = (
            "border-green-300 bg-green-50 text-green-800"
            if order_score == 1.0
            else "border-yellow-300 bg-yellow-50 text-yellow-800"
        )
        val_str = f"{int(order_score * 100)}%"
        badges.append(
            SCORE_BADGE_ITEM.format(
                border_color=color, label="Tool Order Score", value=val_str
            )
        )

    if not badges:
        return ""
    return SCORE_BADGES_TEMPLATE.format(badges="".join(badges))


def categorize_cloud_errors(errors: list[str]) -> dict[str, list[str]]:
    """Groups error strings into six execution and architectural categories."""
    cats: dict[str, list[str]] = {
        "Tool Calls": [],
        "State & Variables": [],
        "Generative & Phrasing": [],
        "Agent Handovers": [],
        "System & Infrastructure": [],
    }

    for err in errors:
        err_lower = err.lower()
        if any(
            w in err_lower
            for w in (
                "tool",
                "function",
                "argument",
                "parameter",
                "schema",
                "missing required",
                "invalid arg",
            )
        ):
            cats["Tool Calls"].append(err)
        elif any(
            w in err_lower
            for w in (
                "variable",
                "session",
                "context",
                "state",
                "unresolved",
                "template",
                "binding",
                "slot",
                "config",
                "unreachable",
                "unfillable",
                "not in slots",
                "{",
            )
        ):
            cats["State & Variables"].append(err)
        elif any(
            w in err_lower
            for w in (
                "routing",
                "transfer",
                "handoff",
                "handover",
                "escalat",
                "target agent",
                "observedagenttransfer",
            )
        ):
            cats["Agent Handovers"].append(err)
        elif any(
            w in err_lower
            for w in (
                "quota",
                "503",
                "500",
                "unavailable",
                "internal",
                "timeout",
                "runtime",
                "connection",
                "infrastructure",
            )
        ):
            cats["System & Infrastructure"].append(err)
        elif any(
            w in err_lower
            for w in (
                "semantic",
                "similarity",
                "hallucin",
                "phras",
                "goal",
                "satisfaction",
                "rubric",
                "expectat",
                "mismatch",
                "inconsistent",
                "consistency",
            )
        ):
            cats["Generative & Phrasing"].append(err)
        else:
            cats["Tool Calls"].append(err)

    for k in cats:
        cats[k] = list(set(cats[k]))
    return cats


def _get_eval_status(item: dict[str, Any]) -> str:
    raw_status = item.get("evaluation_status", item.get("evaluationStatus"))
    if isinstance(raw_status, int):
        status_map = {0: "UNSPECIFIED", 1: "PASS", 2: "FAIL"}
        return status_map.get(raw_status, f"UNKNOWN_{raw_status}")
    return str(raw_status).upper() if raw_status else "UNKNOWN"


def _extract_span_errors_for_turn(
    span: dict[str, Any] | None, turn_num: int
) -> list[str]:
    """Recursively walks turn spans to extract undeclared tool calls and runtime execution errors."""
    errs: list[str] = []
    if not span:
        return errs

    attrs = span.get("attributes", {})
    if "undeclared tool references" in attrs:
        refs = attrs.get("undeclared tool references")
        if isinstance(refs, list):
            for r in refs:
                errs.append(
                    f"[Undeclared Tool Call (Turn {turn_num})]: References to "
                    f"undeclared tools: {r}"
                )
        elif refs:
            errs.append(
                f"[Undeclared Tool Call (Turn {turn_num})]: References to "
                f"undeclared tools: {refs}"
            )

    if "error" in attrs and attrs.get("error"):
        err_msg = attrs.get("error")
        errs.append(f"[Tool Execution Error (Turn {turn_num})]: {err_msg}")

    for child in span.get("child_spans", []):
        errs.extend(_extract_span_errors_for_turn(child, turn_num))

    return list(dict.fromkeys(errs))


def _extract_log_errors_from_obj(
    obj: Any, turn_num: int, visited: set[int] | None = None
) -> list[str]:
    """Recursively traverses turn messages and variables to find state machine log errors and config_validation_failed tags."""
    if visited is None:
        visited = set()
    errs: list[str] = []
    if obj is None or id(obj) in visited:
        return errs
    visited.add(id(obj))

    if isinstance(obj, dict):
        tag = obj.get("tag")
        level = obj.get("level")
        data = obj.get("data", {})
        if tag == "config_validation_failed" or (
            level == "ERROR" and isinstance(data, dict) and "errors" in data
        ):
            err_list = data.get("errors", [])
            if isinstance(err_list, list):
                for e_msg in err_list:
                    errs.append(
                        f"[State Machine Config Error (Turn {turn_num})]: {e_msg}"
                    )
            elif err_list:
                errs.append(
                    f"[State Machine Config Error (Turn {turn_num})]: {err_list}"
                )

        for v in obj.values():
            if isinstance(v, (dict, list)):
                errs.extend(_extract_log_errors_from_obj(v, turn_num, visited))
            elif (
                isinstance(v, str)
                and (
                    "config_validation_failed" in v
                    or '"level": "ERROR"' in v
                    or "'level': 'ERROR'" in v
                )
                and ("{" in v and "}" in v)
            ):
                try:
                    parsed = json.loads(v)
                    errs.extend(
                        _extract_log_errors_from_obj(parsed, turn_num, visited)
                    )
                except Exception:
                    pass

    elif isinstance(obj, list):
        for item in obj:
            errs.extend(_extract_log_errors_from_obj(item, turn_num, visited))

    return list(dict.fromkeys(errs))


def parse_evaluation_schema_details(
    eval_data: dict[str, Any], conv_data: dict[str, Any] | None = None
) -> tuple[list[str], dict[str, Any]]:
    """Parses all evaluation fields according to Google Cloud CES v1beta schema."""
    findings: list[str] = []
    telemetry: dict[str, Any] = {
        "semanticSimilarityScore": None,
        "parameterCorrectnessScore": None,
        "toolOrderedInvocationScore": None,
        "agentTransfers": [],
        "turnLatencies": [],
        "toolCallLatencies": [],
        "tokenUsage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
        "ttftMs": [],
        "models": [],
        "guardrails": {"checked": 0, "triggered": 0},
    }

    raw_overall = (
        eval_data.get("overallToolInvocationResult")
        or eval_data.get("overall_tool_invocation_result")
        or {}
    )
    if "parameterCorrectnessScore" in raw_overall:
        telemetry["parameterCorrectnessScore"] = raw_overall.get(
            "parameterCorrectnessScore"
        )
    elif "parameter_correctness_score" in raw_overall:
        telemetry["parameterCorrectnessScore"] = raw_overall.get(
            "parameter_correctness_score"
        )

    if "toolOrderedInvocationScore" in raw_overall:
        telemetry["toolOrderedInvocationScore"] = raw_overall.get(
            "toolOrderedInvocationScore"
        )
    elif "tool_ordered_invocation_score" in raw_overall:
        telemetry["toolOrderedInvocationScore"] = raw_overall.get(
            "tool_ordered_invocation_score"
        )

    gr = eval_data.get("goldenResult") or eval_data.get("golden_result") or {}
    turn_results = (
        eval_data.get("turnReplayResults")
        or eval_data.get("turn_replay_results")
        or gr.get("turnReplayResults")
        or gr.get("turn_replay_results")
        or []
    )
    if not turn_results and (
        "scenarioResult" in eval_data or "scenario_result" in eval_data
    ):
        scen_res = (
            eval_data.get("scenarioResult")
            or eval_data.get("scenario_result")
            or {}
        )
        turn_results = (
            scen_res.get("stepResults") or scen_res.get("step_results") or []
        )

    for idx, turn in enumerate(turn_results):
        turn_num = idx + 1

        tl = str(turn.get("turn_latency", "0s")).rstrip("s")
        try:
            telemetry["turnLatencies"].append(
                {"turn": turn_num, "latencySeconds": round(float(tl), 4)}
            )
        except (ValueError, TypeError):
            pass

        for tc in turn.get("tool_call_latencies", []):
            tcl = str(tc.get("execution_latency", "0s")).rstrip("s")
            t_name = (
                tc.get("display_name")
                or tc.get("displayName")
                or "unknown_tool"
            )
            try:
                telemetry["toolCallLatencies"].append(
                    {
                        "turn": turn_num,
                        "toolName": t_name,
                        "latencySeconds": round(float(tcl), 4),
                    }
                )
            except (ValueError, TypeError):
                pass

        sem = (
            turn.get("semanticSimilarityResult")
            or turn.get("semantic_similarity_result")
            or {}
        )
        if sem:
            score = sem.get("score")
            consistency = sem.get("consistency", "CONSISTENCY_UNSPECIFIED")
            if score is not None:
                telemetry["semanticSimilarityScore"] = score
            if consistency in (
                "NOT_CONSISTENT",
                "PARTIALLY_CONSISTENT",
            ) or (isinstance(score, (int, float)) and score < 3):
                findings.append(
                    f"[Semantic Similarity (Turn {turn_num})]: Low consistency "
                    f"({consistency}, score: {score}/4)."
                )

        eo_list = (
            turn.get("expectationOutcome")
            or turn.get("expectation_outcome")
            or []
        )
        if isinstance(eo_list, dict):
            eo_list = [eo_list]
        for eo in eo_list:
            outcome = eo.get("outcome", 0)
            if outcome == 2 or str(outcome).upper() == "FAIL":
                exp = eo.get("expectation", {})
                inv_res = (
                    eo.get("tool_invocation_result")
                    or eo.get("toolInvocationResult")
                    or {}
                )
                explanation = inv_res.get("explanation", "")

                if "tool_call" in exp or "toolCall" in exp:
                    tc = exp.get("tool_call") or exp.get("toolCall") or {}
                    tool_name = (
                        tc.get("display_name")
                        or tc.get("displayName")
                        or tc.get("tool", "").split("/")[-1]
                        or "unknown_tool"
                    )
                    if explanation:
                        findings.append(
                            f"[Tool Call Failure (Turn {turn_num})]: Tool "
                            f"'{tool_name}' failed — {explanation}"
                        )
                    else:
                        findings.append(
                            f"[Missing Tool Call (Turn {turn_num})]: Expected "
                            f"tool call '{tool_name}' failed expectation "
                            f"(Outcome: FAIL)."
                        )

                if "agent_transfer" in exp or "agentTransfer" in exp:
                    at = (
                        exp.get("agent_transfer")
                        or exp.get("agentTransfer")
                        or {}
                    )
                    agent_name = (
                        at.get("display_name")
                        or at.get("displayName")
                        or at.get("target_agent", "").split("/")[-1]
                        or at.get("targetAgent", "").split("/")[-1]
                        or "unknown_agent"
                    )
                    if explanation:
                        findings.append(
                            f"[Routing/Transfer Failure (Turn {turn_num})]: "
                            f"Transfer to '{agent_name}' failed — {explanation}"
                        )
                    else:
                        findings.append(
                            f"[Routing/Transfer Failure (Turn {turn_num})]: "
                            f"Expected transfer to '{agent_name}' failed "
                            f"expectation (Outcome: FAIL)."
                        )

            obs_at = eo.get("observed_agent_transfer") or eo.get(
                "observedAgentTransfer"
            )
            if obs_at:
                t_agent = (
                    obs_at.get("display_name")
                    or obs_at.get("displayName")
                    or obs_at.get("target_agent", "").split("/")[-1]
                    or obs_at.get("targetAgent", "").split("/")[-1]
                    or "unknown"
                )
                if t_agent not in telemetry["agentTransfers"]:
                    telemetry["agentTransfers"].append(t_agent)
                if outcome == 2 or str(outcome).upper() == "FAIL":
                    findings.append(
                        f"[Routing/Transfer Failure (Turn {turn_num})]: Agent "
                        f"transfer to '{t_agent}' failed expectation "
                        f"(Outcome: FAIL)."
                    )

        overall_tool = (
            turn.get("overallToolInvocationResult")
            or turn.get("overall_tool_invocation_result")
            or {}
        )
        if "parameterCorrectnessScore" in overall_tool:
            telemetry["parameterCorrectnessScore"] = overall_tool.get(
                "parameterCorrectnessScore"
            )
        elif "parameter_correctness_score" in overall_tool:
            telemetry["parameterCorrectnessScore"] = overall_tool.get(
                "parameter_correctness_score"
            )

        if "toolOrderedInvocationScore" in overall_tool:
            telemetry["toolOrderedInvocationScore"] = overall_tool.get(
                "toolOrderedInvocationScore"
            )
        elif "tool_ordered_invocation_score" in overall_tool:
            telemetry["toolOrderedInvocationScore"] = overall_tool.get(
                "tool_ordered_invocation_score"
            )

        if overall_tool.get("outcome") == "FAIL":
            p_score = overall_tool.get(
                "parameterCorrectnessScore",
                overall_tool.get("parameter_correctness_score", 1.0),
            )
            o_score = overall_tool.get(
                "toolOrderedInvocationScore",
                overall_tool.get("tool_ordered_invocation_score", 1.0),
            )
            if p_score < 1.0:
                findings.append(
                    f"[Tool Param Correctness (Turn {turn_num})]: Parameter "
                    f"correctness dropped to {p_score}."
                )
            if o_score < 1.0:
                findings.append(
                    f"[Tool Order Failure (Turn {turn_num})]: Tool execution "
                    f"order differed from golden sequence ({o_score})."
                )

        if conv_data and len(conv_data.get("turns", [])) >= turn_num:
            conv_turn = conv_data["turns"][turn_num - 1]
            span_errs = _extract_span_errors_for_turn(
                conv_turn.get("root_span"), turn_num
            )
            for se in span_errs:
                if se not in findings:
                    findings.append(se)
            log_errs = _extract_log_errors_from_obj(conv_turn, turn_num)
            for le in log_errs:
                if le not in findings:
                    findings.append(le)

            def _collect_span_metrics(span: dict[str, Any] | None) -> None:
                if not span:
                    return
                attrs = span.get("attributes", {})
                if "input token count" in attrs:
                    try:
                        telemetry["tokenUsage"]["inputTokens"] += int(
                            float(attrs["input token count"])
                        )
                    except (ValueError, TypeError):
                        pass
                if "output token count" in attrs:
                    try:
                        telemetry["tokenUsage"]["outputTokens"] += int(
                            float(attrs["output token count"])
                        )
                    except (ValueError, TypeError):
                        pass
                if "time to first chunk (ms)" in attrs:
                    try:
                        telemetry["ttftMs"].append(
                            round(float(attrs["time to first chunk (ms)"]), 1)
                        )
                    except (ValueError, TypeError):
                        pass
                if "model" in attrs and attrs["model"]:
                    if attrs["model"] not in telemetry["models"]:
                        telemetry["models"].append(str(attrs["model"]))
                if (
                    attrs.get("type") == "LLM_PROMPT_SECURITY"
                    or "guardrail" in str(span.get("name", "")).lower()
                ):
                    telemetry["guardrails"]["checked"] += 1
                    if attrs.get("triggered") is True:
                        telemetry["guardrails"]["triggered"] += 1
                for child in span.get("child_spans", []):
                    _collect_span_metrics(child)

            _collect_span_metrics(conv_turn.get("root_span"))
            telemetry["tokenUsage"]["totalTokens"] = (
                telemetry["tokenUsage"]["inputTokens"]
                + telemetry["tokenUsage"]["outputTokens"]
            )

    if not findings and _get_eval_status(eval_data) == "FAIL":
        findings.append(
            "[Evaluation Failure]: Test case marked as FAIL by CES evaluation engine."
        )

    return findings, telemetry


def audit_cloud_project_linter(app_id: str, env: str = "prod") -> list[str]:
    """Queries live Cloud CES app via authenticated SDK and audits against 12 design rules."""
    issues: list[str] = []

    try:
        from cxas_scrapi.core.agents import Agents
        from cxas_scrapi.core.tools import Tools

        agents_client = Agents(app_name=app_id)
        agents = [type(a).to_dict(a) for a in agents_client.list_agents()]

        tools_client = Tools(app_name=app_id)
        tool_map = tools_client.get_tools_map(reverse=False)
        uuid_to_name = {k.split("/")[-1]: v for k, v in tool_map.items()}
    except Exception as e:
        return [
            f"[Linter API Warning]: Could not fetch live cloud agents for "
            f"linter audit ({e})."
        ]

    for agent in agents:
        name = (
            agent.get("display_name")
            or agent.get("displayName")
            or agent.get("name")
            or "unknown"
        ).split("/")[-1]
        instr_obj = agent.get("instruction", "")
        prompt = (
            instr_obj.get("text", "")
            if isinstance(instr_obj, dict)
            else str(instr_obj or "")
        )
        tools_attached = agent.get("tools", [])

        inactives = re.findall(r"`(\{@(?:TOOL|AGENT):[^}]+\})`", prompt)
        for i in inactives:
            issues.append(
                f"[{name}]: Inactive backticked pill reference found: `{i}` "
                f"(will not resolve dynamically)."
            )

        for t_ref in tools_attached:
            t_id = (
                t_ref.get("tool", "").split("/")[-1]
                if isinstance(t_ref, dict)
                else str(t_ref).split("/")[-1]
            )
            t_name = uuid_to_name.get(t_id, t_id)
            if (
                t_id not in ("set_variables", "end_session", "transfer_agent")
                and t_name not in ("set_variables", "end_session", "transfer_agent")
                and f"{{@TOOL: {t_name}}}" not in prompt
                and f"{{@TOOL:{t_name}}}" not in prompt
                and f"{{@TOOL: {t_id}}}" not in prompt
                and f"{{@TOOL:{t_id}}}" not in prompt
            ):
                issues.append(
                    f"[{name}]: Tool '{t_name}' is attached to agent but "
                    f"missing reference pill in system instruction."
                )

        for raw_ineq in re.findall(r"(?:^|\s)(<|>)\s*\d+", prompt):
            issues.append(
                f"[{name}]: Unescaped raw inequality symbol '{raw_ineq}' "
                f"in prompt text may cause XML/HTML tag truncation."
            )

    return issues


def _build_issues_html(issues: list[str]) -> str:
    """Helper to render bulleted list of issues in HTML cards."""
    if not issues:
        return '<li class="text-gray-500 italic">No specific errors logged.</li>'
    return "".join(
        f'<li class="text-sm font-mono text-gray-700 bg-gray-100 p-2 rounded '
        f'border-l-2 border-red-500">{i}</li>'
        for i in issues
    )


def _build_overview_card(
    dim_id: str,
    label: str,
    description: str,
    count: int,
    color_name: str,
    drawer_html: str,
) -> str:
    """Renders an accordion category header with its slide-out drawer directly underneath."""
    if count > 0:
        return f"""
            <div id="card-{dim_id}" class="dim-card rounded-lg border border-{color_name}-200 bg-white overflow-hidden shadow-sm transition-all duration-200">
                <div onclick="toggleDimension('{dim_id}')" class="p-4 bg-{color_name}-50 hover:bg-{color_name}-100 cursor-pointer flex justify-between items-center transition-colors select-none">
                    <div>
                        <h3 class="text-base font-bold text-{color_name}-900">{label}</h3>
                        <p class="text-xs text-{color_name}-700 mt-0.5">{description}</p>
                    </div>
                    <div class="flex items-center space-x-3">
                        <span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-{color_name}-200 text-{color_name}-900">{count} issues</span>
                        <span class="text-xs text-{color_name}-700 font-semibold underline">▼ slide out</span>
                    </div>
                </div>
                <div id="drawer-{dim_id}" class="dimension-drawer border-t border-{color_name}-200 bg-white">
                    <div class="p-5 space-y-4">
                        <h3 class="text-lg font-bold text-{color_name}-800 mb-3">{label} Failures ({count})</h3>
                        {drawer_html}
                    </div>
                </div>
            </div>
        """
    return f"""
        <div class="dim-card rounded-lg border border-gray-200 bg-white overflow-hidden shadow-sm opacity-60 select-none cursor-default">
            <div class="p-4 bg-gray-50 flex justify-between items-center">
                <div>
                    <h3 class="text-base font-bold text-gray-500">{label}</h3>
                    <p class="text-xs text-gray-400 mt-0.5">{description}</p>
                </div>
                <div class="flex items-center space-x-3">
                    <span class="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-bold bg-gray-200 text-gray-600">0 issues</span>
                </div>
            </div>
        </div>
    """


def compute_performance_summary(
    eval_results: list[dict[str, Any]],
    app_id: str,
    eval_names_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Computes aggregate latency, token economics, TTFT, and guardrail telemetry across the evaluation suite."""
    turn_latencies: list[tuple[float, str]] = []
    tool_latencies: list[tuple[float, str, str]] = []
    ttft_ms_list: list[float] = []
    total_input_tokens = 0.0
    total_output_tokens = 0.0
    guardrails_checked = 0
    guardrails_triggered = 0
    models_seen: set[str] = set()

    for idx, item in enumerate(eval_results):
        parent_eval_name = "/".join((item.get("name") or "").split("/")[:8])
        name = (
            (eval_names_map or {}).get(parent_eval_name)
            or (eval_names_map or {}).get(item.get("name", ""))
            or item.get("test_case_name")
            or item.get("displayName")
            or item.get("display_name")
            or item.get("name")
            or f"test_{idx}"
        )
        gr = item.get("goldenResult") or item.get("golden_result") or {}
        turns = (
            item.get("turnReplayResults")
            or item.get("turn_replay_results")
            or gr.get("turnReplayResults")
            or gr.get("turn_replay_results")
            or []
        )
        for t_idx, turn in enumerate(turns):
            tl = str(turn.get("turn_latency", "0s")).rstrip("s")
            try:
                turn_latencies.append(
                    (float(tl), f"Turn {t_idx+1} ({name[:30]})")
                )
            except (ValueError, TypeError):
                pass

            for tc in turn.get("tool_call_latencies", []):
                tcl = str(tc.get("execution_latency", "0s")).rstrip("s")
                t_name = (
                    tc.get("display_name")
                    or tc.get("displayName")
                    or "unknown_tool"
                )
                try:
                    tool_latencies.append((float(tcl), t_name, name[:30]))
                except (ValueError, TypeError):
                    pass

        conv_data = _fetch_conversation_if_needed(item, app_id)
        if conv_data:

            def _walk_spans(span: dict[str, Any] | None) -> None:
                nonlocal total_input_tokens, total_output_tokens, guardrails_checked, guardrails_triggered
                if not span:
                    return
                attrs = span.get("attributes", {})
                if "input token count" in attrs:
                    try:
                        total_input_tokens += float(attrs["input token count"])
                    except (ValueError, TypeError):
                        pass
                if "output token count" in attrs:
                    try:
                        total_output_tokens += float(
                            attrs["output token count"]
                        )
                    except (ValueError, TypeError):
                        pass
                if "time to first chunk (ms)" in attrs:
                    try:
                        ttft_ms_list.append(
                            float(attrs["time to first chunk (ms)"])
                        )
                    except (ValueError, TypeError):
                        pass
                if "model" in attrs and attrs["model"]:
                    models_seen.add(str(attrs["model"]))
                if (
                    attrs.get("type") == "LLM_PROMPT_SECURITY"
                    or "guardrail" in str(span.get("name", "")).lower()
                ):
                    guardrails_checked += 1
                    if attrs.get("triggered") is True:
                        guardrails_triggered += 1
                for child in span.get("child_spans", []):
                    _walk_spans(child)

            for t in conv_data.get("turns", []):
                _walk_spans(t.get("root_span"))

    avg_turn = (
        sum(x[0] for x in turn_latencies) / len(turn_latencies)
        if turn_latencies
        else 0.0
    )
    max_turn = (
        max(turn_latencies, key=lambda x: x[0])
        if turn_latencies
        else (0.0, "N/A")
    )

    avg_tool = (
        sum(x[0] for x in tool_latencies) / len(tool_latencies)
        if tool_latencies
        else 0.0
    )
    max_tool = (
        max(tool_latencies, key=lambda x: x[0])
        if tool_latencies
        else (0.0, "N/A", "N/A")
    )

    avg_ttft = (
        sum(ttft_ms_list) / len(ttft_ms_list) if ttft_ms_list else 0.0
    )
    max_ttft = max(ttft_ms_list) if ttft_ms_list else 0.0

    return {
        "avgTurnSeconds": round(avg_turn, 2),
        "maxTurnSeconds": round(max_turn[0], 2),
        "maxTurnTest": max_turn[1],
        "avgToolSeconds": round(avg_tool, 4),
        "maxToolSeconds": round(max_tool[0], 4),
        "maxToolName": max_tool[1],
        "totalTokens": int(total_input_tokens + total_output_tokens),
        "inputTokens": int(total_input_tokens),
        "outputTokens": int(total_output_tokens),
        "avgTtftMs": round(avg_ttft, 1),
        "maxTtftMs": round(max_ttft, 1),
        "models": sorted(list(models_seen)) or ["unknown"],
        "guardrailsChecked": guardrails_checked,
        "guardrailsTriggered": guardrails_triggered,
    }


def _build_performance_summary_html(perf: dict[str, Any]) -> str:
    """Renders 4-card grid for executive performance and token economics telemetry."""
    models_str = (
        ", ".join(perf["models"]) if perf["models"] else "unspecified"
    )
    in_k = (
        f"{perf['inputTokens']/1000:.1f}k"
        if perf["inputTokens"] >= 1000
        else str(perf["inputTokens"])
    )
    out_k = (
        f"{perf['outputTokens']/1000:.1f}k"
        if perf["outputTokens"] >= 1000
        else str(perf["outputTokens"])
    )

    return f"""
        <div class="bg-white p-6 rounded-lg shadow-md mb-8 border border-indigo-100">
            <h2 class="text-xl font-bold text-gray-800 mb-4">⚡ Agent Performance & Execution Telemetry</h2>
            <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div class="p-4 bg-blue-50 rounded-lg border border-blue-200">
                    <span class="block text-xs font-bold text-blue-700 uppercase tracking-wide">⏱️ Latency Benchmark</span>
                    <div class="mt-2 text-2xl font-extrabold text-blue-900">{perf['avgTurnSeconds']}s <span class="text-xs font-normal text-blue-600">avg turn</span></div>
                    <div class="mt-2 text-xs text-blue-800">
                        <p><strong>Max Turn:</strong> {perf['maxTurnSeconds']}s ({perf['maxTurnTest']})</p>
                        <p class="mt-1"><strong>Slowest Tool:</strong> {perf['maxToolName']} ({perf['maxToolSeconds']}s)</p>
                    </div>
                </div>
                <div class="p-4 bg-emerald-50 rounded-lg border border-emerald-200">
                    <span class="block text-xs font-bold text-emerald-700 uppercase tracking-wide">🪙 Token Economics</span>
                    <div class="mt-2 text-2xl font-extrabold text-emerald-900">{perf['totalTokens']:,} <span class="text-xs font-normal text-emerald-600">total</span></div>
                    <div class="mt-2 text-xs text-emerald-800">
                        <p><strong>Input / Output:</strong> {in_k} / {out_k}</p>
                        <p class="mt-1"><strong>Models:</strong> {models_str}</p>
                    </div>
                </div>
                <div class="p-4 bg-purple-50 rounded-lg border border-purple-200">
                    <span class="block text-xs font-bold text-purple-700 uppercase tracking-wide">🚀 Model Responsiveness</span>
                    <div class="mt-2 text-2xl font-extrabold text-purple-900">{perf['avgTtftMs']} ms <span class="text-xs font-normal text-purple-600">avg TTFT</span></div>
                    <div class="mt-2 text-xs text-purple-800">
                        <p><strong>Max TTFT:</strong> {perf['maxTtftMs']} ms</p>
                        <p class="mt-1"><strong>Avg Tool Latency:</strong> {perf['avgToolSeconds']*1000:.1f} ms</p>
                    </div>
                </div>
                <div class="p-4 bg-amber-50 rounded-lg border border-amber-200">
                    <span class="block text-xs font-bold text-amber-700 uppercase tracking-wide">🛡️ Safety Guardrails</span>
                    <div class="mt-2 text-2xl font-extrabold text-amber-900">{perf['guardrailsTriggered']} <span class="text-xs font-normal text-amber-600">triggered</span></div>
                    <div class="mt-2 text-xs text-amber-800">
                        <p><strong>Total Checked:</strong> {perf['guardrailsChecked']} checks</p>
                        <p class="mt-1"><strong>Pass Rate:</strong> {100 - int((perf['guardrailsTriggered']/max(1, perf['guardrailsChecked']))*100)}% safe</p>
                    </div>
                </div>
            </div>
        </div>
    """


def _fetch_conversation_if_needed(
    item: dict[str, Any], app_id: str
) -> dict[str, Any] | None:
    """Fetches conversation trace from CES to inspect span-level errors and undeclared tool references."""
    gr = item.get("goldenResult") or item.get("golden_result") or {}
    turns = (
        gr.get("turnReplayResults")
        or gr.get("turn_replay_results")
        or item.get("turnReplayResults")
        or item.get("turn_replay_results")
        or []
    )
    if turns and isinstance(turns, list) and "conversation" in turns[0]:
        try:
            from cxas_scrapi.core.conversation_history import ConversationHistory

            ch = ConversationHistory(app_name=app_id)
            conv_obj = ch.get_conversation(turns[0]["conversation"])
            return type(conv_obj).to_dict(conv_obj)
        except Exception:
            return None
    return None


def generate_cloud_html_report(
    eval_results: list[dict[str, Any]],
    cloud_linter_issues: list[str],
    app_id: str,
    env: str = "prod",
    linter_output: str = "",
    eval_names_map: dict[str, str] | None = None,
) -> str:
    """Generates complete single-file interactive Tailwind HTML dashboard."""
    total = len(eval_results)
    passed = sum(1 for r in eval_results if _get_eval_status(r) == "PASS")
    failed = total - passed

    cards: dict[str, list[str]] = {
        "Tool Calls": [],
        "State & Variables": [],
        "Generative & Phrasing": [],
        "Agent Handovers": [],
        "System & Infrastructure": [],
    }

    console_base = (
        "https://ces-console-dev.corp.google.com"
        if env == "dev"
        else "https://ces.cloud.google.com"
    )
    path_parts = app_id.split("/")
    project = path_parts[1] if len(path_parts) > 1 else "unknown"
    location = path_parts[3] if len(path_parts) > 3 else "unknown"
    app_name = path_parts[5] if len(path_parts) > 5 else "unknown"

    for idx, item in enumerate(eval_results):
        status = _get_eval_status(item)
        parent_eval_name = "/".join((item.get("name") or "").split("/")[:8])
        name = (
            (eval_names_map or {}).get(parent_eval_name)
            or (eval_names_map or {}).get(item.get("name", ""))
            or item.get("test_case_name")
            or item.get("displayName")
            or item.get("display_name")
            or item.get("name")
            or f"test_{idx}"
        )
        conv_data = _fetch_conversation_if_needed(item, app_id)
        findings, telemetry = parse_evaluation_schema_details(item, conv_data)
        cats = categorize_cloud_errors(findings)

        badge_color = (
            "bg-green-100 text-green-800"
            if status == "PASS"
            else "bg-red-100 text-red-800"
        )
        test_id = f"test_card_{idx}"
        parts = (item.get("name") or "").split("/")
        eval_uuid = ""
        result_uuid = ""
        if "evaluations" in parts and "results" in parts:
            eval_idx = parts.index("evaluations")
            eval_uuid = parts[eval_idx + 1] if len(parts) > eval_idx + 1 else ""
            res_idx = parts.index("results")
            result_uuid = parts[res_idx + 1] if len(parts) > res_idx + 1 else ""
        if not eval_uuid:
            eval_uuid = parts[-3] if len(parts) >= 3 else f"eval_{idx}"
        if not result_uuid:
            result_uuid = parts[-1] if len(parts) >= 1 else f"res_{idx}"

        eval_url = (
            f"{console_base}/projects/{project}/locations/{location}/"
            f"apps/{app_name}/evaluate/goldens/{eval_uuid}/results/{result_uuid}"
        )

        for cat_name, issue_list in cats.items():
            if issue_list:
                summary_text = issue_list[0] if issue_list else ""
                failure_pill_html = ""
                if summary_text:
                    clean_text = re.sub(r"^\[.*?\]:\s*", "", summary_text)
                    turn_match = re.search(r"\(Turn (\d+)\)", summary_text)
                    turn_prefix = (
                        f"Turn {turn_match.group(1)}: " if turn_match else ""
                    )
                    failure_pill_html = (
                        f'<span class="inline-block px-2 py-1 text-xs font-semibold '
                        f'text-red-800 bg-red-50 rounded border border-red-200">'
                        f"{turn_prefix}{clean_text}</span>"
                    )

                card_html = ISSUE_CARD_TEMPLATE.format(
                    test_id=f"{test_id}_{cat_name.replace(' ', '_')}",
                    test_name=name,
                    status=status,
                    badge_color=badge_color,
                    failure_summary_pill=failure_pill_html,
                    scores_badge_bar=build_score_badges(telemetry),
                    eval_url=eval_url,
                    issues_list=_build_issues_html(issue_list),
                )
                cards[cat_name].append(card_html)

    linter_html = (
        _build_issues_html(cloud_linter_issues)
        if cloud_linter_issues
        else (
            '<p class="text-sm text-green-700 font-semibold">'
            'All live cloud application agents passed architectural lint rules!</p>'
        )
    )

    tab_btns = [
        '<button onclick="showTab(\'tab-summary\')" id="btn-tab-summary" '
        'class="tab-btn whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm '
        'border-indigo-500 text-indigo-600">Summary Dashboard</button>'
    ]
    for tab_id, cat_name in [
        ("tab-tool-calls", "Tool Calls"),
        ("tab-variables", "State & Variables"),
        ("tab-semantic", "Generative & Phrasing"),
        ("tab-handovers", "Agent Handovers"),
        ("tab-system", "System & Infrastructure"),
    ]:
        cnt = len(cards[cat_name])
        tab_btns.append(
            f'<button onclick="showTab(\'{tab_id}\')" id="btn-{tab_id}" '
            f'class="tab-btn whitespace-nowrap py-4 px-1 border-b-2 font-medium '
            f'text-sm border-transparent text-gray-500 hover:text-gray-700 '
            f'hover:border-gray-300">{cat_name} ({cnt})</button>'
        )
    tab_btns.append(
        '<button onclick="showTab(\'tab-linter\')" id="btn-tab-linter" '
        'class="tab-btn whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm '
        'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300">'
        f'Project Linter ({len(cloud_linter_issues)})</button>'
    )

    overview_cards_html = "".join(
        [
            _build_overview_card(
                "tool-calls",
                "Tool Calls",
                "Tool execution errors, missing parameters, and undeclared tool references.",
                len(cards["Tool Calls"]),
                "red",
                "".join(cards["Tool Calls"]),
            ),
            _build_overview_card(
                "variables",
                "State & Variables",
                "Session state, authentication status, and variable persistence.",
                len(cards["State & Variables"]),
                "yellow",
                "".join(cards["State & Variables"]),
            ),
            _build_overview_card(
                "semantic",
                "Generative & Phrasing",
                "Semantic similarity consistency and phrasing differences.",
                len(cards["Generative & Phrasing"]),
                "purple",
                "".join(cards["Generative & Phrasing"]),
            ),
            _build_overview_card(
                "handovers",
                "Agent Handovers",
                "Routing discrepancies and failed agent transfer expectations.",
                len(cards["Agent Handovers"]),
                "indigo",
                "".join(cards["Agent Handovers"]),
            ),
            _build_overview_card(
                "system",
                "System & Infrastructure",
                "Tool call timeouts, API errors, and runtime exceptions.",
                len(cards["System & Infrastructure"]),
                "gray",
                "".join(cards["System & Infrastructure"]),
            ),
        ]
    )

    static_linter_html = ""
    if linter_output and "No local app-dir provided" not in linter_output:
        static_linter_html = (
            f'<div class="bg-white p-6 rounded-lg shadow-md mt-6">'
            f'<h3 class="text-lg font-bold text-gray-800 mb-2">Local Bundle Static Linting</h3>'
            f'<pre class="bg-gray-900 text-gray-100 p-4 rounded text-xs overflow-x-auto">{linter_output}</pre>'
            f"</div>"
        )

    perf_summary = compute_performance_summary(
        eval_results, app_id, eval_names_map
    )
    performance_summary_html = _build_performance_summary_html(perf_summary)

    return HTML_TEMPLATE.format(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total=total,
        passed=passed,
        failed=failed,
        performance_summary_html=performance_summary_html,
        count_tool_calls=len(cards["Tool Calls"]),
        count_variables=len(cards["State & Variables"]),
        count_semantic=len(cards["Generative & Phrasing"]),
        count_handovers=len(cards["Agent Handovers"]),
        count_system=len(cards["System & Infrastructure"]),
        overview_cards_html=overview_cards_html,
        tab_btns_html=(
            '<div class="border-b border-gray-200 mb-6 flex overflow-x-auto"><nav '
            'class="-mb-px flex space-x-8 cursor-pointer">'
            + "".join(tab_btns)
            + "</nav></div>"
        ),
        tool_cards_html="".join(cards["Tool Calls"])
        or "<p>No Tool Call issues.</p>",
        variable_cards_html="".join(cards["State & Variables"])
        or "<p>No State & Variable issues.</p>",
        semantic_cards_html="".join(cards["Generative & Phrasing"])
        or "<p>No Generative & Phrasing issues.</p>",
        handover_cards_html="".join(cards["Agent Handovers"])
        or "<p>No Agent Handover issues.</p>",
        system_cards_html="".join(cards["System & Infrastructure"])
        or "<p>No System & Infrastructure issues.</p>",
        cloud_linter_html=linter_html,
        static_linter_html=static_linter_html,
    )


def generate_cloud_json_report(
    eval_results: list[dict[str, Any]],
    cloud_linter_issues: list[str],
    app_id: str,
    linter_output: str = "",
    eval_names_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Generates structured JSON payload for automated AI self-healing loops."""
    total = len(eval_results)
    passed = sum(1 for r in eval_results if _get_eval_status(r) == "PASS")

    cat_issues: dict[str, list[dict[str, Any]]] = {
        "Tool Calls": [],
        "State & Variables": [],
        "Generative & Phrasing": [],
        "Agent Handovers": [],
        "System & Infrastructure": [],
    }
    telemetry_records: list[dict[str, Any]] = []

    for idx, item in enumerate(eval_results):
        parent_eval_name = "/".join((item.get("name") or "").split("/")[:8])
        name = (
            (eval_names_map or {}).get(parent_eval_name)
            or (eval_names_map or {}).get(item.get("name", ""))
            or item.get("test_case_name")
            or item.get("displayName")
            or item.get("display_name")
            or item.get("name")
            or f"test_{idx}"
        )
        conv_data = _fetch_conversation_if_needed(item, app_id)
        findings, telemetry = parse_evaluation_schema_details(item, conv_data)
        cats = categorize_cloud_errors(findings)

        record = {
            "testName": name,
            "evaluationStatus": _get_eval_status(item),
            "findings": findings,
            "telemetry": telemetry,
        }
        telemetry_records.append(record)

        for cat_name, issue_list in cats.items():
            if issue_list:
                cat_issues[cat_name].append(
                    {"testName": name, "issues": issue_list}
                )

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "schemaVersion": "ces.v1beta.evaluation.proto",
        "appId": app_id,
        "performanceTelemetry": compute_performance_summary(
            eval_results, app_id, eval_names_map
        ),
        "categorizedIssues": cat_issues,
        "detailedTelemetry": telemetry_records,
        "projectLinterAudit": {
            "totalIssues": len(cloud_linter_issues),
            "issues": cloud_linter_issues,
            "passed": len(cloud_linter_issues) == 0,
        },
        "linterAnalysis": linter_output if linter_output else None,
    }


def generate_cloud_report(
    eval_results: list[dict[str, Any]],
    app_id: str,
    output_path: str,
    report_format: str = "html",
    env: str = "prod",
    app_dir: str = "",
    eval_names_map: dict[str, str] | None = None,
) -> str:
    """Unified entry point for in-product Cloud eval report generation."""
    cloud_linter_issues = audit_cloud_project_linter(app_id, env=env)
    linter_output = ""

    if app_dir and os.path.exists(app_dir):
        scrapi_bin = shutil.which("cxas") or shutil.which("scrapi_cli.py")
        if scrapi_bin:
            try:
                res = subprocess.run(
                    [scrapi_bin, "lint", app_dir],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                linter_output = res.stdout + "\n" + res.stderr
            except Exception as e:
                linter_output = f"Error executing lint command: {e}"

    if report_format == "json":
        payload = generate_cloud_json_report(
            eval_results=eval_results,
            cloud_linter_issues=cloud_linter_issues,
            app_id=app_id,
            linter_output=linter_output,
            eval_names_map=eval_names_map,
        )
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return output_path
    else:
        html_doc = generate_cloud_html_report(
            eval_results=eval_results,
            cloud_linter_issues=cloud_linter_issues,
            app_id=app_id,
            env=env,
            linter_output=linter_output,
            eval_names_map=eval_names_map,
        )
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_doc)
        return output_path
