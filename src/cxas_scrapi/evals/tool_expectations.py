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

"""Deterministic tool_expectations evaluation for simulation traces."""

import ast
import contextlib
import dataclasses
import json
import re
from collections.abc import Callable, Sequence
from typing import Any, Literal

END_SESSION_TOOL = "end_session"
ANY_NON_EMPTY = "*"
TOOL_CHECK_PREFIX = "[tool] "
MET = "Met"
NOT_MET = "Not Met"

SKIP_NO_BLOCK = "the sim has no tool_expectations"
SKIP_CRASHED = "the job crashed (no trace)"

_TOOL_LINE_PREFIXES = ("Tool Call", "Tool Response")
_TOOL_LINE = re.compile(
    r"^Tool (Call|Call \(Output\)|Response): (\S+) with (?:args|result) (.*)$"
)
_TOP_LEVEL_CALL = "Call (Output)"
_PREVIEW_LIMIT = 3

Entry = dict[str, Any]
Verdict = tuple[bool, str]


@dataclasses.dataclass(frozen=True)
class ToolEvent:
    """One tool call (its args) or one tool response (its result) in order."""

    kind: Literal["call", "response"]
    tool: str
    payload: Any


@dataclasses.dataclass(frozen=True)
class ParsedTrace:
    """Ordered tool events and any unreadable tool lines from detailed_trace."""

    events: tuple[ToolEvent, ...] = ()
    unparsed_lines: tuple[str, ...] = ()

    def calls(self, tool: str) -> list[ToolEvent]:
        """Returns all recorded calls to `tool` in order."""
        return [e for e in self.events if e.kind == "call" and e.tool == tool]

    def all_calls(self) -> list[ToolEvent]:
        """Returns all recorded tool calls across all tools in order."""
        return [e for e in self.events if e.kind == "call"]

    def responses(self, tool: str) -> list[ToolEvent]:
        """Returns all recorded responses from `tool` in order."""
        return [
            e for e in self.events if e.kind == "response" and e.tool == tool
        ]


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """Outcome of one deterministic tool check on a simulation run."""

    operator: str
    expectation: str
    met: bool
    justification: str
    prefix: str = TOOL_CHECK_PREFIX

    def as_expectation_detail(self) -> dict[str, str]:
        """Formats this check as a SimulationEvals expectation_details entry."""
        return {
            "operator": self.operator,
            "expectation": self.prefix + self.expectation,
            "status": MET if self.met else NOT_MET,
            "justification": self.justification,
        }


def _tool_name(raw: str) -> str:
    return raw.rsplit("/", maxsplit=1)[-1]


def _parse_line(line: str) -> tuple[str, ToolEvent] | None:
    """Parses a single Tool Call or Tool Response line from detailed_trace."""
    match = _TOOL_LINE.match(line)
    if not match:
        return None
    form, tool, payload_repr = match.groups()
    try:
        payload = ast.literal_eval(payload_repr)
    except (ValueError, SyntaxError):
        return None
    kind: Literal["call", "response"] = (
        "response" if form == "Response" else "call"
    )
    return form, ToolEvent(kind=kind, tool=_tool_name(tool), payload=payload)


def _parse_chunk(chunk: str) -> tuple[list[ToolEvent], list[str]]:
    """Parses a detailed_trace turn chunk into events and unparsed lines."""
    events: list[ToolEvent] = []
    top_level_calls: list[ToolEvent] = []
    unparsed: list[str] = []
    for line in chunk.splitlines():
        if not line.startswith(_TOOL_LINE_PREFIXES):
            continue
        parsed = _parse_line(line)
        if parsed is None:
            unparsed.append(line)
            continue
        form, event = parsed
        if form == _TOP_LEVEL_CALL:
            top_level_calls.append(event)
        else:
            events.append(event)
    events.extend(e for e in top_level_calls if e not in events)
    return events, unparsed


def parse_trace(detailed_trace: Any) -> ParsedTrace:
    """Parses detailed_trace strings or tool objects into tool events."""
    if isinstance(detailed_trace, ParsedTrace):
        return detailed_trace

    events: list[ToolEvent] = []
    unparsed: list[str] = []

    if isinstance(detailed_trace, (list, tuple)):
        for item in detailed_trace:
            if isinstance(item, ToolEvent):
                events.append(item)
            elif isinstance(item, str):
                chunk_events, chunk_unparsed = _parse_chunk(item)
                events.extend(chunk_events)
                unparsed.extend(chunk_unparsed)
            elif hasattr(item, "name") and hasattr(item, "args"):
                events.append(
                    ToolEvent(
                        kind="call",
                        tool=_tool_name(str(item.name)),
                        payload=getattr(item, "args", {}),
                    )
                )
            elif hasattr(item, "name") and hasattr(item, "response"):
                events.append(
                    ToolEvent(
                        kind="response",
                        tool=_tool_name(str(item.name)),
                        payload=getattr(item, "response", None),
                    )
                )

    return ParsedTrace(events=tuple(events), unparsed_lines=tuple(unparsed))


def _is_empty(value: Any) -> bool:
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return value is None


def _as_dict(value: Any) -> Any:
    if isinstance(value, str):
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(value)
    return value


def _bools_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) and isinstance(actual, bool):
        return expected == actual
    as_text = {True: "true", False: "false"}
    flag, other = (
        (expected, actual) if isinstance(expected, bool) else (actual, expected)
    )
    return isinstance(other, str) and other.strip().lower() == as_text[flag]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numbers_match(expected: Any, actual: Any) -> bool:
    try:
        return float(expected) == float(actual)
    except (TypeError, ValueError):
        return False


def value_matches(expected: Any, actual: Any) -> bool:
    """Matches an expected YAML value against an actual trace payload value."""
    if expected == ANY_NON_EMPTY:
        return not _is_empty(actual)
    if isinstance(expected, dict):
        return not expected or subset_match(expected, actual)
    if isinstance(expected, bool) or isinstance(actual, bool):
        return _bools_match(expected, actual)
    if _is_number(actual) and (
        _is_number(expected) or isinstance(expected, str)
    ):
        return _numbers_match(expected, actual)
    return actual == expected or str(actual) == str(expected)


def subset_match(expected: dict[str, Any], actual: Any) -> bool:
    """Returns True if every key in `expected` matches in `actual`."""
    actual = _as_dict(actual)
    if not isinstance(actual, dict):
        return False
    return all(
        key in actual and value_matches(want, actual[key])
        for key, want in expected.items()
    )


def unwrap(response: Any) -> Any:
    """Unwraps CES `{'result': {...}}` tool response envelopes."""
    if (
        isinstance(response, dict)
        and set(response) == {"result"}
        and isinstance(response["result"], dict)
    ):
        return response["result"]
    return response


def dig(data: Any, *path: str) -> Any:
    """Traverses nested dict keys and returns None if any segment is missing."""
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _find_dict_nodes(data: Any, max_depth: int = 4) -> list[dict[str, Any]]:
    """Traverses nested dictionaries to discover candidate metadata payloads."""
    nodes: list[dict[str, Any]] = []
    if not isinstance(data, dict) or max_depth <= 0:
        return nodes
    for raw_val in data.values():
        dict_val = _as_dict(raw_val)
        if isinstance(dict_val, dict):
            nodes.append(dict_val)
            nodes.extend(_find_dict_nodes(dict_val, max_depth - 1))
    return nodes


def _fmt(value: Any, limit: int = 120) -> str:
    text = json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _preview(values: Sequence[Any]) -> str:
    shown = [_fmt(v) for v in values[:_PREVIEW_LIMIT]]
    more = len(values) - len(shown)
    return f"{shown}" + (f" (+{more} more)" if more > 0 else "")


def _called_but_unanswered(trace: ParsedTrace, tool: str) -> str:
    n = len(trace.calls(tool))
    if n:
        return f"{tool} was called {n} time(s), no response recorded"
    return f"{tool} was never called"


def _first_marker_index(
    events: Sequence[ToolEvent], marker: Entry
) -> int | None:
    """Finds the earliest event index matching a `before` marker."""
    for index, event in enumerate(events):
        if event.tool != marker["tool"]:
            continue
        if (
            "result" in marker
            and event.kind == "response"
            and subset_match(marker["result"], unwrap(event.payload))
        ):
            return index
        if (
            "result" not in marker
            and event.kind == "call"
            and subset_match(marker.get("args") or {}, event.payload)
        ):
            return index
    return None


def _eval_tool_called(entry: Entry, trace: ParsedTrace) -> Verdict:
    n = len(trace.calls(entry["tool"]))
    return n >= entry.get("min", 1), f"{entry['tool']} was called {n} time(s)"


def _eval_tool_not_called(entry: Entry, trace: ParsedTrace) -> Verdict:
    """Evaluates a `tool_not_called` expectation against `trace`."""
    events = list(trace.events)
    where = "in the conversation"
    marker = entry.get("before")
    if marker:
        index = _first_marker_index(events, marker)
        if index is None:
            m_tool = marker["tool"]
            where = (
                f"in the conversation ({m_tool} never matched before marker)"
            )
        else:
            events = events[:index]
            where = f"before {marker['tool']} matched"
    n = sum(1 for e in events if e.kind == "call" and e.tool == entry["tool"])
    return n == 0, f"{entry['tool']} was called {n} time(s) {where}"


def _eval_tool_input(entry: Entry, trace: ParsedTrace) -> Verdict:
    calls = trace.calls(entry["tool"])
    if not calls:
        return False, f"{entry['tool']} was never called"
    matching = [c for c in calls if subset_match(entry["args"], c.payload)]
    why = (
        f"{len(matching)} of {len(calls)} call(s) matched; calls:"
        f" {_preview([c.payload for c in calls])}"
    )
    return len(matching) >= entry.get("min", 1), why


def _eval_tool_output(entry: Entry, trace: ParsedTrace) -> Verdict:
    responses = [unwrap(r.payload) for r in trace.responses(entry["tool"])]
    if not responses:
        return False, _called_but_unanswered(trace, entry["tool"])
    matching = [r for r in responses if subset_match(entry["result"], r)]
    why = (
        f"{len(matching)} of {len(responses)} response(s) matched; responses:"
        f" {_preview(responses)}"
    )
    return len(matching) >= entry.get("min", 1), why


def _eval_last_tool(entry: Entry, trace: ParsedTrace) -> Verdict:
    calls = trace.all_calls()
    if not calls:
        return False, "no tool was called"
    last = calls[-1]
    met = last.tool == entry["tool"] and subset_match(
        entry.get("args") or {}, last.payload
    )
    return met, f"last tool call: {last.tool} {_fmt(last.payload)}"


def _eval_every_call(entry: Entry, trace: ParsedTrace) -> Verdict:
    calls = trace.calls(entry["tool"])
    if not calls:
        return True, f"{entry['tool']} was never called"
    offending = [
        c.payload for c in calls if not subset_match(entry["args"], c.payload)
    ]
    matched = len(calls) - len(offending)
    why = f"{matched} of {len(calls)} call(s) matched" + (
        f"; not matching: {_preview(offending)}" if offending else ""
    )
    return not offending, why


def _eval_followed_by(entry: Entry, trace: ParsedTrace) -> Verdict:
    """Evaluates a `followed_by` ordering expectation against `trace`."""
    calls = trace.all_calls()
    marker = entry["next"]
    positions = [i for i, c in enumerate(calls) if c.tool == entry["tool"]]
    if not positions:
        return True, f"{entry['tool']} was never called"

    def followed(position: int) -> bool:
        return any(
            c.tool == marker["tool"]
            and subset_match(marker.get("args") or {}, c.payload)
            for c in calls[position + 1 :]
        )

    unfollowed = [calls[i].payload for i in positions if not followed(i)]
    why = (
        f"{len(positions) - len(unfollowed)} of {len(positions)}"
        f" {entry['tool']} call(s) followed"
    )
    if unfollowed:
        why += f"; not followed: {_preview(unfollowed)}"
    return not unfollowed, why


def _eval_terminal_tool(entry: Entry, trace: ParsedTrace) -> Verdict:
    """Evaluates a `terminal_tool` expectation against `trace`."""
    calls = trace.all_calls()
    positions = [i for i, c in enumerate(calls) if c.tool == entry["tool"]]
    if not positions:
        return True, f"{entry['tool']} was never called"
    if len(positions) > 1:
        return False, f"{entry['tool']} was called {len(positions)} times"
    after = [c.tool for c in calls[positions[0] + 1 :]]
    why = (
        f"called after {entry['tool']}: {after}"
        if after
        else "nothing followed it"
    )
    return not after, why


def _eval_end_session(entry: Entry, trace: ParsedTrace) -> Verdict:
    """Evaluates an `end_session` (or `handoff`) expectation against `trace`."""
    calls = trace.all_calls()
    last = calls[-1] if calls else None
    if last is None or last.tool != END_SESSION_TOOL:
        last_name = last.tool if last else "none"
        return False, f"last tool call: {last_name} (not {END_SESSION_TOOL})"

    expected_args = entry.get("args")
    if expected_args is not None:
        if subset_match(expected_args, last.payload):
            return True, f"{END_SESSION_TOOL} args={_fmt(last.payload)}"
        if isinstance(last.payload, dict):
            for nested_key in ("params", "metadata", "xHeaders", "headers"):
                nested = last.payload.get(nested_key)
                if isinstance(nested, dict) and subset_match(
                    expected_args, nested
                ):
                    return (
                        True,
                        f"{END_SESSION_TOOL} {nested_key}={_fmt(nested)}",
                    )
            for nested in _find_dict_nodes(last.payload):
                if subset_match(expected_args, nested):
                    return True, f"{END_SESSION_TOOL} metadata={_fmt(nested)}"
        return False, (
            f"{END_SESSION_TOOL} args={_fmt(last.payload)} did not match "
            f"{_fmt(expected_args)}"
        )

    xheaders = entry.get("xheaders")
    if xheaders is not None:
        if subset_match(xheaders, last.payload):
            return True, f"{END_SESSION_TOOL} headers={_fmt(last.payload)}"
        if isinstance(last.payload, dict):
            for nested in _find_dict_nodes(last.payload):
                if subset_match(xheaders, nested):
                    return True, f"{END_SESSION_TOOL} headers={_fmt(nested)}"
        return False, (
            f"{END_SESSION_TOOL} headers={_fmt(last.payload)} did not match "
            f"{_fmt(xheaders)}"
        )

    return True, f"last tool call: {END_SESSION_TOOL} {_fmt(last.payload)}"


def _times(entry: Entry) -> str:
    n = entry.get("min", 1)
    return f" at least {n} times" if n > 1 else ""


def _describe_followed_by(entry: Entry) -> str:
    return f"every {entry['tool']} call is followed by {_fmt(entry['next'])}"


def _describe_not_called(entry: Entry) -> str:
    marker = entry.get("before")
    return f"{entry['tool']} is not called" + (
        f" before {_fmt(marker)}" if marker else ""
    )


def _describe_last_tool(entry: Entry) -> str:
    args = entry.get("args")
    return f"the last tool call is {entry['tool']}" + (
        f" with {_fmt(args)}" if args else ""
    )


def _describe_end_session(entry: Entry) -> str:
    args = entry.get("args")
    if args:
        return f"the call ends with {END_SESSION_TOOL} args {_fmt(args)}"
    xheaders = entry.get("xheaders")
    if xheaders:
        return f"the call ends with {END_SESSION_TOOL} headers {_fmt(xheaders)}"
    return f"the call ends with {END_SESSION_TOOL}"


def _describe_handoff(entry: Entry) -> str:
    args = entry.get("args") or entry.get("xheaders")
    if args:
        return f"the call ends with a handoff ({END_SESSION_TOOL}) {_fmt(args)}"
    return f"the call ends with a handoff ({END_SESSION_TOOL})"


@dataclasses.dataclass(frozen=True)
class Operator:
    """Specification and evaluator for a tool_expectations operator."""

    name: str
    required: frozenset[str]
    optional: frozenset[str]
    evaluate: Callable[[Entry, ParsedTrace], Verdict]
    describe: Callable[[Entry], str]


OPERATORS: dict[str, Operator] = {
    op.name: op
    for op in (
        Operator(
            "tool_called",
            frozenset({"tool"}),
            frozenset({"min"}),
            _eval_tool_called,
            lambda e: f"{e['tool']} is called{_times(e)}",
        ),
        Operator(
            "tool_not_called",
            frozenset({"tool"}),
            frozenset({"before"}),
            _eval_tool_not_called,
            _describe_not_called,
        ),
        Operator(
            "tool_input",
            frozenset({"tool", "args"}),
            frozenset({"min"}),
            _eval_tool_input,
            lambda e: (
                f"{e['tool']} is called{_times(e)} with {_fmt(e['args'])}"
            ),
        ),
        Operator(
            "tool_output",
            frozenset({"tool", "result"}),
            frozenset({"min"}),
            _eval_tool_output,
            lambda e: f"{e['tool']} returns{_times(e)} {_fmt(e['result'])}",
        ),
        Operator(
            "last_tool",
            frozenset({"tool"}),
            frozenset({"args"}),
            _eval_last_tool,
            _describe_last_tool,
        ),
        Operator(
            "every_call",
            frozenset({"tool", "args"}),
            frozenset(),
            _eval_every_call,
            lambda e: f"every {e['tool']} call has {_fmt(e['args'])}",
        ),
        Operator(
            "followed_by",
            frozenset({"tool", "next"}),
            frozenset(),
            _eval_followed_by,
            _describe_followed_by,
        ),
        Operator(
            "terminal_tool",
            frozenset({"tool"}),
            frozenset(),
            _eval_terminal_tool,
            lambda e: (
                f"{e['tool']} is called at most once and nothing follows it"
            ),
        ),
        Operator(
            "end_session",
            frozenset(),
            frozenset({"args", "xheaders"}),
            _eval_end_session,
            _describe_end_session,
        ),
        Operator(
            "handoff",
            frozenset(),
            frozenset({"args", "xheaders"}),
            _eval_end_session,
            _describe_handoff,
        ),
    )
}

_MARKER_FIELDS = {"before": ("args", "result"), "next": ("args",)}


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _marker_problems(op_type: str, name: str, marker: Any) -> list[str]:
    """Validates a `before` or `next` marker sub-dictionary."""
    if not isinstance(marker, dict):
        return [f"{op_type}: '{name}' must be a mapping"]
    fields = _MARKER_FIELDS[name]
    problems: list[str] = []
    if not _is_name(marker.get("tool")):
        problems.append(f"{op_type}: '{name}' needs a non-empty 'tool'")
    extra = set(marker) - {"tool", *fields}
    if extra:
        problems.append(
            f"{op_type}: '{name}' has unexpected field(s) {sorted(extra)}"
        )
    if len(set(fields) & set(marker)) > 1:
        choices = " or ".join(map(repr, fields))
        problems.append(f"{op_type}: '{name}' takes at most one of {choices}")
    for key in fields:
        if key in marker and not isinstance(marker[key], dict):
            problems.append(f"{op_type}: '{name}.{key}' must be a mapping")
    return problems


def _field_problems(op_type: str, entry: Entry) -> list[str]:
    """Validates field value types on a tool_expectations entry."""
    problems: list[str] = []
    if "tool" in entry and not _is_name(entry["tool"]):
        problems.append(f"{op_type}: 'tool' must be a non-empty string")
    for key in ("args", "result", "xheaders"):
        if key in entry and not isinstance(entry[key], dict):
            problems.append(f"{op_type}: '{key}' must be a mapping")
    if "min" in entry and not (
        isinstance(entry["min"], int)
        and not isinstance(entry["min"], bool)
        and entry["min"] >= 1
    ):
        problems.append(f"{op_type}: 'min' must be an integer >= 1")
    for name in _MARKER_FIELDS:
        if name in entry:
            problems.extend(_marker_problems(op_type, name, entry[name]))
    return problems


def validate(entry: Any) -> list[str]:
    """Returns schema validation errors for a tool_expectations entry."""
    if not isinstance(entry, dict):
        return [f"entry is not a mapping: {entry!r}"]
    op_type = entry.get("type")
    operator = OPERATORS.get(str(op_type)) if isinstance(op_type, str) else None
    if operator is None:
        return [
            f"unknown type {op_type!r} (expected one of {', '.join(OPERATORS)})"
        ]
    problems = [
        f"{op_type}: missing {name!r}"
        for name in sorted(operator.required - set(entry))
    ]
    extra = set(entry) - operator.required - operator.optional - {"type"}
    if extra:
        problems.append(f"{op_type}: unexpected field(s) {sorted(extra)}")
    return problems + _field_problems(operator.name, entry)


def validate_all(tool_expectations: Any) -> list[str]:
    """Validates a list of tool_expectations entries."""
    if not isinstance(tool_expectations, list):
        return [
            "tool_expectations must be a list, got"
            f" {type(tool_expectations).__name__}"
        ]
    problems: list[str] = []
    for idx, entry in enumerate(tool_expectations):
        for p in validate(entry):
            problems.append(f"entry [{idx}]: {p}")
    return problems


def _run_entry(entry: Any, trace: ParsedTrace) -> CheckResult:
    """Validates and evaluates a single tool_expectations entry."""
    problems = validate(entry)
    if problems:
        op_type = entry.get("type") if isinstance(entry, dict) else None
        return CheckResult(
            str(op_type or "invalid"),
            f"valid entry {_fmt(entry)}",
            False,
            "; ".join(problems),
        )
    operator = OPERATORS[entry["type"]]
    met, why = operator.evaluate(entry, trace)
    return CheckResult(operator.name, operator.describe(entry), met, why)


def trace_parsed_check(trace: ParsedTrace) -> CheckResult:
    """Returns a CheckResult asserting all tool trace lines parsed cleanly."""
    bad = trace.unparsed_lines
    why = (
        f"{len(bad)} tool line(s) could not be parsed: {_preview(list(bad))}"
        if bad
        else "every tool line parsed"
    )
    return CheckResult(
        "trace_parsed", "every tool line in the trace was parsed", not bad, why
    )


def check(
    tool_expectations: Sequence[Any], trace: ParsedTrace
) -> list[CheckResult]:
    """Evaluates all tool_expectations entries plus the trace-parsed check."""
    if not tool_expectations:
        return []
    if not isinstance(tool_expectations, list):
        found = type(tool_expectations).__name__
        entries = [
            CheckResult(
                "invalid",
                "tool_expectations is a list of entries",
                False,
                f"got a {found}",
            )
        ]
    else:
        entries = [_run_entry(entry, trace) for entry in tool_expectations]
    return [*entries, trace_parsed_check(trace)]


def has_scorable_trace(result: Any) -> bool:
    """Returns True when result is a simulation dict with a trace."""
    return (
        isinstance(result, dict)
        and "error" not in result
        and isinstance(result.get("detailed_trace"), list)
    )


def skip_reason(test_case: dict[str, Any] | None, result: Any) -> str | None:
    """Returns why tool checks are skipped for a job, or None if they run."""
    if not (test_case or {}).get("tool_expectations"):
        return SKIP_NO_BLOCK
    if not has_scorable_trace(result):
        return SKIP_CRASHED
    return None


def _is_tool_check(detail: Any) -> bool:
    return isinstance(detail, dict) and str(
        detail.get("expectation", "")
    ).startswith(TOOL_CHECK_PREFIX)


def judge_verdict(result: dict[str, Any]) -> bool:
    """Returns the LLM judge verdict prior to folding deterministic checks."""
    if "judge_passed" in result:
        return bool(result["judge_passed"])
    if "error" in result:
        return False
    details = result.get("expectation_details") or []
    if not any(_is_tool_check(d) for d in details):
        return bool(result.get("passed"))
    judged = [d for d in details if not _is_tool_check(d)]
    goals_met, _, goals_total = str(result.get("goals", "")).partition("/")
    return (
        bool(goals_total)
        and goals_met == goals_total
        and all(d.get("status") == MET for d in judged)
    )


def fold_into(result: dict[str, Any], check_results: list[CheckResult]) -> None:
    """Writes tool check verdicts into `result` idempotently."""
    judge_passed = result.setdefault("judge_passed", judge_verdict(result))
    details = [
        d
        for d in result.get("expectation_details") or []
        if not _is_tool_check(d)
    ]
    details += [r.as_expectation_detail() for r in check_results]
    result["expectation_details"] = details
    met = sum(1 for d in details if d.get("status") == MET)
    result["expectations"] = f"{met}/{len(details)}"
    result["passed"] = bool(judge_passed) and all(r.met for r in check_results)


def checker_error_result(exc: BaseException) -> CheckResult:
    """Formats an unexpected checker exception as a failed CheckResult."""
    return CheckResult(
        "checker_error",
        "the tool checks ran",
        False,
        f"{type(exc).__name__}: {exc}",
    )


def apply_to_result(
    test_case: dict[str, Any] | None, result: Any
) -> list[CheckResult]:
    """Scores `tool_expectations` against `result` and folds in place."""
    if skip_reason(test_case, result) or test_case is None:
        return []
    try:
        check_results = check(
            test_case["tool_expectations"],
            parse_trace(result.get("detailed_trace")),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        check_results = [checker_error_result(exc)]
    fold_into(result, check_results)
    return check_results


__all__ = [
    "ANY_NON_EMPTY",
    "END_SESSION_TOOL",
    "MET",
    "NOT_MET",
    "OPERATORS",
    "SKIP_CRASHED",
    "SKIP_NO_BLOCK",
    "TOOL_CHECK_PREFIX",
    "CheckResult",
    "Operator",
    "ParsedTrace",
    "ToolEvent",
    "apply_to_result",
    "check",
    "checker_error_result",
    "dig",
    "fold_into",
    "has_scorable_trace",
    "judge_verdict",
    "parse_trace",
    "skip_reason",
    "subset_match",
    "trace_parsed_check",
    "unwrap",
    "validate",
    "validate_all",
    "value_matches",
]
