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


"""Eval lint rules (E001-E013).

Validates golden, scenario, shadow, and simulation/milestone YAML files.
"""

import difflib
import re
import typing
from pathlib import Path

import yaml

from cxas_scrapi.utils.linter import (
    LintContext,
    LintResult,
    Rule,
    Severity,
    rule,
)


def _is_golden(file_path: Path) -> bool:
    return "goldens" in file_path.parts


def _is_simulation(file_path: Path) -> bool:
    return "simulations" in file_path.parts


def _is_tool_test(file_path: Path) -> bool:
    return "tool_tests" in file_path.stem or "tool_tests" in file_path.parts


def _parse_yaml(content: str) -> dict | None:
    """Parse YAML, returning None on error or empty content."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError:
        return None
    return data if data else None


def _iter_golden_turns(data: dict) -> typing.Any:
    """Yield (conv_name, turn_index, turn_dict) for golden eval turns."""
    for conv in data.get("conversations", []):
        conv_name = conv.get("conversation", "")
        for i, turn in enumerate(conv.get("turns", [])):
            if isinstance(turn, dict):
                yield conv_name, i + 1, turn


@rule("evals")
class InvalidYaml(Rule):
    id = "E001"
    name = "eval-yaml-parse"
    description = "Eval file must be valid YAML"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as e:
            return [self.make_result(file=rel, message=f"Invalid YAML: {e}")]
        return []


@rule("evals")
class MissingConversations(Rule):
    id = "E002"
    name = "eval-structure"
    description = "Golden eval must have 'conversations' key"
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_golden(file_path):
            return []
        rel = str(file_path.relative_to(context.project_root))
        data = _parse_yaml(content)
        if data is None:
            return [
                self.make_result(
                    file=rel, message="Eval file is empty or invalid"
                )
            ]
        if "conversations" not in data:
            return [
                self.make_result(
                    file=rel,
                    message="Missing 'conversations' key in golden eval YAML",
                )
            ]
        return []


@rule("evals")
class InvalidToolCall(Rule):
    id = "E003"
    name = "eval-tool-exists"
    description = "Tool calls in evals must reference existing tools"
    default_severity = Severity.WARNING

    SPECIAL_ACTIONS = {"transfer_to_agent", "end_session"}

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_golden(file_path):
            return []

        data = _parse_yaml(content)
        if not data:
            return []

        conversations = (
            data if isinstance(data, list) else data.get("conversations", [])
        )
        valid_tools = context.all_known_tools | self.SPECIAL_ACTIONS
        rel = str(file_path.relative_to(context.project_root))

        results = []
        for conv_name, turn_num, turn in _iter_golden_turns(
            {"conversations": conversations}
        ):
            for tc in turn.get("tool_calls", []):
                action = tc.get("action", "")
                if action and action not in valid_tools:
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"Conv '{conv_name}'"
                                f" turn {turn_num}:"
                                f" tool_call '{action}'"
                                " not found in local"
                                " app tools"
                            ),
                            fix=(
                                "Available local tools:"
                                f" {', '.join(sorted(context.all_known_tools))}"
                            ),
                        )
                    )
        return results


@rule("evals")
class UndeclaredSessionParam(Rule):
    id = "E004"
    name = "eval-session-param"
    description = "Session parameters should reference known variables"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        return []


@rule("evals")
class DuplicateYamlKeys(Rule):
    id = "E005"
    name = "eval-duplicate-keys"
    description = (
        "Duplicate YAML keys in same mapping (second overwrites first)"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        rel = str(file_path.relative_to(context.project_root))
        results = []
        prev_key = ""
        prev_indent = -1
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            key_match = re.match(r"^(\w+):", stripped)
            if key_match:
                key = key_match.group(1)
                if (
                    key == prev_key
                    and indent == prev_indent
                    and key == "tool_calls"
                ):
                    results.append(
                        self.make_result(
                            file=rel,
                            line=i,
                            message=(
                                f"Duplicate '{key}:'"
                                " key at same level"
                                " — second overwrites"
                                " first"
                            ),
                            fix="Combine into a single tool_calls: list",
                        )
                    )
                prev_key = key
                prev_indent = indent
            elif (
                stripped
                and not stripped.startswith("-")
                and not stripped.startswith("#")
            ):
                prev_key = ""
        return results


@rule("evals")
class GoldenWithoutMocks(Rule):
    id = "E006"
    name = "eval-no-mocks"
    description = "Golden eval with tool_calls but no session_parameters"
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_golden(file_path):
            return []
        data = _parse_yaml(content)
        if not data:
            return []

        has_common_params = bool(data.get("common_session_parameters"))
        has_tool_calls = any(
            turn.get("tool_calls")
            for _conv, _i, turn in _iter_golden_turns(data)
        )

        if has_tool_calls and not has_common_params:
            rel = str(file_path.relative_to(context.project_root))
            return [
                self.make_result(
                    file=rel,
                    message=(
                        "Golden eval has tool_calls"
                        " but no"
                        " common_session_parameters"
                    ),
                    fix="Add session parameters for reliable tool responses",
                )
            ]
        return []


@rule("evals")
class GoldenAgentFieldNotString(Rule):
    id = "E007"
    name = "eval-agent-not-string"
    description = (
        "Golden agent response must be a string or list of strings, not a dict"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_golden(file_path):
            return []
        data = _parse_yaml(content)
        if not data:
            return []

        rel = str(file_path.relative_to(context.project_root))
        results = []
        for conv, i, turn in _iter_golden_turns(data):
            agent = turn.get("agent")
            if agent is None:
                continue
            if isinstance(agent, str):
                continue
            if isinstance(agent, list) and all(
                isinstance(item, str) for item in agent
            ):
                continue
            results.append(
                self.make_result(
                    file=rel,
                    message=f"Conv '{conv}' turn {i}: 'agent' field is a "
                    f"{type(agent).__name__}, must be a plain string"
                    " or a list of strings",
                    fix=(
                        "Replace with a plain string or a list"
                        " of strings containing the expected"
                        " agent response text"
                    ),
                )
            )
        return results


@rule("evals")
class GoldenMissingAgentField(Rule):
    id = "E008"
    name = "eval-missing-agent"
    description = (
        "Golden turn missing 'agent' field"
        " — causes automatic FAIL from"
        " unexpected response"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_golden(file_path):
            return []
        data = _parse_yaml(content)
        if not data:
            return []

        rel = str(file_path.relative_to(context.project_root))
        return [
            self.make_result(
                file=rel,
                message=(
                    f"Conv '{conv}' turn {i}:"
                    " has 'user' but no"
                    " 'agent' field — any"
                    " agent response will be"
                    " flagged as UNEXPECTED"
                    " RESPONSE"
                ),
                fix=("Add an 'agent' field with the expected response text"),
            )
            for conv, i, turn in _iter_golden_turns(data)
            if "user" in turn and "agent" not in turn
        ]


@rule("evals")
class SimMissingTags(Rule):
    id = "E009"
    name = "eval-sim-missing-tags"
    description = (
        "Simulation eval missing 'tags' field — won't match --priority filters"
    )
    default_severity = Severity.WARNING

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_simulation(file_path):
            return []
        data = _parse_yaml(content)
        if not data:
            return []

        rel = str(file_path.relative_to(context.project_root))
        if isinstance(data, dict):
            evals_list = data.get("evals", [])
        elif isinstance(data, list):
            evals_list = data
        else:
            return []

        return [
            self.make_result(
                file=rel,
                message=f"Sim '{ev.get('name', '')}' has no 'tags' field — "
                f"won't be found by --priority P0/P1/P2 filters",
                fix='Add: tags: ["P0", "category"]',
            )
            for ev in evals_list
            if isinstance(ev, dict) and "tags" not in ev
        ]


@rule("evals")
class ToolTestWrongKey(Rule):
    id = "E010"
    name = "eval-tool-test-wrong-key"
    description = (
        "Tool test YAML uses 'test_cases'"
        " instead of 'tests' — SCRAPI"
        " silently returns 0 tests"
    )
    default_severity = Severity.ERROR

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_tool_test(file_path):
            return []
        data = _parse_yaml(content)
        if not data or not isinstance(data, dict):
            return []

        rel = str(file_path.relative_to(context.project_root))
        results = []
        if "test_cases" in data and "tests" not in data:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "Uses 'test_cases' key but"
                        " SCRAPI expects 'tests'"
                        " — all tests will be"
                        " silently skipped"
                    ),
                    fix="Rename 'test_cases:' to 'tests:'",
                )
            )
        if "tool_name" in data and "tests" not in data:
            results.append(
                self.make_result(
                    file=rel,
                    message=(
                        "Uses top-level 'tool_name'"
                        " (old format) — SCRAPI"
                        " expects 'tool' on each"
                        " test case inside 'tests'"
                    ),
                    fix=(
                        "Restructure: move"
                        " tool_name into each test"
                        " case as 'tool:', rename"
                        " 'test_cases:' to 'tests:'"
                    ),
                )
            )
        return results


@rule("evals")
class InvalidMatchType(Rule):
    id = "E011"
    name = "eval-invalid-match-type"
    description = "Invalid $matchType value in golden tool_calls args"
    default_severity = Severity.ERROR

    VALID_MATCH_TYPES = {"ignore", "semantic", "contains", "regexp"}
    COMMON_TYPOS = {
        "regex": "regexp",
        "exact": None,
        "fuzzy": "semantic",
    }

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_golden(file_path):
            return []
        data = _parse_yaml(content)
        if not data:
            return []

        rel = str(file_path.relative_to(context.project_root))
        results = []
        for conv_name, turn_num, turn in _iter_golden_turns(data):
            for tc in turn.get("tool_calls", []):
                args = tc.get("args", {})
                if not isinstance(args, dict):
                    continue
                for arg_name, arg_val in args.items():
                    if not isinstance(arg_val, dict):
                        continue
                    match_type = arg_val.get("$matchType")
                    if (
                        match_type is None
                        or match_type in self.VALID_MATCH_TYPES
                    ):
                        continue
                    suggestion = self.COMMON_TYPOS.get(match_type)
                    valid_vals = ", ".join(sorted(self.VALID_MATCH_TYPES))
                    fix = (
                        f'Did you mean "{suggestion}"?'
                        if suggestion
                        else f"Valid values: {valid_vals}"
                    )
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"Conv"
                                f" '{conv_name}'"
                                f" turn"
                                f" {turn_num}:"
                                f" arg"
                                f" '{arg_name}'"
                                " has invalid"
                                " $matchType"
                                f" '{match_type}'"
                            ),
                            fix=fix,
                        )
                    )
        return results


def _is_shadow(file_path: Path) -> bool:
    return "shadows" in file_path.parts or "shadow_evals" in file_path.parts


@rule("evals")
class ShadowEvalStructure(Rule):
    id = "E012"
    name = "eval-shadow-structure"
    description = (
        "Shadow eval must specify conversation_id and non-empty natural "
        "expectations, use a valid replay_mode, and only known keys"
    )
    default_severity = Severity.ERROR

    _VALID_REPLAY_MODES = ("hybrid", "exact")
    _CONFIG_KEYS = frozenset(
        {
            "project_id",
            "location",
            "app_id",
            "gcs_bucket",
            "max_turns",
            "voice_config",
            "replay_mode",
            "use_tool_fakes",
            "session_parameters",
        }
    )

    @staticmethod
    def _case_keys() -> frozenset[str]:
        from cxas_scrapi.evals.shadow_evals import (  # noqa: PLC0415
            ShadowTestCase,
        )

        return frozenset(ShadowTestCase.model_fields)

    def _unknown_key_results(
        self, rel: str, data: dict, known: frozenset[str], where: str
    ) -> list[LintResult]:
        results = []
        for key in data:
            if key in known:
                continue
            close = difflib.get_close_matches(str(key), known, n=1)
            results.append(
                self.make_result(
                    file=rel,
                    severity=Severity.WARNING,
                    message=(
                        f"Unknown key '{key}' in {where} is ignored by "
                        "ShadowEvals"
                    ),
                    fix=f"Did you mean '{close[0]}'?" if close else "",
                )
            )
        return results

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if not _is_shadow(file_path):
            return []
        data = _parse_yaml(content)
        if not data:
            return []

        rel = str(file_path.relative_to(context.project_root))
        results = []
        config: dict = {}
        if isinstance(data, list):
            evals_list = data
        elif isinstance(data, dict):
            evals_list = (
                data.get("shadow_evals")
                or data.get("evals")
                or data.get("conversations")
                or []
            )
            config = data.get("config") or {}
            if isinstance(config, dict):
                results.extend(
                    self._unknown_key_results(
                        rel, config, self._CONFIG_KEYS, "the config block"
                    )
                )
            else:
                config = {}
        else:
            return []

        case_keys = self._case_keys()
        for idx, ev in enumerate(evals_list, start=1):
            if not isinstance(ev, dict):
                continue
            conv_id = str(ev.get("conversation_id", "")).strip()
            name = ev.get("name") or conv_id or f"item #{idx}"
            results.extend(
                self._unknown_key_results(
                    rel, ev, case_keys, f"shadow eval '{name}'"
                )
            )
            replay_mode = ev.get("replay_mode", config.get("replay_mode"))
            if (
                replay_mode is not None
                and replay_mode not in self._VALID_REPLAY_MODES
            ):
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Shadow eval '{name}' has invalid replay_mode "
                            f"'{replay_mode}'"
                        ),
                        fix="Use replay_mode: hybrid (default) or exact",
                    )
                )
            if not conv_id:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Shadow eval '{name}' is missing required "
                            "'conversation_id'"
                        ),
                        fix="Add conversation_id: '<past-conversation-uuid>'",
                    )
                )
            exps = ev.get("expectations")
            if not isinstance(exps, list) or not any(
                (isinstance(e, str) and e.strip())
                or (
                    isinstance(e, dict)
                    and str(e.get("expectation", "")).strip()
                )
                for e in exps
            ):
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Shadow eval '{name}' (conversation_id="
                            f"'{conv_id}') must define non-empty "
                            "'expectations' in natural prompts"
                        ),
                        fix=(
                            "Add expectations: ['The agent resolves the "
                            "user request...']"
                        ),
                    )
                )
        return results


@rule("evals")
class SimulationEvalStructure(Rule):
    """Validates simulation / CXAS 2.0 Milestone evaluation YAML structure.

    Grounded in CES MilestoneYamlTranslator and SimulationEvals:
    - Each evaluation entry must define a non-empty ``steps`` list.
    - Each step must define at least one of ``goal``, ``success_criteria``,
      ``static_utterance``, or ``event`` (and if ``event`` is a mapping, it
      must specify ``event`` or ``name``).
    - ``inject_variables`` is only supported on the first step (step 1).
    - Unknown keys on eval items or steps emit a warning with typo suggestions.
    """

    id = "E013"
    name = "eval-simulation-structure"
    description = (
        "Simulation/milestone eval must define valid steps, step actions, "
        "first-step-only inject_variables, and known keys"
    )
    default_severity = Severity.ERROR

    _NON_MILESTONE_MANIFEST_KEYS = frozenset(
        {"manifest_type", "scenarios", "cases", "test_cases", "conversations"}
    )

    _EVAL_KEYS = frozenset(
        {
            # Platform Milestone (CES MilestoneYamlTranslator) keys
            "name",
            "tags",
            "session_parameters",
            "sessionParameters",
            "steps",
            "expectations",
            "evaluation_expectations",
            "evaluationExpectations",
            "max_turns",
            "maxTurns",
            "max_turn_count",
            "maxTurnCount",
            "initial_event",
            "initialEvent",
            "metadata",
            # Local SimulationEvals keys
            "modality",
            "audio_expectations",
            "naturalness_metric",
            "naturalness",
            "use_tool_fakes",
            "barge_in_after_ms",
            "voice_config",
            "background_noise",
            "burst_noise",
            "pass_k",
            "runs",
            "initial_utterance",
            "tool_expectations",
            "insights_expectations",
            "trace_assertions",
            "priority",
            "category",
            "domain",
            "eval_id",
            "target_agent",
            "target_tool",
            "user_ask_summary",
            "legacy_dfcx_lineage",
            "description",
            "notes",
        }
    )

    _STEP_KEYS = frozenset(
        {
            "goal",
            "success_criteria",
            "successCriteria",
            "response_guide",
            "responseGuide",
            "protocol",
            "max_turns",
            "maxTurns",
            "max_turn_count",
            "maxTurnCount",
            "static_utterance",
            "staticUtterance",
            "event",
            "inject_variables",
            "injectVariables",
            "metadata",
            "barge_in_after_ms",
            "bargeInAfterMs",
            "description",
            "notes",
        }
    )

    def _unknown_key_warnings(
        self, rel: str, mapping: dict, allowed: frozenset[str], where: str
    ) -> list[LintResult]:
        results: list[LintResult] = []
        for key in mapping:
            key_str = str(key)
            if key_str in allowed or key_str.startswith("_"):
                continue
            close = difflib.get_close_matches(key_str, allowed, n=1)
            results.append(
                self.make_result(
                    file=rel,
                    severity=Severity.WARNING,
                    message=(
                        f"Unknown key '{key_str}' in {where} is ignored by "
                        "SimulationEvals / MilestoneYamlTranslator"
                    ),
                    fix=f"Did you mean '{close[0]}'?" if close else "",
                )
            )
        return results

    def check(
        self, file_path: Path, content: str, context: LintContext
    ) -> list[LintResult]:
        if (
            _is_golden(file_path)
            or _is_shadow(file_path)
            or _is_tool_test(file_path)
        ):
            return []

        data = _parse_yaml(content)
        if not data:
            return []

        from cxas_scrapi.utils.eval_utils import EvalUtils  # noqa: PLC0415

        is_sim_path = _is_simulation(file_path)
        is_milestone = EvalUtils.is_milestone_eval_data(data)
        if not is_sim_path and not is_milestone:
            return []

        rel = str(file_path.relative_to(context.project_root))
        results: list[LintResult] = []

        if isinstance(data, list):
            evals_list = data
        elif isinstance(data, dict):
            if "evals" in data and isinstance(data["evals"], list):
                evals_list = data["evals"]
            elif "steps" in data:
                evals_list = [data]
            elif self._NON_MILESTONE_MANIFEST_KEYS & data.keys():
                return []
            else:
                return [
                    self.make_result(
                        file=rel,
                        message=(
                            "Missing 'evals' (or single-case 'steps') key in "
                            "simulation/milestone eval YAML"
                        ),
                        fix="Add an 'evals:' list of simulation test cases",
                    )
                ]
        else:
            return []

        for idx, ev in enumerate(evals_list, start=1):
            if not isinstance(ev, dict):
                continue
            name = str(ev.get("name") or f"item #{idx}")
            results.extend(
                self._unknown_key_warnings(
                    rel, ev, self._EVAL_KEYS, f"simulation eval '{name}'"
                )
            )

            steps = ev.get("steps")
            if not isinstance(steps, list) or not steps:
                results.append(
                    self.make_result(
                        file=rel,
                        message=(
                            f"Simulation eval '{name}' must define a "
                            "non-empty 'steps' list"
                        ),
                        fix=(
                            "Add steps: [{goal: '...', "
                            "success_criteria: '...'}]"
                        ),
                    )
                )
                continue

            for step_idx, step in enumerate(steps):
                step_num = step_idx + 1
                if not isinstance(step, dict):
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"Simulation eval '{name}' step {step_num} "
                                "must be a mapping"
                            ),
                            fix=(
                                "Replace step with a mapping containing "
                                "'goal' and 'success_criteria'"
                            ),
                        )
                    )
                    continue

                results.extend(
                    self._unknown_key_warnings(
                        rel,
                        step,
                        self._STEP_KEYS,
                        f"simulation eval '{name}' step {step_num}",
                    )
                )

                has_goal = bool(str(step.get("goal") or "").strip())
                has_criteria = bool(
                    str(
                        step.get("success_criteria")
                        or step.get("successCriteria")
                        or ""
                    ).strip()
                )
                has_static = bool(
                    str(
                        step.get("static_utterance")
                        or step.get("staticUtterance")
                        or ""
                    ).strip()
                )
                event_val = step.get("event")
                has_event = event_val is not None and (
                    (isinstance(event_val, str) and bool(event_val.strip()))
                    or isinstance(event_val, dict)
                )

                if not (has_goal or has_criteria or has_static or has_event):
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"Simulation eval '{name}' step {step_num} "
                                "must define 'goal' (with 'success_criteria'), "
                                "'static_utterance', or 'event'"
                            ),
                            fix=(
                                "Add 'goal' and 'success_criteria', "
                                "'static_utterance', or 'event' to the step"
                            ),
                        )
                    )

                if isinstance(event_val, dict) and not (
                    str(event_val.get("event") or "").strip()
                    or str(event_val.get("name") or "").strip()
                ):
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"Simulation eval '{name}' step {step_num} "
                                "'event' mapping must specify an 'event' or "
                                "'name' key"
                            ),
                            fix="Use event: {event: '<event_name>'}",
                        )
                    )

                if step_idx > 0 and (
                    "inject_variables" in step or "injectVariables" in step
                ):
                    results.append(
                        self.make_result(
                            file=rel,
                            message=(
                                f"Simulation eval '{name}' step {step_num} "
                                "sets 'inject_variables', which is only "
                                "supported on the first step (step 1)"
                            ),
                            fix=(
                                "Move 'inject_variables' to step 1 or "
                                "'session_parameters' on the eval"
                            ),
                        )
                    )

        return results
