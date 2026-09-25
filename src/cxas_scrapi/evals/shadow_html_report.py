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

"""Self-contained HTML report comparing a past call with its ShadowEval replay.

The report is written next to the audio files it references (relative paths),
so the whole artifacts directory can be zipped, shared, or served statically.
"""

import html
from typing import Any

_CSS = """
body{font-family:'Google Sans',Roboto,Arial,sans-serif;margin:24px auto;
max-width:1280px;color:#1f1f1f;padding:0 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}
.meta{color:#5f6368;font-size:13px;margin-bottom:16px}
.meta code{font-size:12px}
.chips span{display:inline-block;padding:4px 10px;border-radius:12px;
margin:0 6px 6px 0;font-size:13px;background:#f1f3f4}
.pass{background:#e6f4ea!important;color:#137333}
.fail{background:#fce8e6!important;color:#c5221f}
table{border-collapse:collapse;width:100%}
th,td{border-bottom:1px solid #e0e3e7;padding:8px;vertical-align:top;
text-align:left;font-size:13px}
th{background:#f8f9fa;position:sticky;top:0}
td.idx{color:#5f6368;white-space:nowrap}
.who{font-size:11px;font-weight:600;color:#5f6368;text-transform:uppercase;
margin-top:6px}
.txt{margin:2px 0 4px}
audio{width:100%;height:32px}
.badge{display:inline-block;font-size:11px;padding:2px 8px;border-radius:10px;
font-weight:600}
.b-past{background:#e8f0fe;color:#1967d2}.b-tts{background:#fef7e0;color:#b06000}
.b-event{background:#f1f3f4;color:#5f6368}.b-end{background:#f3e8fd;color:#8430ce}
.why{color:#5f6368;font-size:12px;margin-top:4px}
.none{color:#9aa0a6;font-style:italic}
"""

_BADGES = {
    "use_past_audio": ("b-past", "Past audio"),
    "generate_tts": ("b-tts", "TTS"),
    "event": ("b-event", "Event"),
    "end_conversation": ("b-end", "End"),
}


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _audio(path: str | None) -> str:
    if not path:
        return ""
    return f'<audio controls preload="none" src="{_esc(path)}"></audio>'


def _utterance(label: str, text: str | None, audio: str | None) -> str:
    body = _esc(text) if text else '<span class="none">(nothing said)</span>'
    return (
        f'<div class="who">{_esc(label)}</div><div class="txt">{body}</div>'
        f"{_audio(audio)}"
    )


def _past_cell(past: dict[str, Any] | None) -> str:
    if not past:
        return '<span class="none">No recorded equivalent (new turn).</span>'
    return _utterance(
        f"Caller · past turn #{past.get('turn_index')}",
        past.get("user_text"),
        past.get("user_audio"),
    ) + _utterance("Agent", past.get("agent_text"), past.get("agent_audio"))


def _new_cell(turn: dict[str, Any]) -> str:
    css, label = _BADGES.get(
        turn.get("decision", ""), ("b-event", turn.get("decision", ""))
    )
    user_audio = turn.get("user_audio")
    user_label = "Caller"
    if turn.get("decision") == "generate_tts":
        user_label = "Caller (synthesized)"
    elif turn.get("decision") == "use_past_audio":
        user_label = "Caller (recorded audio replayed)"
    cell = f'<span class="badge {css}">{_esc(label)}</span>'
    cell += _utterance(user_label, turn.get("user_text"), user_audio)
    cell += _utterance("Agent", turn.get("agent_text"), turn.get("agent_audio"))
    if turn.get("justification"):
        cell += f'<div class="why">{_esc(turn["justification"])}</div>'
    return cell


def render_shadow_html_report(data: dict[str, Any]) -> str:
    """Renders a side-by-side (past call vs. replay) HTML report.

    Args:
        data: The report payload written by `ShadowEvals` alongside the audio
            (`shadow_result.json`). Audio paths are relative to the report.

    Returns:
        The HTML document as a string.
    """
    summary = data.get("summary", {})
    passed = summary.get("passed")
    status_css = "pass" if passed else "fail"
    chips = [
        f'<span class="{status_css}">{"PASS" if passed else "FAIL"}</span>',
        f"<span>Expectations {_esc(summary.get('expectations'))}</span>",
        "<span>Past-audio turns "
        f"{_esc(summary.get('past_audio_turns'))}</span>",
        f"<span>TTS turns {_esc(summary.get('tts_turns'))}</span>",
        f"<span>Replay mode {_esc(data.get('replay_mode'))}</span>",
    ]

    exp_rows = "".join(
        f'<tr><td class="{"pass" if e.get("status") == "Met" else "fail"}">'
        f"{_esc(e.get('status'))}</td><td>{_esc(e.get('expectation'))}</td>"
        f"<td>{_esc(e.get('justification'))}</td></tr>"
        for e in data.get("expectations", [])
    )

    turn_rows = "".join(
        f'<tr><td class="idx">{_esc(t.get("sim_turn"))}</td>'
        f"<td>{_past_cell(t.get('past'))}</td><td>{_new_cell(t)}</td></tr>"
        for t in data.get("turns", [])
    )

    unreplayed = data.get("unreplayed_past_turns", [])
    unreplayed_html = ""
    if unreplayed:
        rows = "".join(
            f'<tr><td class="idx">#{_esc(p.get("turn_index"))}</td>'
            f"<td>{_past_cell(p)}</td></tr>"
            for p in unreplayed
        )
        unreplayed_html = (
            "<h2>Past turns not replayed</h2>"
            "<table><tr><th>Turn</th><th>Past call</th></tr>"
            f"{rows}</table>"
        )

    params = data.get("session_parameters") or {}
    params_html = (
        ", ".join(
            f"<code>{_esc(k)}={_esc(v)}</code>" for k, v in params.items()
        )
        or "none"
    )
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>ShadowEval – {_esc(data.get("name"))}</title><style>{_CSS}</style></head>
<body>
<h1>ShadowEval: {_esc(data.get("name"))}</h1>
<div class="meta">Past conversation
<code>{_esc(data.get("conversation_id"))}</code> on
<code>{_esc(data.get("source_app"))}</code><br>
Replayed as session <code>{_esc(data.get("session_id"))}</code> on
<code>{_esc(data.get("target_app"))}</code><br>
Session parameters: {params_html} · tool fakes:
<code>{_esc(data.get("use_tool_fakes"))}</code></div>
<div class="chips">{"".join(chips)}</div>
<h2>Expectations</h2>
<table><tr><th>Status</th><th>Expectation</th><th>Justification</th></tr>
{exp_rows}</table>
<h2>Turns</h2>
<table><tr><th>#</th><th style="width:48%">Past call</th>
<th style="width:48%">New call (replay)</th></tr>{turn_rows}</table>
{unreplayed_html}
</body></html>
"""
