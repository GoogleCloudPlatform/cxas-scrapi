"""Normalizes a CES `Conversation` proto into a structured trace and renders it.

Output formats: JSON, Markdown, plain text, HTML. The normalized
representation is a list of `TraceEntry` dicts that the four formatters share,
so adding a new format only means writing a new render function.
"""

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

import datetime
import html as _html
import json
from typing import Any

from google.cloud.ces_v1beta import types as ces_types

from cxas_scrapi.core.common import Common

_SOURCE_NAMES = {s.value: s.name for s in ces_types.Conversation.Source}
_INPUT_TYPE_NAMES = {s.value: s.name for s in ces_types.Conversation.InputType}


def normalize(conversation: Any) -> dict[str, Any]:
    """Converts a `types.Conversation` proto (or dict) to a normalized trace.

    The normalized form is dict-of-primitives that can be JSON-serialized,
    fed back into a Pydantic model, or rendered. Each entry has a `kind`
    (`user`, `agent`, `tool_call`, `tool_response`, `agent_transfer`,
    `custom_payload`, `system`) plus a small payload.
    """
    conv_dict = (
        type(conversation).to_dict(conversation)
        if not isinstance(conversation, dict)
        else conversation
    )

    entries: list[dict[str, Any]] = []
    turn_metrics: list[dict[str, Any]] = []
    for turn_idx, turn in enumerate(conv_dict.get("turns", [])):
        for msg in turn.get("messages", []):
            role = msg.get("role", "")
            for chunk in msg.get("chunks", []) or []:
                entry = _chunk_to_entry(chunk, role, turn_idx)
                if entry is not None:
                    entries.append(entry)
        turn_metrics.append(_turn_metrics(turn, turn_idx))

    raw_source = conv_dict.get("source")
    raw_input_types = conv_dict.get("input_types") or []
    return {
        "conversation_id": (conv_dict.get("name") or "").split("/")[-1],
        "name": conv_dict.get("name"),
        "display_name": conv_dict.get("display_name"),
        "source": _source_name(raw_source),
        "input_types": [_input_type_name(it) for it in raw_input_types],
        "channel": _channel_label(raw_input_types),
        "start_time": _to_iso(conv_dict.get("start_time")),
        "end_time": _to_iso(conv_dict.get("end_time")),
        "num_turns": len(conv_dict.get("turns", [])),
        "entries": entries,
        "turn_metrics": turn_metrics,
        "totals": _conversation_totals(turn_metrics),
        "raw": conv_dict,
    }


def _turn_metrics(turn: dict[str, Any], turn_idx: int) -> dict[str, Any]:
    """Pulls per-turn timing, latency, tokens, and a flat span list."""
    rs = turn.get("root_span") or {}
    spans: list[dict[str, Any]] = []
    _flatten_spans(rs.get("child_spans", []) or [], spans)
    tokens = _sum_tokens(spans)
    return {
        "turn": turn_idx,
        "start_time": _to_iso(rs.get("start_time")),
        "end_time": _to_iso(rs.get("end_time")),
        "duration_ms": _duration_ms(
            rs.get("start_time"), rs.get("end_time"), rs.get("duration")
        ),
        "perceived_latency_ms": _attr(rs, "perceived latency (ms)"),
        "tokens": tokens,
        "spans": spans,
    }


def _flatten_spans(
    children: list[dict[str, Any]],
    out: list[dict[str, Any]],
    depth: int = 0,
) -> None:
    """Walks `child_spans` recursively into a flat list with depth markers."""
    for s in children:
        attrs = s.get("attributes") or {}
        out.append(
            {
                "name": s.get("name"),
                "depth": depth,
                "stage": attrs.get("stage"),
                "agent": attrs.get("agent"),
                "tool": attrs.get("name") if s.get("name") == "Tool" else None,
                "model": attrs.get("model"),
                "tool_args": Common.unwrap_struct(attrs.get("args"))
                if s.get("name") == "Tool"
                else None,
                "tool_response": Common.unwrap_struct(attrs.get("response"))
                if s.get("name") == "Tool"
                else None,
                "tool_id": attrs.get("tool call id"),
                "tool_type": attrs.get("type"),
                "tokens": (
                    {
                        "input": _to_int(attrs.get("input token count")),
                        "output": _to_int(attrs.get("output token count")),
                        "thought": _to_int(attrs.get("thought token count")),
                        "ttfc_ms": _to_int(
                            attrs.get("time to first chunk (ms)")
                        ),
                    }
                    if s.get("name") == "LLM"
                    else None
                ),
                "duration_ms": _duration_ms(
                    s.get("start_time"), s.get("end_time"), s.get("duration")
                ),
                "start_time": _to_iso(s.get("start_time")),
                "end_time": _to_iso(s.get("end_time")),
            }
        )
        _flatten_spans(s.get("child_spans", []) or [], out, depth + 1)


def _sum_tokens(spans: list[dict[str, Any]]) -> dict[str, int]:
    totals = {"input": 0, "output": 0, "thought": 0}
    for s in spans:
        t = s.get("tokens")
        if not t:
            continue
        for k in totals:
            v = t.get(k)
            if v is not None:
                totals[k] += v
    totals["total"] = totals["input"] + totals["output"] + totals["thought"]
    return totals


def _conversation_totals(
    turn_metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    total_ms = 0.0
    perceived_ms = 0.0
    tokens = {"input": 0, "output": 0, "thought": 0, "total": 0}
    span_counts: dict[str, int] = {}
    for tm in turn_metrics:
        if tm.get("duration_ms") is not None:
            total_ms += tm["duration_ms"]
        if tm.get("perceived_latency_ms") is not None:
            perceived_ms += tm["perceived_latency_ms"]
        for k in tokens:
            tokens[k] += tm.get("tokens", {}).get(k, 0) or 0
        for s in tm.get("spans", []):
            span_counts[s["name"]] = span_counts.get(s["name"], 0) + 1
    return {
        "duration_ms": total_ms,
        "perceived_latency_ms": perceived_ms,
        "tokens": tokens,
        "span_counts": span_counts,
    }


def _attr(span: dict[str, Any], key: str) -> Any:
    attrs = span.get("attributes") or {}
    if isinstance(attrs, dict):
        return _to_int_or_float(attrs.get(key))
    return None


def _duration_ms(start: Any, end: Any, duration: Any = None) -> float | None:
    if duration:
        # CES proto serializes as e.g. "0.131247s"
        if isinstance(duration, str) and duration.endswith("s"):
            try:
                return float(duration[:-1]) * 1000.0
            except ValueError:
                pass
    s = _to_dt(start)
    e = _to_dt(end)
    if s and e:
        return (e - s).total_seconds() * 1000.0
    return None


def _to_dt(x: Any):
    if x is None:
        return None
    if isinstance(x, datetime.datetime):
        return x
    if isinstance(x, str):
        try:
            return datetime.datetime.fromisoformat(x.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _to_int(x: Any) -> int | None:
    if x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _to_int_or_float(x: Any) -> Any:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return x
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None


def _chunk_to_entry(
    chunk: dict[str, Any],
    role: str,
    turn_idx: int,
) -> dict[str, Any] | None:
    if "text" in chunk and chunk["text"]:
        kind = "user" if role.lower() == "user" else "agent"
        return {
            "kind": kind,
            "turn": turn_idx,
            "role": role,
            "text": chunk["text"],
        }
    if "transcript" in chunk and chunk["transcript"]:
        return {
            "kind": "user" if role.lower() == "user" else "agent",
            "turn": turn_idx,
            "role": role,
            "text": chunk["transcript"],
        }
    if "tool_call" in chunk:
        tc = chunk["tool_call"]
        return {
            "kind": "tool_call",
            "turn": turn_idx,
            "agent": role,
            "tool": tc.get("display_name") or tc.get("name") or tc.get("tool"),
            "args": Common.unwrap_struct(tc.get("args", {})),
        }
    if "tool_response" in chunk:
        tr = chunk["tool_response"]
        return {
            "kind": "tool_response",
            "turn": turn_idx,
            "agent": role,
            "tool": tr.get("display_name") or tr.get("name") or tr.get("tool"),
            "response": Common.unwrap_struct(tr.get("response", {})),
        }
    if "agent_transfer" in chunk:
        at = chunk["agent_transfer"]
        target = (
            at.get("target_agent")
            or at.get("agent")
            or at.get("display_name")
            or at
        )
        return {
            "kind": "agent_transfer",
            "turn": turn_idx,
            "agent": role,
            "target": target,
        }
    if "payload" in chunk:
        return {
            "kind": "custom_payload",
            "turn": turn_idx,
            "agent": role,
            "payload": Common.unwrap_struct(chunk["payload"]),
        }
    if "default_variables" in chunk:
        return {
            "kind": "variable_default",
            "turn": turn_idx,
            "agent": role,
            "variables": Common.unwrap_struct(chunk["default_variables"]),
        }
    if "updated_variables" in chunk:
        return {
            "kind": "variable_update",
            "turn": turn_idx,
            "agent": role,
            "variables": Common.unwrap_struct(chunk["updated_variables"]),
        }
    return None


def _channel_label(input_types: list[Any]) -> str:
    """Maps `input_types` (list of enums or strings) to a friendly label."""
    if not input_types:
        return "UNKNOWN"
    names = [_input_type_name(it) for it in input_types]
    if "INPUT_TYPE_AUDIO" in names and "INPUT_TYPE_TEXT" in names:
        return "MULTIMODAL"
    if "INPUT_TYPE_AUDIO" in names:
        return "AUDIO"
    if "INPUT_TYPE_TEXT" in names:
        return "TEXT"
    return "OTHER"


def _input_type_name(it: Any) -> str:
    if hasattr(it, "name"):
        return it.name
    if isinstance(it, int):
        return _INPUT_TYPE_NAMES.get(it, f"INPUT_TYPE_{it}")
    return str(it).upper()


def _source_name(s: Any) -> str | None:
    if s is None:
        return None
    if hasattr(s, "name"):
        return s.name
    if isinstance(s, int):
        return _SOURCE_NAMES.get(s, f"SOURCE_{s}")
    return str(s)


def _to_iso(ts: Any) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, str):
        return ts
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    return str(ts)


def to_json(
    trace: dict[str, Any],
    include_raw: bool = False,
    extras: dict[str, Any] | None = None,
) -> str:
    payload = {k: v for k, v in trace.items() if include_raw or k != "raw"}
    if extras:
        payload["extras"] = extras
    return json.dumps(payload, indent=2, default=str, sort_keys=False)


def to_text(trace: dict[str, Any], extras: dict[str, Any] | None = None) -> str:
    lines: list[str] = []
    lines.append(f"Conversation: {trace.get('conversation_id')}")
    lines.append(
        f"  source={trace.get('source')}  channel={trace.get('channel')}  "
        f"turns={trace.get('num_turns')}"
    )
    lines.append(
        f"  start={trace.get('start_time')}  end={trace.get('end_time')}"
    )
    totals = trace.get("totals") or {}
    if totals:
        t = totals.get("tokens", {})
        lines.append(
            f"  total_duration={_fmt_ms(totals.get('duration_ms'))}  "
            f"perceived={_fmt_ms(totals.get('perceived_latency_ms'))}  "
            f"tokens(in/out/think/total)="
            f"{t.get('input', 0)}/{t.get('output', 0)}/"
            f"{t.get('thought', 0)}/{t.get('total', 0)}"
        )
    lines.append("")
    by_turn = _entries_by_turn(trace.get("entries", []))
    metrics_by_turn = {m["turn"]: m for m in trace.get("turn_metrics", [])}
    for turn_idx in sorted(set(by_turn) | set(metrics_by_turn)):
        m = metrics_by_turn.get(turn_idx, {})
        lines.append(_turn_header_text(turn_idx, m))
        for e in by_turn.get(turn_idx, []):
            lines.append(_entry_to_text(e))
        for s in m.get("spans", []) or []:
            lines.append(_span_to_text(s))
    if extras:
        lines.append("")
        lines.append("--- Extras ---")
        lines.append(json.dumps(extras, indent=2, default=str))
    return "\n".join(lines)


def _entries_by_turn(
    entries: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for e in entries:
        out.setdefault(e.get("turn", 0), []).append(e)
    return out


def _turn_header_text(turn_idx: int, m: dict[str, Any]) -> str:
    bits = [f"-- Turn {turn_idx} --"]
    if m.get("duration_ms") is not None:
        bits.append(f"duration={_fmt_ms(m['duration_ms'])}")
    if m.get("perceived_latency_ms") is not None:
        bits.append(f"perceived={_fmt_ms(m['perceived_latency_ms'])}")
    tokens = m.get("tokens") or {}
    if tokens.get("total"):
        bits.append(
            f"tokens={tokens.get('input', 0)}in/{tokens.get('output', 0)}out"
        )
    return "  ".join(bits)


def _span_to_text(s: dict[str, Any]) -> str:
    indent = "    " * (1 + s.get("depth", 0))
    name = s.get("name") or "?"
    dur = _fmt_ms(s.get("duration_ms"))
    if name == "Callback":
        return f"{indent}* Callback[{s.get('stage')}] {dur}"
    if name == "LLM":
        t = s.get("tokens") or {}
        return (
            f"{indent}* LLM {s.get('model')} {dur}  "
            f"in={t.get('input')} out={t.get('output')} "
            f"ttfc_ms={t.get('ttfc_ms')}"
        )
    if name == "Tool":
        return (
            f"{indent}* Tool {s.get('tool')} {dur}  "
            f"args={json.dumps(s.get('tool_args'), default=str)[:80]}"
        )
    return f"{indent}* {name} {dur}"


def _entry_to_text(e: dict[str, Any]) -> str:
    kind = e["kind"]
    turn = e.get("turn", 0)
    if kind == "user":
        return f"[{turn}] USER: {e['text']}"
    if kind == "agent":
        return f"[{turn}] AGENT ({e.get('role', '?')}): {e['text']}"
    if kind == "tool_call":
        return (
            f"[{turn}]   tool_call {e.get('tool')} "
            f"args={json.dumps(e.get('args'), default=str)}"
        )
    if kind == "tool_response":
        resp = json.dumps(e.get("response"), default=str)
        if len(resp) > 200:
            resp = resp[:197] + "..."
        return f"[{turn}]   tool_response {e.get('tool')} -> {resp}"
    if kind == "agent_transfer":
        return f"[{turn}]   transfer -> {e.get('target')}"
    if kind == "custom_payload":
        return (
            f"[{turn}]   custom_payload "
            f"{json.dumps(e.get('payload'), default=str)}"
        )
    if kind == "variable_default":
        return (
            f"[{turn}]   var_default "
            f"{json.dumps(e.get('variables'), default=str)}"
        )
    if kind == "variable_update":
        return (
            f"[{turn}]   var_update "
            f"{json.dumps(e.get('variables'), default=str)}"
        )
    return f"[{turn}]   {kind}"


def _fmt_ms(v: Any) -> str:
    if v is None:
        return "?"
    return f"{float(v):.1f}ms"


def to_markdown(
    trace: dict[str, Any],
    console_url: str | None = None,
    extras: dict[str, Any] | None = None,
) -> str:
    md: list[str] = []
    md.append(f"# Conversation `{trace.get('conversation_id')}`\n")
    md.append("| Field | Value |")
    md.append("|---|---|")
    md.append(f"| Source | {trace.get('source')} |")
    md.append(f"| Channel | {trace.get('channel')} |")
    md.append(f"| Turns | {trace.get('num_turns')} |")
    md.append(f"| Start | {trace.get('start_time')} |")
    md.append(f"| End | {trace.get('end_time')} |")
    totals = trace.get("totals") or {}
    if totals:
        t = totals.get("tokens", {})
        md.append(f"| Total span time | {_fmt_ms(totals.get('duration_ms'))} |")
        md.append(
            f"| Total perceived latency | "
            f"{_fmt_ms(totals.get('perceived_latency_ms'))} |"
        )
        md.append(
            f"| Tokens (in / out / think / total) | "
            f"{t.get('input', 0)} / {t.get('output', 0)} / "
            f"{t.get('thought', 0)} / {t.get('total', 0)} |"
        )
        sc = totals.get("span_counts") or {}
        if sc:
            md.append(
                f"| Span counts | "
                f"{', '.join(f'{k}={v}' for k, v in sorted(sc.items()))} |"
            )
    if console_url:
        md.append(f"| Console | [Open in CES Console]({console_url}) |")
    md.append("")
    md.append("## Trace\n")

    by_turn = _entries_by_turn(trace.get("entries", []))
    metrics_by_turn = {m["turn"]: m for m in trace.get("turn_metrics", [])}
    for turn_idx in sorted(set(by_turn) | set(metrics_by_turn)):
        m = metrics_by_turn.get(turn_idx, {})
        md.append(_turn_header_md(turn_idx, m))
        for e in by_turn.get(turn_idx, []):
            md.append(_entry_to_markdown(e))
        spans = m.get("spans") or []
        if spans:
            md.append("")
            md.append("  <details><summary>Execution spans</summary>")
            md.append("")
            md.append(
                "  | depth | name | stage / tool / model | duration |"
                " tokens (in/out/ttfc_ms) |"
            )
            md.append("  |---|---|---|---|---|")
            for s in spans:
                md.append(_span_to_markdown(s))
            md.append("  </details>")
        md.append("")

    if extras:
        for section, body in extras.items():
            md.append(f"\n## {section}\n")
            if isinstance(body, str):
                md.append(body)
            else:
                md.append("```json")
                md.append(json.dumps(body, indent=2, default=str))
                md.append("```")
    return "\n".join(md) + "\n"


def _turn_header_md(turn_idx: int, m: dict[str, Any]) -> str:
    bits = [f"### Turn {turn_idx}"]
    extras = []
    if m.get("duration_ms") is not None:
        extras.append(f"duration **{_fmt_ms(m['duration_ms'])}**")
    if m.get("perceived_latency_ms") is not None:
        extras.append(f"perceived **{_fmt_ms(m['perceived_latency_ms'])}**")
    tokens = m.get("tokens") or {}
    if tokens.get("total"):
        extras.append(
            f"tokens **{tokens.get('input', 0)}** in / "
            f"**{tokens.get('output', 0)}** out"
        )
    if extras:
        bits.append("— " + " · ".join(extras))
    return " ".join(bits)


def _span_to_markdown(s: dict[str, Any]) -> str:
    name = s.get("name") or "?"
    dur = _fmt_ms(s.get("duration_ms"))
    label = s.get("stage") or s.get("tool") or s.get("model") or ""
    if name == "LLM":
        t = s.get("tokens") or {}
        token_str = (
            f"{t.get('input', 0)} / {t.get('output', 0)} / "
            f"{t.get('ttfc_ms', '?')}"
        )
    else:
        token_str = ""
    return (
        f"  | {s.get('depth', 0)} | {name} | `{label}` | {dur} | {token_str} |"
    )


def _entry_to_markdown(e: dict[str, Any]) -> str:
    kind = e["kind"]
    turn = e.get("turn", 0)
    if kind == "user":
        return f"- **[{turn}] User:** {e['text']}"
    if kind == "agent":
        return f"- **[{turn}] Agent ({e.get('role', '?')}):** {e['text']}"
    if kind == "tool_call":
        args = json.dumps(e.get("args"), indent=2, default=str)
        return (
            f"- **[{turn}] Tool call** `{e.get('tool')}`\n"
            f"  ```json\n  {args}\n  ```"
        )
    if kind == "tool_response":
        resp = json.dumps(e.get("response"), indent=2, default=str)
        return (
            f"- **[{turn}] Tool response** `{e.get('tool')}`\n"
            f"  ```json\n  {resp}\n  ```"
        )
    if kind == "agent_transfer":
        return f"- **[{turn}] Transfer ->** `{e.get('target')}`"
    if kind == "custom_payload":
        body = json.dumps(e.get("payload"), indent=2, default=str)
        return f"- **[{turn}] Custom payload**\n  ```json\n  {body}\n  ```"
    if kind == "variable_default":
        body = json.dumps(e.get("variables"), indent=2, default=str)
        return f"- **[{turn}] Default variables**\n  ```json\n  {body}\n  ```"
    if kind == "variable_update":
        body = json.dumps(e.get("variables"), indent=2, default=str)
        return f"- **[{turn}] Variable update**\n  ```json\n  {body}\n  ```"
    return f"- [{turn}] {kind}"


def to_html(
    trace: dict[str, Any],
    console_url: str | None = None,
    extras: dict[str, Any] | None = None,
    audio_path: str | None = None,
) -> str:
    body: list[str] = []
    title = _html.escape(trace.get("conversation_id") or "conversation")
    body.append(f"<h1>Conversation <code>{title}</code></h1>")
    body.append("<table>")
    for k in ("source", "channel", "num_turns", "start_time", "end_time"):
        body.append(
            f"<tr><th>{k}</th><td>{_html.escape(str(trace.get(k)))}</td></tr>"
        )
    totals = trace.get("totals") or {}
    if totals:
        t = totals.get("tokens", {})
        body.append(
            f"<tr><th>total span time</th><td>"
            f"{_fmt_ms(totals.get('duration_ms'))}</td></tr>"
        )
        body.append(
            f"<tr><th>total perceived latency</th><td>"
            f"{_fmt_ms(totals.get('perceived_latency_ms'))}</td></tr>"
        )
        body.append(
            f"<tr><th>tokens (in/out/think/total)</th><td>"
            f"{t.get('input', 0)} / {t.get('output', 0)} / "
            f"{t.get('thought', 0)} / {t.get('total', 0)}</td></tr>"
        )
        sc = totals.get("span_counts") or {}
        if sc:
            sc_str = ", ".join(f"{k}={v}" for k, v in sorted(sc.items()))
            body.append(
                f"<tr><th>span counts</th><td>{_html.escape(sc_str)}</td></tr>"
            )
    if console_url:
        body.append(
            f'<tr><th>Console</th><td><a href="{_html.escape(console_url)}" '
            f'target="_blank">Open in CES Console</a></td></tr>'
        )
    body.append("</table>")
    if audio_path:
        body.append(
            f'<audio controls src="{_html.escape(audio_path)}"></audio>'
        )
    body.append("<h2>Trace</h2>")
    body.append('<div class="trace">')
    by_turn = _entries_by_turn(trace.get("entries", []))
    metrics_by_turn = {m["turn"]: m for m in trace.get("turn_metrics", [])}
    for turn_idx in sorted(set(by_turn) | set(metrics_by_turn)):
        m = metrics_by_turn.get(turn_idx, {})
        body.append(_turn_header_html(turn_idx, m))
        for e in by_turn.get(turn_idx, []):
            body.append(_entry_to_html(e))
        spans = m.get("spans") or []
        if spans:
            body.append(
                "<details class='spans'><summary>Execution spans"
                f" ({len(spans)})</summary><table>"
                "<tr><th>depth</th><th>name</th><th>label</th>"
                "<th>duration</th><th>tokens (in/out)</th>"
                "<th>ttfc_ms</th></tr>"
            )
            for s in spans:
                body.append(_span_to_html(s))
            body.append("</table></details>")
    body.append("</div>")
    if extras:
        for section, content in extras.items():
            body.append(f"<h2>{_html.escape(section)}</h2>")
            if isinstance(content, str):
                body.append(f"<pre>{_html.escape(content)}</pre>")
            else:
                rendered = _html.escape(
                    json.dumps(content, indent=2, default=str)
                )
                body.append(f"<pre>{rendered}</pre>")

    css = (
        "body{font-family:system-ui,sans-serif;max-width:1100px;"
        "margin:2em auto;padding:0 1em;color:#222;}"
        "table{border-collapse:collapse;margin-bottom:1em}"
        "th,td{border:1px solid #ddd;padding:6px 12px;text-align:left;"
        "vertical-align:top}"
        ".trace .turn-header{margin-top:1.4em;padding:6px 8px;"
        "background:#f0f0f5;border-left:4px solid #6b6b9a;"
        "border-radius:4px;font-weight:600}"
        ".trace .turn-header .meta{font-weight:400;color:#555;"
        "margin-left:.6em;font-size:.92em}"
        ".trace .user{background:#eef;padding:8px;"
        "margin:4px 0;border-radius:6px}"
        ".trace .agent{background:#efe;padding:8px;"
        "margin:4px 0;border-radius:6px}"
        ".trace .vars summary{color:#7a4d00}"
        ".trace details{margin:4px 0}"
        ".spans table{font-size:.9em}"
        "pre{background:#f6f6f6;padding:8px;"
        "border-radius:6px;overflow:auto}"
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8"/>'
        f"<title>{title}</title><style>{css}</style></head>"
        f"<body>{''.join(body)}</body></html>"
    )


def _turn_header_html(turn_idx: int, m: dict[str, Any]) -> str:
    bits = []
    if m.get("duration_ms") is not None:
        bits.append(f"duration {_fmt_ms(m['duration_ms'])}")
    if m.get("perceived_latency_ms") is not None:
        bits.append(f"perceived {_fmt_ms(m['perceived_latency_ms'])}")
    tokens = m.get("tokens") or {}
    if tokens.get("total"):
        bits.append(
            f"{tokens.get('input', 0)} in / "
            f"{tokens.get('output', 0)} out tokens"
        )
    sub = " · ".join(bits) if bits else ""
    return (
        f"<h3 class='turn-header'>Turn {turn_idx}"
        f"<span class='meta'> {_html.escape(sub)}</span></h3>"
    )


def _span_to_html(s: dict[str, Any]) -> str:
    esc = _html.escape
    name = esc(str(s.get("name") or "?"))
    label = esc(str(s.get("stage") or s.get("tool") or s.get("model") or ""))
    dur = _fmt_ms(s.get("duration_ms"))
    if s.get("name") == "LLM":
        t = s.get("tokens") or {}
        tok_in = t.get("input", 0)
        tok_out = t.get("output", 0)
        ttfc = t.get("ttfc_ms")
    else:
        tok_in = tok_out = ttfc = ""
    return (
        f"<tr><td>{s.get('depth', 0)}</td><td>{name}</td>"
        f"<td>{label}</td><td>{dur}</td>"
        f"<td>{tok_in}/{tok_out}</td><td>{ttfc}</td></tr>"
    )


def _entry_to_html(e: dict[str, Any]) -> str:
    kind = e["kind"]
    turn = e.get("turn", 0)
    esc = _html.escape
    if kind == "user":
        return f'<div class="user"><b>[{turn}] User:</b> {esc(e["text"])}</div>'
    if kind == "agent":
        role = esc(e.get("role", "?"))
        return (
            f'<div class="agent"><b>[{turn}] Agent ({role}):</b> '
            f"{esc(e['text'])}</div>"
        )
    if kind == "tool_call":
        args = esc(json.dumps(e.get("args"), indent=2, default=str))
        return (
            f"<details><summary>&#128295; [{turn}] Tool call "
            f"<code>{esc(str(e.get('tool')))}</code></summary>"
            f"<pre>{args}</pre></details>"
        )
    if kind == "tool_response":
        resp = esc(json.dumps(e.get("response"), indent=2, default=str))
        return (
            f"<details><summary>&#128228; [{turn}] Tool response "
            f"<code>{esc(str(e.get('tool')))}</code></summary>"
            f"<pre>{resp}</pre></details>"
        )
    if kind == "agent_transfer":
        return (
            f'<div class="system">&#128256; [{turn}] Transfer -> '
            f"<code>{esc(str(e.get('target')))}</code></div>"
        )
    if kind == "custom_payload":
        body = esc(json.dumps(e.get("payload"), indent=2, default=str))
        return (
            f"<details><summary>&#128230; [{turn}] Custom payload"
            f"</summary><pre>{body}</pre></details>"
        )
    if kind == "variable_default":
        body = esc(json.dumps(e.get("variables"), indent=2, default=str))
        return (
            f"<details class='vars'><summary>&#129529; [{turn}] "
            f"Default variables</summary><pre>{body}</pre></details>"
        )
    if kind == "variable_update":
        body = esc(json.dumps(e.get("variables"), indent=2, default=str))
        return (
            f"<details class='vars'><summary>&#9881;&#65039; [{turn}] "
            f"Variable update</summary><pre>{body}</pre></details>"
        )
    return f'<div class="system">[{turn}] {esc(kind)}</div>'
