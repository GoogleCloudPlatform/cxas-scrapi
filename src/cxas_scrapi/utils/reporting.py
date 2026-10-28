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


"""Utility functions for generating reports."""

import contextlib
import datetime
import glob
import json
import math
import os
import pathlib
import re
import statistics
import typing
import wave
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import jinja2
import numpy as np
import pandas as pd
import yaml

from cxas_scrapi.core import conversation_history, tools
from cxas_scrapi.core.audio_transformer import snr_to_noise_level
from cxas_scrapi.evals import naturalness
from cxas_scrapi.evals import runner as evals_runner
from cxas_scrapi.prompts import naturalness_prompts
from cxas_scrapi.utils import (
    base_components,
    eval_utils,
    gcs_utils,
    report_components,
)
from cxas_scrapi.utils.latency_parser import LatencyParser
from cxas_scrapi.utils.tracing import (
    audio_analysis,
    audio_drift,
    audio_metrics,
    audio_transcription,
)

_ORIG_OPEN = open


def _escape(text: typing.Any) -> typing.Any:
    """HTML-escape a string."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _fmt_duration(seconds: typing.Any) -> str:
    """Format duration: seconds if < 60, minutes otherwise."""
    seconds_per_minute = 60
    if seconds is None:
        return ""
    if seconds >= seconds_per_minute:
        return f"{seconds / seconds_per_minute:.1f}m"
    return f"{seconds:.1f}s"


def _join_chunk_text(
    chunks: Sequence[Mapping[str, Any]] | None,
) -> str:
    """Join the text of every chunk in an agent response.

    A single agent turn can be emitted as multiple chunks (e.g. a short
    filler said before a tool call, then the substantive answer after the
    tool returns). Rendering only the first chunk makes the turn look
    truncated even though the platform stored — and the scorer evaluated —
    the full response. Concatenate all chunk texts so the report matches.
    """
    if not chunks:
        return ""
    return " ".join(
        text
        for c in chunks
        if isinstance(c, Mapping) and (text := (c.get("text") or "").strip())
    )


def _resolve_tool_name(
    raw_name: typing.Any, tools_map: typing.Any
) -> typing.Any:
    """Resolve a full resource path to a display name."""
    if not raw_name:
        return raw_name
    # Check reverse map (resource path → display name)
    if raw_name in tools_map:
        return tools_map[raw_name]
    # Try matching by tool ID (last segment)
    tool_id = raw_name.split("/")[-1] if "/" in raw_name else raw_name
    for path, display in tools_map.items():
        if path.endswith(f"/{tool_id}"):
            return display
    # Fallback: just use last segment
    return tool_id if "/" in raw_name else raw_name


def _format_trace_line(line: typing.Any, tools_map: typing.Any) -> typing.Any:
    """Format a trace line, resolving tool IDs to display names."""
    if "Tool Call:" in line or "Tool Response:" in line:
        # Replace resource paths with display names
        for path, display in tools_map.items():
            line = line.replace(path, display)
    return line


def _get_html_head(ts: typing.Any) -> str:
    """Return the HTML head with CSS and JS."""
    css_path = os.path.join(
        os.path.dirname(__file__), "../resources/components/base/base.css"
    )
    js_path = os.path.join(
        os.path.dirname(__file__), "../resources/components/base/interaction.js"
    )
    with open(css_path, encoding="utf-8") as f:
        css = f.read()
    with open(js_path, encoding="utf-8") as f:
        js = f.read()
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Simulation Report - {ts}</title>
<style>
{css}
</style>
<script>
{js}
</script>
</head><body>
"""


def _fmt_ms(val: float | int | None) -> str:
    """Format milliseconds with thousands separator, e.g. '2,902 ms'."""
    if val is None:
        return "N/A"
    return f"{round(float(val)):,} ms"


def _pl_status_cls(pl_ms: float | None) -> str:
    """Return CSS status class ('pass', 'warn', 'fail') for PL."""
    if pl_ms is None:
        return "meta"
    if pl_ms <= 1500.0:
        return "pass"
    if pl_ms <= 2500.0:
        return "warn"
    return "fail"


def _pl_turn_drawer_cls(pl_ms: float | None) -> str:
    """Return CSS border class ('pl-fast', 'pl-warn', 'pl-slow') for a turn."""
    if pl_ms is None or pl_ms <= 1500.0:
        return "pl-fast"
    if pl_ms <= 2500.0:
        return "pl-warn"
    return "pl-slow"


def _render_turn_latency_drawer(turn_pl: dict[str, Any]) -> str:
    """Render collapsed-by-default Per-Turn Perceived Latency drawer."""
    if not isinstance(turn_pl, dict):
        return ""
    turn_num = turn_pl.get("turn_num", "?")
    pl_ms = turn_pl.get("pl_ms")
    is_silent = turn_pl.get("is_silent", False)
    drawer_cls = _pl_turn_drawer_cls(pl_ms)
    status_cls = _pl_status_cls(pl_ms)
    pl_str = "Silent (No Audio)" if is_silent else _fmt_ms(pl_ms)

    meta_parts = [str(turn_pl.get("category_label", "Turn"))]
    if turn_pl.get("filler_masked"):
        saved_str = _fmt_ms(turn_pl.get("filler_saved_ms", 0))
        meta_parts.append(f"Masked by Filler · Saved {saved_str}")
    elif not is_silent and turn_pl.get("pre_speech_summary"):
        meta_parts.append(str(turn_pl["pre_speech_summary"]))
    if turn_pl.get("bottleneck_label") and not turn_pl.get("filler_masked"):
        meta_parts.append(f"Bottleneck: {turn_pl['bottleneck_label']}")
    meta_str = " · ".join(meta_parts)

    first_audio_pct = turn_pl.get("first_audio_pct")
    fa_marker = (
        f'<div class="wf-first-audio-line" style="left:{first_audio_pct}%;" '
        f'title="First Audio: {_fmt_ms(pl_ms)}"></div>'
        if first_audio_pct is not None
        else ""
    )

    wf_rows_html = ""
    if first_audio_pct is not None:
        pin_tx = (
            "translateX(0%)"
            if first_audio_pct < 18
            else (
                "translateX(-100%)"
                if first_audio_pct > 82
                else "translateX(-50%)"
            )
        )
        pre_zone_html = (
            '<span class="wf-axis-zone wf-axis-pre">'
            "0 ms &middot; Silent Wait (Pre-Speech)</span>"
            if first_audio_pct >= 32
            else ""
        )
        post_zone_html = (
            '<span class="wf-axis-zone wf-axis-post">'
            "Post-Speech (Agent Speaking) &rarr;</span>"
            if first_audio_pct <= 68
            else ""
        )
        wf_rows_html += (
            '<div class="wf-row wf-header-row">'
            '<div class="wf-header-col">Execution Stage</div>'
            '<div class="wf-axis-track">'
            f"{pre_zone_html}"
            f'<div class="wf-fa-pin" style="left:{first_audio_pct}%;'
            f'transform:{pin_tx};">🔊 First Audio: {_fmt_ms(pl_ms)}</div>'
            '<div class="wf-first-audio-line" '
            f'style="left:{first_audio_pct}%;"></div>'
            f"{post_zone_html}"
            "</div>"
            '<div class="wf-header-col" style="text-align:right;">'
            "Duration</div>"
            "</div>\n"
        )

    wf_kind_to_seg = {
        "vad": "pl-seg-vad",
        "cb": "pl-seg-cb",
        "llm": "pl-seg-sllm",
        "tts": "pl-seg-ttfa",
        "audio_stream": "pl-seg-ttfa",
        "tool": "pl-seg-tool",
        "guardrail": "pl-seg-gap",
    }
    for idx, wf in enumerate(turn_pl.get("waterfall_spans", []), start=1):
        wf_kind = wf.get("kind", "")
        seg_cls = wf_kind_to_seg.get(wf_kind, "pl-seg-gap")
        if wf.get("is_first_audio") or wf_kind == "audio_stream":
            seg_cls = (
                "pl-seg-filler"
                if (wf_kind == "cb" or turn_pl.get("filler_masked"))
                else "pl-seg-ttfa"
            )
        if wf_kind == "audio_stream":
            seg_cls += " wf-stream-bar"
            opacity = "opacity:0.82;"
        else:
            opacity = (
                "opacity:0.6;"
                if (not wf.get("is_pre_speech") and wf_kind != "vad")
                else ""
            )
        dur_cls = (
            "fail"
            if wf.get("is_pre_speech") and wf.get("dur_ms", 0) > 1500
            else ""
        )
        left_p = wf.get("left_pct", 0)
        width_p = wf.get("width_pct", 2)
        post_left_p = wf.get("post_left_pct")
        post_width_p = wf.get("post_width_pct")
        post_bar_html = (
            f'<div class="wf-bar {seg_cls}" '
            f'style="left:{post_left_p}%;width:{post_width_p}%;opacity:0.32;" '
            'title="Post-speech / background continuation"></div>'
            if post_left_p is not None and post_width_p is not None
            else ""
        )
        wf_dur = _fmt_ms(wf.get("dur_ms", 0))
        cbs = wf.get("callbacks") or []
        if wf_kind == "cb" and cbs:
            sub_rows_html = ""
            for cb in cbs:
                cb_seq = cb.get("seq", 1)
                cb_nm = _escape(cb.get("cb_name", "callback"))
                cb_cts = cb.get("child_tools") or []
                has_det = bool(
                    cb.get("has_detailed_latency")
                    or cb.get("code_ms", 0) > 0
                    or cb.get("sandbox_overhead_ms", 0) > 0
                )
                if has_det:
                    tot_r = round(float(cb.get("dur_ms", 0)))
                    code_r = round(float(cb.get("code_ms", 0)))
                    wait_r = round(float(cb.get("ext_wait_ms", 0)))
                    sbx_r = max(0, tot_r - code_r - wait_r)
                    bd_parts = [f"code {code_r}ms"]
                    if wait_r > 0:
                        if cb_cts:
                            ct_names = ", ".join(
                                _escape(ct["name"]) for ct in cb_cts[:2]
                            )
                            if len(cb_cts) > 2:
                                ct_names += f" +{len(cb_cts) - 2}"
                            bd_parts.append(
                                f"ext_wait {wait_r}ms &rarr; {ct_names}"
                            )
                        else:
                            bd_parts.append(f"ext_wait {wait_r}ms")
                    bd_parts.append(f"sandbox {sbx_r}ms")
                    cb_extra = f" ({' + '.join(bd_parts)})"
                elif cb_cts:
                    ct_str = ", ".join(
                        f"{_escape(ct['name'])} ({round(ct['dur_ms'])}ms)"
                        for ct in cb_cts[:3]
                    )
                    if len(cb_cts) > 3:
                        ct_str += f", +{len(cb_cts) - 3} more"
                    cb_extra = f" &rarr; {ct_str}"
                else:
                    cb_extra = ""
                cb_left = cb.get("left_pct", left_p)
                cb_width = cb.get("width_pct", 1.5)
                cb_dur = _fmt_ms(cb.get("dur_ms", 0))
                sub_rows_html += (
                    '<div class="wf-row wf-sub-row">'
                    f'<div><span class="cb-seq-badge">#{cb_seq}</span> '
                    f"<code>{cb_nm}</code>"
                    f'<span class="meta">{cb_extra}</span></div>'
                    '<div class="wf-track">'
                    f'<div class="wf-bar {seg_cls}" '
                    f'style="left:{cb_left}%;width:{cb_width}%;{opacity}">'
                    f"</div>{fa_marker}</div>"
                    f'<div class="wf-dur">{cb_dur}</div>'
                    "</div>\n"
                )
            wf_rows_html += (
                '<details class="wf-cb-details">'
                '<summary class="wf-row wf-cb-summary">'
                f'<div>{idx}. <span class="pl-arrow">&#9654;</span>'
                f"<code>{_escape(wf.get('label', ''))}</code> "
                f'<span class="meta">({_escape(wf.get("detail", ""))})'
                "</span></div>"
                '<div class="wf-track">'
                f'<div class="wf-bar {seg_cls}" '
                f'style="left:{left_p}%;width:{width_p}%;{opacity}"></div>'
                f"{post_bar_html}{fa_marker}</div>"
                f'<div class="wf-dur {dur_cls}">{wf_dur}</div>'
                "</summary>"
                f'<div class="wf-cb-list">{sub_rows_html}</div>'
                "</details>\n"
            )
        else:
            wf_rows_html += (
                '<div class="wf-row">'
                f"<div>{idx}. <code>{_escape(wf.get('label', ''))}</code> "
                f'<span class="meta">({_escape(wf.get("detail", ""))})'
                "</span></div>"
                '<div class="wf-track">'
                f'<div class="wf-bar {seg_cls}" '
                f'style="left:{left_p}%;width:{width_p}%;{opacity}"></div>'
                f"{post_bar_html}{fa_marker}</div>"
                f'<div class="wf-dur {dur_cls}">{wf_dur}</div>'
                "</div>\n"
            )

    return (
        f'<details class="pl-turn-details {drawer_cls} latency-drawer">'
        f'<summary><span class="pl-arrow">&#9654;</span>⏱ '
        f"<b>Turn {turn_num} Perceived Latency: "
        f'<span class="{status_cls}">{_escape(pl_str)}</span></b> '
        f'<span class="meta">({_escape(meta_str)} — '
        "click for waterfall)</span></summary>"
        f'<div style="margin-top:6px;">{wf_rows_html}</div>'
        "</details>\n"
    )


def _render_conv_latency_drawer(
    conv_pl: dict[str, Any], run_label: str = ""
) -> str:
    """Render collapsed-by-default Per-Conversation Perceived Latency drawer."""
    if not isinstance(conv_pl, dict) or not conv_pl.get("turns"):
        return ""
    avg_ms = conv_pl.get("avg_ms", 0.0)
    p50_ms = conv_pl.get("p50_ms", 0.0)
    p90_ms = conv_pl.get("p90_ms", 0.0)
    slice_lbl = conv_pl.get("primary_slice_label", "Audio")
    filler_cnt = conv_pl.get("filler_masked_count", 0)
    total_turns = conv_pl.get("total_turns", len(conv_pl["turns"]))
    lbl_suffix = f" ({_escape(run_label)})" if run_label else ""

    rows_html = ""
    for t in conv_pl["turns"]:
        t_pl = t.get("pl_ms")
        t_cls = _pl_status_cls(t_pl)
        pl_cell = (
            '<span class="meta">Silent</span>'
            if t.get("is_silent")
            else f'<b class="{t_cls}">{_fmt_ms(t_pl)}</b>'
        )
        bd = t.get("breakdown_ms", {})
        if t.get("filler_masked"):
            mask_cell = (
                '<span class="badge pass">'
                f"Masked (Saved {_fmt_ms(t.get('filler_saved_ms', 0))})</span>"
            )
        else:
            mask_cell = _escape(t.get("bottleneck_label", "-"))
        rows_html += (
            "<tr>"
            f"<td>Turn {t.get('turn_num', '?')}</td>"
            f"<td>{_escape(t.get('category_label', ''))}</td>"
            f"<td>{pl_cell}</td>"
            f"<td>{_fmt_ms(bd.get('callback_ms', 0))}</td>"
            f"<td>{_fmt_ms(bd.get('llm_ttfc_ms', 0))}</td>"
            f"<td>{_fmt_ms(bd.get('tool_ms', 0))}</td>"
            f"<td>{_fmt_ms(bd.get('tts_ms', 0))}</td>"
            f"<td>{mask_cell}</td>"
            "</tr>\n"
        )

    avg_cls = _pl_status_cls(avg_ms)
    return (
        '<details class="pl-conv-details latency-drawer">\n'
        f'<summary><span class="pl-arrow">&#9654;</span>⏱ '
        f"<b>Perceived Latency Summary{lbl_suffix}</b> — "
        f'{_escape(slice_lbl)} Avg: <b class="{avg_cls}">{_fmt_ms(avg_ms)}</b> '
        f'| p50: <b class="{_pl_status_cls(p50_ms)}">{_fmt_ms(p50_ms)}</b> | '
        f'p90: <b class="{_pl_status_cls(p90_ms)}">{_fmt_ms(p90_ms)}</b> | '
        f"Filler Masked: <b>{filler_cnt}/{total_turns} turns</b></summary>\n"
        '<div style="margin-top:8px;">\n'
        "<table>\n"
        "<tr><th>Turn</th><th>Category</th><th>Perceived Latency</th>"
        "<th>Callbacks</th><th>LLM Inference</th><th>Tools / APIs</th>"
        "<th>TTS / Audio</th><th>Filler Masking / Bottleneck</th></tr>\n"
        f"{rows_html}"
        "</table>\n"
        "</div>\n"
        "</details>\n"
    )


def _prepare_trend_chart(
    buckets: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Calculate geometry and data points for dual-panel trend SVG chart."""
    if not buckets or not any(b.get("n") for b in buckets):
        return None
    w = 760
    h1 = 170
    gap = 26
    h2 = 44
    pad_l = 48
    pad_r = 64
    pad_t = 12

    top_p90 = max((float(b.get("p90") or 0.0)) for b in buckets)
    top = max(1.0, math.ceil(top_p90 / 1000.0))
    tick = 2.0 if top > 8 else (1.0 if top > 4 else 0.5)

    bw = (w - pad_l - pad_r) / max(1, len(buckets))

    def cx(i: int) -> float:
        return pad_l + bw * (i + 0.5)

    def y(v: float | None) -> float:
        return pad_t + (1.0 - (float(v or 0.0) / (top * 1000.0))) * h1

    nmax = max(int(b.get("n") or 1) for b in buckets)

    def y2(v: int) -> float:
        return pad_t + h1 + gap + h2 - (float(v) / float(nmax)) * h2

    grid_lines = []
    t_val = 0.0
    while t_val <= top + 1e-6:
        y_pos = round(y(t_val * 1000.0), 1)
        grid_col = "var(--axis)" if t_val == 0 else "var(--grid)"
        grid_lines.append(
            {
                "y": y_pos,
                "x1": pad_l,
                "x2": w - pad_r,
                "text_x": pad_l - 8,
                "text_y": round(y_pos + 4, 1),
                "label": f"{t_val:.1g}s",
                "stroke": grid_col,
            }
        )
        t_val += tick

    series = []
    end_y: float | None = None
    for key, col, label_name in [
        ("p90", "var(--serious)", "p90"),
        ("p50", "var(--accent)", "p50"),
    ]:
        d = ""
        prev_ok = False
        valid_indices: list[int] = []
        dots = []
        for i, b in enumerate(buckets):
            v = b.get(key)
            if v is None:
                prev_ok = False
                continue
            valid_indices.append(i)
            px = round(cx(i), 1)
            py = round(y(float(v)), 1)
            d += f"{'L' if prev_ok else 'M'}{px:.1f},{py:.1f}"
            prev_ok = True
            dots.append({"cx": px, "cy": py, "val": float(v)})
        if d:
            last_i = valid_indices[-1]
            last_v = float(buckets[last_i][key])
            ly = round(y(last_v) + 4, 1)
            if (
                label_name == "p50"
                and end_y is not None
                and abs(ly - end_y) < 14
            ):
                ly = round(end_y + 14, 1)
            end_y = ly
            series.append(
                {
                    "key": key,
                    "label": label_name,
                    "color": col,
                    "d": d,
                    "dots": dots,
                    "last_x": round(cx(last_i) + 10, 1),
                    "last_y": ly,
                    "last_val_fmt": _fmt_ms(last_v),
                }
            )

    bars = []
    cw = min(24.0, bw * 0.62)
    for i, b in enumerate(buckets):
        n_val = int(b.get("n", 0))
        yy = y2(n_val)
        h_bar = pad_t + h1 + gap + h2 - yy
        bars.append(
            {
                "x": round(cx(i) - cw / 2.0, 1),
                "y": round(yy, 1),
                "width": round(cw, 1),
                "height": round(h_bar, 1),
                "text_x": round(cx(i), 1),
                "text_y": round(pad_t + h1 + gap + h2 + 16, 1),
                "label": str(b.get("label", "")),
            }
        )

    hits = []
    for i, b in enumerate(buckets):
        lbl = str(b.get("label", f"Turn {i + 1}"))
        p50_s = _fmt_ms(b.get("p50"))
        p90_s = _fmt_ms(b.get("p90"))
        p95_s = _fmt_ms(b.get("p95"))
        n_turns = int(b.get("n", 0))
        n_calls = int(b.get("calls", 0))
        tip_title = (
            f"{lbl}&#10;p50: {p50_s}&#10;p90: {p90_s}&#10;p95: {p95_s}&#10;"
            f"Turns: {n_turns}&#10;Evals: {n_calls}"
        )
        hits.append(
            {
                "turn_num": b.get("turn_num", i + 1),
                "label": lbl,
                "p50_s": p50_s,
                "p90_s": p90_s,
                "p95_s": p95_s,
                "turns": n_turns,
                "calls": n_calls,
                "x": round(pad_l + bw * i, 1),
                "y": pad_t,
                "width": round(bw, 1),
                "height": h1 + gap + h2,
                "title": tip_title,
            }
        )

    return {
        "w": w,
        "view_h": pad_t + h1 + gap + h2 + 24,
        "pad_l": pad_l,
        "turns_label_y": pad_t + h1 + gap - 8,
        "nmax": nmax,
        "nmax_y": round(y2(nmax) + 8, 1),
        "grid_lines": grid_lines,
        "series": series,
        "bars": bars,
        "hits": hits,
    }


def _get_combined_report_template() -> jinja2.Template:
    """Load and return the Jinja2 template for combined eval reports."""
    template_path = os.path.join(
        os.path.dirname(__file__), "combined_report_template.html"
    )
    with _ORIG_OPEN(template_path, encoding="utf-8") as f:
        template_content = f.read()
    return jinja2.Template(template_content)


def _get_combined_template_module(
    extra_vars: dict[str, Any] | None = None,
) -> Any:
    """Instantiate template module with safe default context variables."""
    template = _get_combined_report_template()
    default_vars: dict[str, Any] = {
        "pct": 0,
        "passed": 0,
        "total": 0,
        "ts": "",
        "unified": [],
        "golden_results": [],
        "sim_results": [],
        "tool_results": [],
        "callback_results": [],
        "failure_groups": [],
        "g_passed": 0,
        "g_total": 0,
        "g_duration": 0,
        "s_passed": 0,
        "s_total": 0,
        "s_dur": 0,
        "t_passed": 0,
        "t_total": 0,
        "c_passed": 0,
        "c_total": 0,
        "golden_modality": "text",
        "sim_modality": "text",
        "ces_base": "",
        "sim_wall_clock_s": 0,
        "failure_patterns_html": "",
        "suite_latency_html": "",
        "suite_pl": None,
        "suite_audio": None,
        "suite_audio_html": "",
        "_fmt_duration": lambda s: f"{s:.1f}s",
        "_fmt_ms": _fmt_ms,
        "_pl_status_cls": _pl_status_cls,
        "json": json,
    }
    if extra_vars:
        default_vars.update(extra_vars)
    return template.make_module(default_vars)


def _render_trend_svg(buckets: list[dict[str, Any]]) -> str:
    """Render dual-panel SVG chart showing p50/p90 latency trend and turn
    volume.
    """
    chart = _prepare_trend_chart(buckets)
    mod = _get_combined_template_module()
    return str(mod.render_trend_svg(chart))


def _render_suite_latency_drawer(suite_pl: dict[str, Any] | None) -> str:
    """Render Suite-Level Perceived Latency & Bottleneck Analysis drawer."""
    if not isinstance(suite_pl, dict) or not suite_pl.get("primary"):
        return ""
    trend_chart = _prepare_trend_chart(suite_pl.get("turn_trend_buckets", []))
    mod = _get_combined_template_module(
        {"_fmt_ms": _fmt_ms, "_pl_status_cls": _pl_status_cls}
    )
    return str(
        mod.render_suite_latency(suite_pl, trend_chart, _fmt_ms, _pl_status_cls)
    )


def _extract_transcript_messages(r: dict[str, Any]) -> list[list[str]]:
    """Extract normalized [who, text] chat messages from evaluation result."""
    out: list[list[str]] = []

    # 1. Direct transcript list of pairs or dicts
    tr = r.get("transcript")
    if isinstance(tr, list):
        for item in tr:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                who_raw = str(item[0]).lower().strip()
                who = (
                    "caller"
                    if who_raw in ("caller", "user", "human")
                    else (
                        "agent"
                        if who_raw in ("agent", "bot", "assistant")
                        else "event"
                    )
                )
                out.append([who, str(item[1])])
            elif isinstance(item, dict):
                role = (
                    str(item.get("role") or item.get("who") or "")
                    .lower()
                    .strip()
                )
                text = str(
                    item.get("text")
                    or item.get("content")
                    or item.get("message")
                    or ""
                )
                who = (
                    "caller"
                    if role in ("caller", "user", "human")
                    else (
                        "agent"
                        if role in ("agent", "bot", "assistant")
                        else "event"
                    )
                )
                out.append([who, text])
        if out:
            return out

    # 2. String transcript
    if isinstance(tr, str) and tr.strip():
        for raw_line in tr.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line_low = line.lower()
            if line_low.startswith("caller:") or line_low.startswith("user:"):
                prefix_len = line.index(":") + 1
                out.append(["caller", line[prefix_len:].strip()])
            elif line_low.startswith("agent:") or line_low.startswith(
                "agent text:"
            ):
                prefix_len = line.index(":") + 1
                out.append(["agent", line[prefix_len:].strip()])
            elif line_low.startswith("event:"):
                prefix_len = line.index(":") + 1
                out.append(["event", line[prefix_len:].strip()])
            else:
                out.append(["event", line])
        if out:
            return out

    # 3. conversation list
    conv = r.get("conversation")
    if isinstance(conv, list):
        for item in conv:
            if isinstance(item, dict):
                role = str(item.get("role") or "").lower().strip()
                text = str(item.get("text") or item.get("content") or "")
                who = (
                    "caller"
                    if role in ("caller", "user", "human")
                    else (
                        "agent"
                        if role in ("agent", "bot", "assistant")
                        else "event"
                    )
                )
                out.append([who, text])
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                who_raw = str(item[0]).lower().strip()
                who = (
                    "caller"
                    if who_raw in ("caller", "user", "human")
                    else (
                        "agent"
                        if who_raw in ("agent", "bot", "assistant")
                        else "event"
                    )
                )
                out.append([who, str(item[1])])
        if out:
            return out

    # 4. detailed_trace list
    dt = r.get("detailed_trace")
    if isinstance(dt, list):
        for entry in dt:
            for raw_line in str(entry).splitlines():
                line = raw_line.strip()
                if (
                    not line
                    or line.startswith("Agent Text (Diag):")
                    or line.startswith("User Query:")
                ):
                    continue
                if line.startswith("Agent Text:"):
                    out.append(["agent", line[len("Agent Text:") :].strip()])
                elif line.startswith("User:"):
                    out.append(["caller", line[len("User:") :].strip()])
                elif (
                    line.startswith("Tool Call")
                    or line.startswith("Tool Response")
                    or line.startswith("Callback:")
                ):
                    out.append(["event", line])
        if out:
            return out

    # 5. turns list
    turns = r.get("turns")
    if isinstance(turns, list):
        for t in turns:
            if isinstance(t, dict):
                if t.get("user_query"):
                    out.append(["caller", str(t["user_query"])])
                elif t.get("user_text"):
                    out.append(["caller", str(t["user_text"])])
                elif t.get("user_input"):
                    out.append(["caller", str(t["user_input"])])
                if t.get("agent_response"):
                    out.append(["agent", str(t["agent_response"])])
                elif t.get("agent_text"):
                    out.append(["agent", str(t["agent_text"])])
                comparisons = t.get("comparisons")
                if isinstance(comparisons, list):
                    for c in comparisons:
                        if isinstance(c, dict):
                            c_type = c.get("type")
                            actual = c.get("actual")
                            if actual and actual != "(missed)":
                                if c_type == "text":
                                    out.append(["agent", str(actual)])
                                elif c_type == "tool_call":
                                    out.append(
                                        ["event", f"Tool Call: {actual}"]
                                    )
                                elif c_type == "transfer":
                                    out.append(["event", f"Transfer: {actual}"])
                parts = t.get("parts")
                if isinstance(parts, list):
                    for p in parts:
                        if isinstance(p, dict):
                            role = str(p.get("role") or "").lower()
                            chunks = p.get("chunks") or []
                            txt = " ".join(
                                c.get("text", "")
                                for c in chunks
                                if isinstance(c, dict)
                            )
                            who = (
                                "caller"
                                if role in ("caller", "user")
                                else ("agent" if role == "agent" else "event")
                            )
                            if txt:
                                out.append([who, txt])
    return out


def _collect_spans(node: Any) -> list[dict[str, Any]]:
    """Recursively collect all span dicts from a trace root or turn node."""
    collected: list[dict[str, Any]] = []
    if not isinstance(node, dict):
        return collected
    if "name" in node:
        collected.append(node)
    for k in ("rootSpan", "root_span", "childSpans", "child_spans", "spans"):
        val = node.get(k)
        if isinstance(val, list):
            for item in val:
                collected.extend(_collect_spans(item))
        elif isinstance(val, dict):
            collected.extend(_collect_spans(val))
    return collected


def _aggregate_suite_audio_analysis(
    sim_results: Sequence[Mapping[str, Any]] | None = None,
    golden_results: Sequence[Mapping[str, Any]] | None = None,
    audio_analysis: bool | None = None,
) -> dict[str, Any]:
    """Aggregate suite-level audio, voice quality, and language metrics."""
    all_runs: list[dict[str, Any]] = []
    if golden_results:
        for r in golden_results:
            d = dict(r)
            d["_eval_type"] = "golden"
            all_runs.append(d)
    if sim_results:
        for r in sim_results:
            d = dict(r)
            d["_eval_type"] = "sim"
            all_runs.append(d)

    total_runs = len(all_runs)
    graded_runs_count = 0
    naturalness_runs_count = 0
    nat_scores: list[float] = []
    gpa_scores: list[float] = []

    grade_counts = {"human_like": 0, "transitional": 0, "bot_like": 0}
    turn_grade_counts = {"Human-like": 0, "Transitional": 0, "Bot-like": 0}
    total_graded_turns = 0

    turn_factors_meta = [
        ("emotion", "Emotion & Tone"),
        ("pacing", "Pacing & Rhythm"),
        ("grammarStyle", "Spoken Grammar Style"),
        ("disfluency", "Natural Disfluency"),
        ("lexicalVariety", "Lexical Variety"),
        ("concision", "Concision & Spoken Length"),
        ("spokenNumbers", "Spoken Numbers & Formatting"),
        ("empathyCalibration", "Empathy Calibration"),
        ("scriptedness", "Unscripted Delivery"),
        ("turnTaking", "Turn-Taking & Barge-In"),
        ("perceivedLatency", "Perceived Latency"),
    ]
    turn_factor_scores: dict[str, list[float]] = {
        k: [] for k, _ in turn_factors_meta
    }

    conv_factors_meta = [
        ("personaConsistency", "Persona Consistency"),
        ("repetitionAvoidance", "Repetition Avoidance"),
        ("emotionalArcTracking", "Emotional Arc Tracking"),
        ("conversationalFlow", "Conversational Flow"),
    ]
    conv_factor_scores: dict[str, list[float]] = {
        k: [] for k, _ in conv_factors_meta
    }

    check_keys = [
        ("agent_voice_consistency", "Voice Consistency"),
        ("no_long_pauses", "No Long Pauses (>20s)"),
        ("agent_having_trouble", "No 'Having Trouble' Fallback"),
        ("agent_looping", "No Agent Looping"),
        ("agent_cutoff", "No Abrupt Audio Cutoff"),
    ]
    check_stats: dict[str, dict[str, int]] = {
        k: {"pass": 0, "fail": 0, "skip": 0, "total": 0} for k, _ in check_keys
    }

    lang_counts: dict[str, int] = {}
    lang_mismatches: dict[str, int] = {}
    total_switches = 0
    adhered_switches = 0
    mismatch_switches = 0

    wer_vals: list[float] = []
    high_wer_count = 0

    issues_meta = [
        ("robotic_delivery", "Robotic / Scripted Delivery", "var(--serious)"),
        ("language_mismatch", "Language Switch / Mismatch", "var(--critical)"),
        (
            "voice_drift",
            "Voice Drift / Persona Inconsistency",
            "var(--serious)",
        ),
        ("long_pauses", "Long Pauses / Dead Air", "var(--critical)"),
        ("agent_looping", "Agent Looping / Repetition", "var(--critical)"),
        ("audio_cutoff", "Abrupt Audio Cutoff / Truncation", "var(--critical)"),
        (
            "having_trouble",
            "Agent Having Trouble / Error Fallback",
            "var(--critical)",
        ),
        ("flat_emotion", "Flat Emotion or Empathy Mismatch", "var(--warn)"),
        ("unnatural_pacing", "Unnatural Pacing or Rhythm", "var(--warn)"),
        (
            "written_grammar",
            "Written-Style Grammar / Spoken Numbers",
            "var(--warn)",
        ),
        ("overlong_turns", "Overlong / Verbose Spoken Turn", "var(--warn)"),
        ("turn_taking", "Poor Turn-Taking / Barge-In", "var(--serious)"),
        ("high_wer", "High Transcription WER (>25%)", "var(--warn)"),
        ("audio_exp_failed", "Audio Expectation Not Met", "var(--serious)"),
        (
            "verdict_barged_missed",
            "Interrupted Decisive Turn",
            "var(--critical)",
        ),
        (
            "wall_of_text",
            "Wall of Text (>12s TTS / >80 words)",
            "var(--serious)",
        ),
        (
            "markdown_link_tts_leakage",
            "Markdown Link / URL TTS Leakage",
            "var(--critical)",
        ),
        (
            "prompt_or_directive_leakage",
            "Prompt / Directive Leakage",
            "var(--critical)",
        ),
        (
            "early_ack_redundancy",
            "Redundant Early Acknowledgment",
            "var(--warn)",
        ),
        (
            "subagent_transfer_verbalization",
            "Subagent Transfer Verbalization",
            "var(--warn)",
        ),
        (
            "subagent_ping_pong_loop",
            "Subagent Ping-Pong Loop",
            "var(--critical)",
        ),
        (
            "ignored_user",
            "Ignored Human Escalation Request",
            "var(--critical)",
        ),
        (
            "caller_requested_human",
            "Caller Requested Human Agent",
            "var(--warn)",
        ),
        ("tts_cache_seam", "TTS Cache Vintage Seam", "var(--serious)"),
        ("loudness_jump", "Loudness Jump (>3.0 dB LUFS)", "var(--serious)"),
        ("midturn_voice_seam", "Mid-Turn Voice Pitch Seam", "var(--critical)"),
        ("voice_jump", "Voice Jump at a Render Join", "var(--serious)"),
        ("audio_truncation", "Audio Playback Truncation", "var(--critical)"),
    ]
    issue_counts: dict[str, int] = {k: 0 for k, _, _ in issues_meta}
    issue_dict: dict[str, tuple[str, str]] = {
        k: (lbl, col) for k, lbl, col in issues_meta
    }
    issue_short_dict: dict[str, str] = {
        "language_mismatch": "Lang mismatch",
        "voice_drift": "Voice drift",
        "long_pauses": "Dead air",
        "agent_looping": "Looping",
        "audio_cutoff": "Cutoff",
        "having_trouble": "Trouble",
        "flat_emotion": "Flat emotion",
        "unnatural_pacing": "Pacing",
        "written_grammar": "Grammar",
        "overlong_turns": "Verbose",
        "turn_taking": "Barge-in",
        "high_wer": "High WER",
        "audio_exp_failed": "Exp failed",
        "verdict_barged_missed": "Barged verdict",
        "wall_of_text": "Wall of text",
        "markdown_link_tts_leakage": "TTS link leak",
        "prompt_or_directive_leakage": "Prompt leak",
        "early_ack_redundancy": "Early ack",
        "subagent_transfer_verbalization": "Router leak",
        "subagent_ping_pong_loop": "Ping-pong",
        "ignored_user": "Ignored user",
        "caller_requested_human": "Asked human",
        "tts_cache_seam": "TTS seam",
        "loudness_jump": "Loudness jump",
        "midturn_voice_seam": "Voice seam",
        "voice_jump": "Voice jump",
        "audio_truncation": "Truncation",
    }

    suite_barge_turns = 0
    suite_total_turns = 0
    suite_total_llm_turns = 0
    sessions_with_barge_in = 0

    suite_measured_acoustic_runs = 0
    suite_clean_acoustic_runs = 0
    suite_seam_runs = 0
    suite_loud_runs = 0
    suite_midturn_runs = 0
    suite_jump_runs = 0
    suite_jump_turns = 0
    suite_join_runs = 0
    suite_trunc_runs = 0
    suite_lufs_means: list[float] = []
    suite_rate_means: list[float] = []
    suite_f0_sds: list[float] = []
    suite_max_seam_st = 0.0

    run_rows: list[dict[str, Any]] = []
    eval_run_indices: dict[tuple[str, str], int] = {}
    spanish_words = {
        "hola",
        "gracias",
        "por",
        "favor",
        "español",
        "necesito",
        "ayuda",
        "cuenta",
        "internet",
        "servicio",
        "buenos",
        "dias",
        "días",
        "tardes",
        "noches",
    }

    for idx, r in enumerate(all_runs):
        is_graded = False
        run_issues: list[str] = []

        nat_details = r.get("naturalness_details")
        nat_score: float | None = None
        nat_label: str | None = None

        if isinstance(nat_details, dict):
            is_graded = True
            raw_score = nat_details.get("overall_score")
            if raw_score is not None:
                with contextlib.suppress(ValueError, TypeError):
                    nat_score = float(raw_score)
            nat_label = nat_details.get("overall_label")

            # Collect factor averages if explicitly provided
            if isinstance(nat_details.get("factor_averages"), dict):
                for q, s in nat_details["factor_averages"].items():
                    if s is not None:
                        try:
                            s_val = float(s)
                            if q not in turn_factor_scores:
                                turn_factor_scores[q] = []
                            turn_factor_scores[q].append(s_val)
                        except (ValueError, TypeError):
                            pass

            for t in nat_details.get("turns", []):
                t_lbl = t.get("label")
                if t_lbl in turn_grade_counts:
                    turn_grade_counts[t_lbl] += 1
                    total_graded_turns += 1
                for f in t.get("factors", []):
                    q = f.get("quality")
                    s = f.get("score")
                    if q and s is not None:
                        try:
                            s_val = float(s)
                            if q not in turn_factor_scores:
                                turn_factor_scores[q] = []
                            if not nat_details.get("factor_averages"):
                                turn_factor_scores[q].append(s_val)
                            if q == "scriptedness" and s_val <= 2:
                                run_issues.append("robotic_delivery")
                            elif (
                                q in ("emotion", "empathyCalibration")
                                and s_val <= 2
                            ):
                                run_issues.append("flat_emotion")
                            elif q == "pacing" and s_val <= 2:
                                run_issues.append("unnatural_pacing")
                            elif (
                                q in ("grammarStyle", "spokenNumbers")
                                and s_val <= 2
                            ):
                                run_issues.append("written_grammar")
                            elif q == "concision" and s_val <= 2:
                                run_issues.append("overlong_turns")
                            elif (
                                q in ("turnTaking", "disfluency") and s_val <= 2
                            ):
                                run_issues.append("turn_taking")
                            elif q == "perceivedLatency" and s_val <= 2:
                                run_issues.append("long_pauses")
                        except (ValueError, TypeError):
                            pass

            for f in nat_details.get("conversation_factors", []):
                q = f.get("quality")
                s = f.get("score")
                if q and s is not None:
                    try:
                        s_val = float(s)
                        if q not in conv_factor_scores:
                            conv_factor_scores[q] = []
                        conv_factor_scores[q].append(s_val)
                        if q == "personaConsistency" and s_val <= 2:
                            run_issues.append("voice_drift")
                        elif q == "repetitionAvoidance" and s_val <= 2:
                            run_issues.append("agent_looping")
                        elif q == "emotionalArcTracking" and s_val <= 2:
                            run_issues.append("flat_emotion")
                        elif q == "conversationalFlow" and s_val <= 2:
                            run_issues.append("turn_taking")
                    except (ValueError, TypeError):
                        pass

            for reason in nat_details.get("failure_reasons", []):
                r_low = reason.lower()
                if "latency" in r_low or "slow" in r_low:
                    run_issues.append("long_pauses")
                if "language" in r_low:
                    run_issues.append("language_mismatch")

        if nat_score is None and r.get("naturalness"):
            raw_nat = str(r["naturalness"])
            m_s = re.search(r"(\d+(\.\d+)?)", raw_nat)
            if m_s:
                nat_score = float(m_s.group(1))
                is_graded = True
            if not nat_label and r.get("naturalness_label"):
                nat_label = str(r["naturalness_label"])

        grade_key = "ungraded"
        grade_label = "Ungraded"
        if nat_score is not None:
            nat_scores.append(nat_score)
            naturalness_runs_count += 1
            if nat_score >= 4.0 or nat_label == "Human-like":
                grade_key = "human_like"
                grade_label = "Human-like"
                gpa_scores.append(4.0)
            elif nat_score >= 2.5 or nat_label == "Transitional":
                grade_key = "transitional"
                grade_label = "Transitional"
                gpa_scores.append(3.0 if nat_score >= 3.5 else 2.0)
            else:
                grade_key = "bot_like"
                grade_label = "Bot-like"
                gpa_scores.append(1.0 if nat_score >= 1.5 else 0.0)
                run_issues.append("robotic_delivery")
            grade_counts[grade_key] += 1

        # Language Detection & Language Switch
        sparams = (
            r.get("session_parameters", {})
            if isinstance(r.get("session_parameters"), dict)
            else {}
        )
        vconfig = (
            r.get("voice_config", {})
            if isinstance(r.get("voice_config"), dict)
            else {}
        )
        raw_lang = (
            r.get("language")
            or r.get("routed_language")
            or vconfig.get("language_code")
            or sparams.get("language")
            or sparams.get("lang")
            or sparams.get("dfcx_lang")
            or sparams.get("language_code")
            or sparams.get("locale")
            or sparams.get("preferred_language")
            or sparams.get("target_language")
            or ""
        )
        raw_lang_str = str(raw_lang).strip().lower()
        if raw_lang_str.startswith("es") or "spanish" in raw_lang_str:
            routed_lang = "Spanish"
        elif raw_lang_str.startswith("fr") or "french" in raw_lang_str:
            routed_lang = "French"
        elif raw_lang_str.startswith("de") or "german" in raw_lang_str:
            routed_lang = "German"
        else:
            routed_lang = "English"

        text_corpus = " ".join(
            [
                str(r.get("name", "")),
                str(r.get("detailed_trace", "")),
                str(r.get("transcript", "")),
                str(r.get("step_details", "")),
                str(r.get("expectation_details", "")),
            ]
        ).lower()

        has_spanish = any(w in text_corpus for w in spanish_words) or any(
            c in text_corpus for c in "áéíóúñ¿¡"
        )
        sw_field = r.get("language_switch")
        is_lang_switch = bool(
            sw_field
            or "language switch" in text_corpus
            or "switch to spanish" in text_corpus
            or "switch to english" in text_corpus
            or "bilingual" in text_corpus
            or "habla español" in text_corpus
        )

        is_mismatch = bool(
            r.get("language_mismatch") is True
            or (
                isinstance(sw_field, dict)
                and (
                    sw_field.get("adhered") is False
                    or sw_field.get("mismatch") is True
                )
            )
        )

        for exp in r.get("expectation_details") or []:
            if isinstance(exp, dict):
                e_txt = (
                    str(exp.get("expectation", ""))
                    + " "
                    + str(exp.get("justification", ""))
                ).lower()
                e_stat = str(exp.get("status", "")).lower()
                if any(
                    k in e_txt
                    for k in (
                        "language",
                        "spanish",
                        "english",
                        "switch",
                        "bilingual",
                        "idioma",
                    )
                ) and any(
                    bad in e_stat for bad in ("not met", "fail", "incomplete")
                ):
                    is_mismatch = True

        for step in r.get("step_details") or []:
            if isinstance(step, dict):
                s_txt = (
                    str(step.get("goal", ""))
                    + " "
                    + str(step.get("justification", ""))
                ).lower()
                s_stat = str(step.get("status", "")).lower()
                if any(
                    k in s_txt
                    for k in (
                        "language",
                        "spanish",
                        "english",
                        "switch",
                        "bilingual",
                    )
                ) and any(bad in s_stat for bad in ("fail", "incomplete")):
                    is_mismatch = True

        lang_name = routed_lang
        if routed_lang == "English" and has_spanish and not is_lang_switch:
            lang_name = "Spanish"

        lang_counts[lang_name] = lang_counts.get(lang_name, 0) + 1
        if is_mismatch:
            lang_mismatches[lang_name] = lang_mismatches.get(lang_name, 0) + 1
            run_issues.append("language_mismatch")

        if is_lang_switch:
            total_switches += 1
            if is_mismatch:
                mismatch_switches += 1
            else:
                adhered_switches += 1

        # Multimodal Audio Checks
        audio_checks_map = (
            r.get("audio_analysis", {})
            if isinstance(r.get("audio_analysis"), dict)
            else {}
        )
        checks_passed = 0
        checks_total = 0
        for ck, _ in check_keys:
            val = audio_checks_map.get(ck)
            if val is not None:
                is_graded = True
                checks_total += 1
                res = str(
                    val.get("result", "") if isinstance(val, dict) else val
                ).upper()
                if "PASS" in res:
                    check_stats[ck]["pass"] += 1
                    checks_passed += 1
                elif "FAIL" in res:
                    check_stats[ck]["fail"] += 1
                    if ck == "agent_voice_consistency":
                        run_issues.append("voice_drift")
                    elif ck == "no_long_pauses":
                        run_issues.append("long_pauses")
                    elif ck == "agent_having_trouble":
                        run_issues.append("having_trouble")
                    elif ck == "agent_looping":
                        run_issues.append("agent_looping")
                    elif ck == "agent_cutoff":
                        run_issues.append("audio_cutoff")
                else:
                    check_stats[ck]["skip"] += 1
                check_stats[ck]["total"] += 1

        if r.get("audio_expectations"):
            is_graded = True
        for exp in r.get("expectation_details") or []:
            if isinstance(exp, dict) and exp.get("requires_audio_paths"):
                is_graded = True
                if str(exp.get("status", "")).lower() in ("not met", "fail"):
                    run_issues.append("audio_exp_failed")

        # WER
        wer_val = (
            r.get("wer") or r.get("overall_wer") or r.get("average_turn_wer")
        )
        if wer_val is not None:
            try:
                w_f = float(wer_val)
                wer_vals.append(w_f)
                is_graded = True
                if w_f > 0.25:
                    high_wer_count += 1
                    run_issues.append("high_wer")
            except (ValueError, TypeError):
                pass

        # Extract intent
        val = (
            r.get("file_name")
            or r.get("file")
            or r.get("source_file")
            or r.get("eval_file")
            or r.get("intent")
            or r.get("name")
        )
        run_intent = pathlib.Path(str(val)).stem if val else "unknown"

        # Deterministic Phase 1 Extractor Signals
        run_barge_turns = 0
        has_barge_in = False
        has_verdict_barged_missed = False
        run_max_tts_ms = 0.0
        run_agent_speech_ms = 0.0
        agent_utterances: list[str] = []
        caller_utterances: list[str] = []

        turn_traces = r.get("turn_traces") or []
        turn_tts_cache_types: list[str] = []
        for t in turn_traces:
            if not isinstance(t, dict):
                continue
            all_spans = _collect_spans(t)

            turn_cached: bool | None = None
            for span in all_spans:
                s_name = str(span.get("name", "")).upper()
                s_attrs = span.get("attributes") or {}
                if (
                    s_name == "TTS"
                    or "time to first audio (ms)" in s_attrs
                    or "cache hit" in s_attrs
                    or "cached" in s_attrs
                ):
                    c_hit = s_attrs.get("cache hit")
                    if c_hit is not None:
                        turn_cached = bool(c_hit)
                    elif s_attrs.get("cached") is not None:
                        turn_cached = bool(s_attrs.get("cached"))
                    elif span.get("cached") is not None:
                        turn_cached = bool(span.get("cached"))
                    elif s_attrs.get("time to first audio (ms)") is not None:
                        with contextlib.suppress(ValueError, TypeError):
                            turn_cached = (
                                float(s_attrs["time to first audio (ms)"])
                                < 50.0
                            )
            if turn_cached is not None:
                turn_tts_cache_types.append(
                    "cached" if turn_cached else "fresh"
                )
            else:
                turn_tts_cache_types.append("")

            t_interrupted = bool(
                t.get("interrupted")
                or t.get("agent_interrupted")
                or t.get("vad_barge_in")
                or t.get("barge_in")
            )
            for msg in t.get("messages") or []:
                if (
                    isinstance(msg, str)
                    and "agent speaking was interrupted" in msg.lower()
                ):
                    t_interrupted = True
                    break
                elif isinstance(msg, dict):
                    m_text = str(
                        msg.get("text") or msg.get("message") or ""
                    ).lower()
                    if "agent speaking was interrupted" in m_text:
                        t_interrupted = True
                        break

            for span in all_spans:
                s_name = str(span.get("name", "")).lower()
                s_attrs = span.get("attributes") or {}
                if (
                    "interruption" in s_name
                    or s_attrs.get("barge-in")
                    or s_attrs.get("barge_in")
                    or s_attrs.get("interrupted")
                ):
                    t_interrupted = True
                    break

            if t_interrupted:
                run_barge_turns += 1
                has_barge_in = True
                has_decisive_action = bool(
                    t.get("tool_name")
                    or t.get("tool_call")
                    or t.get("verdict_barged_missed")
                    or any(
                        "tool" in str(s.get("name", "")).lower()
                        for s in all_spans
                    )
                )
                if (
                    has_decisive_action
                    or t.get("verdict_barged_missed")
                    or r.get("verdict_barged_missed")
                ):
                    has_verdict_barged_missed = True

            # Track TTS duration per turn
            turn_tts_ms = float(t.get("tts_ms") or 0.0)
            for span in all_spans:
                s_name = str(span.get("name", "")).upper()
                s_attrs = span.get("attributes") or {}
                dur_ms = None
                if (
                    s_name == "TTS"
                    or "audio duration (ms)" in s_attrs
                    or "audio_duration_ms" in s_attrs
                ):
                    raw_dur_ms = s_attrs.get(
                        "audio duration (ms)"
                    ) or s_attrs.get("audio_duration_ms")
                    if raw_dur_ms is not None:
                        with contextlib.suppress(ValueError, TypeError):
                            dur_ms = float(raw_dur_ms)
                    if dur_ms is None:
                        dur = span.get("duration", "")
                        if isinstance(dur, str) and dur.endswith("s"):
                            with contextlib.suppress(ValueError):
                                dur_ms = float(dur[:-1]) * 1000.0
                        elif isinstance(dur, (int, float)):
                            dur_ms = float(dur) * 1000.0
                if dur_ms is not None and dur_ms > 0:
                    turn_tts_ms = max(turn_tts_ms, dur_ms)

            if turn_tts_ms > 0:
                run_agent_speech_ms += turn_tts_ms
                run_max_tts_ms = max(run_max_tts_ms, turn_tts_ms)
                if turn_tts_ms > 12000.0:
                    run_issues.append("wall_of_text")

            for part in t.get("parts") or []:
                role = part.get("role")
                chunks = part.get("chunks") or []
                text = " ".join(
                    c.get("text", "") for c in chunks if isinstance(c, dict)
                )
                if not text and isinstance(part.get("text"), str):
                    text = part["text"]
                if role == "agent" and text:
                    agent_utterances.append(text)
                elif role in ("user", "caller") and text:
                    caller_utterances.append(text)

        if r.get("max_tts_ms"):
            with contextlib.suppress(ValueError, TypeError):
                run_max_tts_ms = max(run_max_tts_ms, float(r["max_tts_ms"]))
        if r.get("agent_speech_ms"):
            with contextlib.suppress(ValueError, TypeError):
                run_agent_speech_ms = max(
                    run_agent_speech_ms, float(r["agent_speech_ms"])
                )
        if run_max_tts_ms > 12000.0:
            run_issues.append("wall_of_text")

        if r.get("interruption_signal") or r.get("barge_in"):
            has_barge_in = True
            if run_barge_turns == 0:
                run_barge_turns = 1

        trace_has_seam = False
        prev_annotated_type = ""
        for ctype in turn_tts_cache_types:
            if ctype:
                if prev_annotated_type and ctype != prev_annotated_type:
                    trace_has_seam = True
                    break
                prev_annotated_type = ctype

        # PR #506 Barge-In and VAD evaluation fields
        barge_in_details: list[dict[str, Any]] = []
        for s_idx, step in enumerate(r.get("step_details") or []):
            if not isinstance(step, dict):
                continue
            s_barge_ms = step.get("barge_in_after_ms")
            s_interrupted = bool(step.get("agent_interrupted"))
            s_vad_barge = bool(step.get("vad_barge_in"))
            s_vad_skip = step.get("vad_skip_reason")
            s_awareness = bool(step.get("barge_in_awareness_injected"))
            s_meta = step.get("barge_in_metadata")
            played_ms = None
            total_ms = None
            if s_meta is not None:
                if isinstance(s_meta, dict):
                    if s_meta.get("interrupted"):
                        s_interrupted = True
                    played_ms = s_meta.get("played_audio_duration_ms")
                    total_ms = s_meta.get("total_audio_duration_ms")
                elif hasattr(s_meta, "interrupted"):
                    if s_meta.interrupted:
                        s_interrupted = True
                    played_ms = getattr(
                        s_meta, "played_audio_duration_ms", None
                    )
                    total_ms = getattr(s_meta, "total_audio_duration_ms", None)

            if s_interrupted or s_vad_barge:
                has_barge_in = True
                run_barge_turns += 1

            if (
                s_barge_ms is not None
                or s_interrupted
                or s_vad_barge
                or s_vad_skip
                or s_awareness
                or s_meta is not None
            ):
                barge_in_details.append(
                    {
                        "step": step.get("turn") or f"Step {s_idx + 1}",
                        "barge_in_after_ms": s_barge_ms,
                        "agent_interrupted": s_interrupted,
                        "vad_barge_in": s_vad_barge,
                        "vad_skip_reason": str(s_vad_skip)
                        if s_vad_skip
                        else None,
                        "barge_in_awareness_injected": s_awareness,
                        "played_audio_duration_ms": (
                            round(float(played_ms), 1)
                            if played_ms is not None
                            else None
                        ),
                        "total_audio_duration_ms": (
                            round(float(total_ms), 1)
                            if total_ms is not None
                            else None
                        ),
                    }
                )

        if r.get("agent_interrupted") or r.get("vad_barge_in"):
            has_barge_in = True
            if run_barge_turns == 0:
                run_barge_turns = 1

        # Acoustic Signal & Voice Drift Metrics
        a_metrics = r.get("audio_metrics") or r.get("voice_metrics")
        if not a_metrics:
            sess_id = r.get("session_id")
            if sess_id:
                local_wavs = []
                agent_paths = r.get("agent_audio_paths")
                if isinstance(agent_paths, dict):
                    local_wavs = [
                        p
                        for _, p in sorted(agent_paths.items())
                        if os.path.exists(p)
                    ]
                if not local_wavs:
                    candidates = sorted(
                        glob.glob(f"/tmp/scrapi_evals/*{sess_id}*/*agent*.wav")
                        + glob.glob(f".cxas/audio/{sess_id}/*agent*.wav")
                    )
                    local_wavs = [p for p in candidates if os.path.exists(p)]
                if local_wavs:
                    try:
                        call, turns = audio_metrics.measure_call(local_wavs)
                        call["conversation_id"] = sess_id
                        r["audio_metrics"] = {"call": call, "turns": turns}
                        r["audio_drift"] = audio_drift.flag_drift(turns)
                    except Exception:
                        try:
                            fallback_turns = []
                            total_dur = 0.0
                            for w_idx, wpath in enumerate(local_wavs):
                                with wave.open(wpath, "rb") as wf:
                                    nf = wf.getnframes()
                                    fr = wf.getframerate()
                                    dur = nf / float(fr) if fr else 0.0
                                    total_dur += dur
                                    raw = wf.readframes(nf)
                                    samp = np.frombuffer(raw, dtype=np.int16)
                                    rms = (
                                        float(
                                            np.sqrt(
                                                np.mean(
                                                    samp.astype(np.float64) ** 2
                                                )
                                            )
                                        )
                                        if len(samp)
                                        else 0.0
                                    )
                                    db = (
                                        round(20 * math.log10(rms / 32768.0), 1)
                                        if rms > 0
                                        else -100.0
                                    )
                                    fallback_turns.append(
                                        {
                                            "turn_index": w_idx,
                                            "duration_s": round(dur, 2),
                                            "lufs": db,
                                            "flags": [],
                                        }
                                    )
                            if fallback_turns:
                                call = {
                                    "conversation_id": sess_id,
                                    "duration_s": round(total_dur, 2),
                                    "lufs_mean": round(
                                        statistics.mean(
                                            [t["lufs"] for t in fallback_turns]
                                        ),
                                        1,
                                    ),
                                    "flags": [],
                                    "drift_reasons": [],
                                }
                                r["audio_metrics"] = {
                                    "call": call,
                                    "turns": fallback_turns,
                                }
                                r["audio_drift"] = {
                                    "flags": [],
                                    "drift_reasons": [],
                                }
                        except Exception:
                            pass
                    if isinstance(r.get("runs"), list):
                        for sub_r in r["runs"]:
                            if isinstance(sub_r, dict) and not sub_r.get(
                                "audio_metrics"
                            ):
                                sub_r["audio_metrics"] = r.get("audio_metrics")
                                sub_r["audio_drift"] = r.get("audio_drift")
                    a_metrics = r.get("audio_metrics")

        call_metrics: dict[str, Any] = {}
        turn_metrics_rows: list[dict[str, Any]] = []
        if isinstance(a_metrics, tuple) and len(a_metrics) == 2:
            call_metrics = (
                dict(a_metrics[0]) if isinstance(a_metrics[0], dict) else {}
            )
            turn_metrics_rows = (
                list(a_metrics[1]) if isinstance(a_metrics[1], list) else []
            )
        elif isinstance(a_metrics, dict):
            if "call" in a_metrics and isinstance(a_metrics["call"], dict):
                call_metrics = dict(a_metrics["call"])
                turn_metrics_rows = list(a_metrics.get("turns") or [])
            else:
                call_metrics = dict(a_metrics)
                turn_metrics_rows = list(a_metrics.get("turns") or [])

        a_drift = r.get("audio_drift")
        drift_rows: list[dict[str, Any]] = []
        if isinstance(a_drift, list):
            drift_rows = [dict(x) for x in a_drift if isinstance(x, dict)]
        elif isinstance(a_drift, dict):
            if "turns" in a_drift and isinstance(a_drift["turns"], list):
                drift_rows = [
                    dict(x) for x in a_drift["turns"] if isinstance(x, dict)
                ]
            elif "rows" in a_drift and isinstance(a_drift["rows"], list):
                drift_rows = [
                    dict(x) for x in a_drift["rows"] if isinstance(x, dict)
                ]
            elif "flagged" in a_drift and isinstance(a_drift["flagged"], list):
                drift_rows = [
                    dict(x) for x in a_drift["flagged"] if isinstance(x, dict)
                ]

        run_acoustic_flags: list[str] = []
        if trace_has_seam:
            run_acoustic_flags.append("SEAM")

        for f in call_metrics.get("flags") or []:
            f_str = str(f).upper()
            if f_str not in run_acoustic_flags:
                run_acoustic_flags.append(f_str)

        for dr in drift_rows:
            for f in dr.get("flags") or []:
                f_str = str(f).upper()
                if f_str not in run_acoustic_flags:
                    run_acoustic_flags.append(f_str)

        if "SEAM" in run_acoustic_flags:
            run_issues.append("tts_cache_seam")

        loud_thresh = getattr(
            audio_drift,
            "LOUD_STEP_LUFS",
            getattr(audio_drift, "DEFAULT_LOUD_DB", 3.0),
        )
        has_loud = "LOUD" in run_acoustic_flags
        if not has_loud:
            adj_d = call_metrics.get("max_adj_dlufs")
            if (
                adj_d is not None
                and isinstance(adj_d, (int, float))
                and adj_d > loud_thresh
            ):
                has_loud = True
                run_acoustic_flags.append("LOUD")
        if has_loud:
            run_issues.append("loudness_jump")

        seam_mild = getattr(
            audio_metrics,
            "SEAM_MILD_ST",
            getattr(audio_metrics, "SEAM_DRIFT_ST", 7.0),
        )
        has_midturn = (
            "MIDTURN" in run_acoustic_flags
            or "MIDDRIFT" in run_acoustic_flags
            or "INTRA_TURN_SPEAKER_CHANGE" in run_acoustic_flags
            or "INTRA_TURN_VOICE_DRIFT" in run_acoustic_flags
            or bool(call_metrics.get("seam_speaker_change_turns"))
            or bool(call_metrics.get("seam_drift_turns"))
        )
        if not has_midturn:
            seam_st = call_metrics.get("max_seam_st")
            if (
                seam_st is not None
                and isinstance(seam_st, (int, float))
                and seam_st >= seam_mild
            ):
                has_midturn = True
                run_acoustic_flags.append("MIDTURN")
        if has_midturn:
            run_issues.append("midturn_voice_seam")

        if "TRUNC" in run_acoustic_flags:
            run_issues.append("audio_truncation")

        # JUMP: the voice stepped up louder and higher where a turn's next
        # rendered part started (audio_drift); only turns sent in several
        # parts are measured, so count the runs that had a join at all.
        run_jump_turns = sum(
            1 for dr in drift_rows if "JUMP" in (dr.get("flags") or [])
        )
        run_has_joins = any(dr.get("joins") for dr in drift_rows)
        if run_jump_turns:
            run_issues.append("voice_jump")

        acoustic_turns: list[dict[str, Any]] = []
        drift_by_turn: dict[int, dict[str, Any]] = {}
        for d in drift_rows:
            t_num = d.get("turn")
            if t_num is not None:
                with contextlib.suppress(ValueError, TypeError):
                    drift_by_turn[int(t_num)] = d

        for tm in turn_metrics_rows:
            t_num = tm.get("turn", 0)
            dr = drift_by_turn.get(t_num, {})
            t_flags = list(dr.get("flags") or [])
            t_tts = dr.get("tts") or tm.get("tts") or ""
            acoustic_turns.append(
                {
                    "turn": t_num,
                    "dur_s": (
                        round(float(tm["dur_s"]), 2)
                        if tm.get("dur_s") is not None
                        else None
                    ),
                    "lufs": (
                        round(float(tm["lufs"]), 1)
                        if tm.get("lufs") is not None
                        and not math.isnan(tm["lufs"])
                        else None
                    ),
                    "artic_rate": (
                        round(float(tm["artic_rate"]), 1)
                        if tm.get("artic_rate") is not None
                        and not math.isnan(tm["artic_rate"])
                        else None
                    ),
                    "pitch_hz": (
                        round(float(tm["f0_median_hz"]), 1)
                        if tm.get("f0_median_hz") is not None
                        and not math.isnan(tm["f0_median_hz"])
                        else None
                    ),
                    "max_seam_st": (
                        round(float(tm["max_seam_st"]), 1)
                        if tm.get("max_seam_st") is not None
                        else None
                    ),
                    "tts": t_tts,
                    "flags": t_flags,
                }
            )

        if not turn_metrics_rows and turn_tts_cache_types:
            for t_idx, tts_type in enumerate(turn_tts_cache_types, 1):
                if tts_type:
                    acoustic_turns.append(
                        {
                            "turn": t_idx,
                            "dur_s": None,
                            "lufs": None,
                            "artic_rate": None,
                            "pitch_hz": None,
                            "max_seam_st": None,
                            "tts": tts_type,
                            "flags": [],
                        }
                    )

        drift_reasons: list[str] = []
        for dr in drift_rows:
            desc = audio_drift.describe(dr)
            if desc:
                drift_reasons.append(f"Turn {dr.get('turn', '?')}: {desc}")

        has_tts_spans = any(bool(x) for x in turn_tts_cache_types)
        is_measured = bool(
            call_metrics or drift_rows or turn_metrics_rows or has_tts_spans
        )
        if is_measured:
            is_graded = True
            suite_measured_acoustic_runs += 1
            has_acoustic_defect = bool(
                "tts_cache_seam" in run_issues
                or "loudness_jump" in run_issues
                or "midturn_voice_seam" in run_issues
                or "audio_truncation" in run_issues
                or "voice_jump" in run_issues
            )
            if not has_acoustic_defect:
                suite_clean_acoustic_runs += 1
            if "tts_cache_seam" in run_issues:
                suite_seam_runs += 1
            if "loudness_jump" in run_issues:
                suite_loud_runs += 1
            if "midturn_voice_seam" in run_issues:
                suite_midturn_runs += 1
            if "audio_truncation" in run_issues:
                suite_trunc_runs += 1
            if run_has_joins:
                suite_join_runs += 1
            if run_jump_turns:
                suite_jump_runs += 1
                suite_jump_turns += run_jump_turns

            if call_metrics.get("lufs_mean") is not None:
                suite_lufs_means.append(float(call_metrics["lufs_mean"]))
            if call_metrics.get("rate_mean") is not None:
                suite_rate_means.append(float(call_metrics["rate_mean"]))
            if call_metrics.get("f0_median_sd_st") is not None:
                suite_f0_sds.append(float(call_metrics["f0_median_sd_st"]))
            if call_metrics.get("max_seam_st") is not None:
                suite_max_seam_st = max(
                    suite_max_seam_st, float(call_metrics["max_seam_st"])
                )

        acoustic_detail = {
            "measured": is_measured,
            "lufs_mean": (
                round(float(call_metrics["lufs_mean"]), 2)
                if call_metrics.get("lufs_mean") is not None
                else None
            ),
            "lufs_sd": (
                round(float(call_metrics["lufs_sd"]), 2)
                if call_metrics.get("lufs_sd") is not None
                else None
            ),
            "max_adj_dlufs": (
                round(float(call_metrics["max_adj_dlufs"]), 2)
                if call_metrics.get("max_adj_dlufs") is not None
                else None
            ),
            "rate_mean": (
                round(float(call_metrics["rate_mean"]), 2)
                if call_metrics.get("rate_mean") is not None
                else None
            ),
            "rate_cov_pct": (
                round(float(call_metrics["rate_cov_pct"]), 1)
                if call_metrics.get("rate_cov_pct") is not None
                else None
            ),
            "f0_median_sd_st": (
                round(float(call_metrics["f0_median_sd_st"]), 2)
                if call_metrics.get("f0_median_sd_st") is not None
                else None
            ),
            "max_seam_st": (
                round(float(call_metrics["max_seam_st"]), 1)
                if call_metrics.get("max_seam_st") is not None
                else None
            ),
            "flags": sorted(list(set(run_acoustic_flags))),
            "turns": acoustic_turns,
            "drift_reasons": drift_reasons,
            "barge_in_details": barge_in_details,
        }

        if r.get("verdict_barged_missed"):
            has_verdict_barged_missed = True

        suite_barge_turns += run_barge_turns
        if has_barge_in:
            sessions_with_barge_in += 1
            is_graded = True

        if has_verdict_barged_missed:
            run_issues.append("verdict_barged_missed")

        if not agent_utterances and isinstance(r.get("detailed_trace"), list):
            for entry in r["detailed_trace"]:
                s_entry = str(entry)
                if s_entry.startswith("Agent Text:") or s_entry.startswith(
                    "Agent:"
                ):
                    agent_utterances.append(s_entry.split(":", 1)[1].strip())
                elif s_entry.startswith("User:") or s_entry.startswith(
                    "Caller:"
                ):
                    caller_utterances.append(s_entry.split(":", 1)[1].strip())

        if not agent_utterances and isinstance(r.get("transcript"), str):
            for line in r["transcript"].splitlines():
                if line.startswith("Agent:"):
                    agent_utterances.append(line.split(":", 1)[1].strip())
                elif line.startswith("Caller:") or line.startswith("User:"):
                    caller_utterances.append(line.split(":", 1)[1].strip())

        if not agent_utterances and isinstance(r.get("turns"), list):
            for t in r["turns"]:
                if not isinstance(t, dict):
                    continue
                user_u = (
                    t.get("user_input")
                    or t.get("user_text")
                    or t.get("user_query")
                )
                if user_u:
                    caller_utterances.append(str(user_u).strip())
                agent_u = t.get("agent_text") or t.get("agent_response")
                if agent_u:
                    agent_utterances.append(str(agent_u).strip())
                for c in t.get("comparisons") or []:
                    if (
                        isinstance(c, dict)
                        and c.get("type") == "text"
                        and c.get("actual")
                        and c.get("actual") != "(missed)"
                    ):
                        agent_utterances.append(str(c["actual"]).strip())

        early_ack_count = 0
        early_ack_regex = re.compile(
            r"^(sure|certainly|of course|absolutely|i understand|"
            r"i'd be happy to|i can help with that|no problem)[,.]?",
            re.IGNORECASE,
        )
        subagent_transfer_regex = re.compile(
            r"\b(transferring to|routing to|calling|handing off to|transfer to)"
            r"\s+([a-zA-Z0-9_-]+_(agent|subagent|flow)|dialogflow|nga)\b"
            r"|\b(routing agent|subagent)\b",
            re.IGNORECASE,
        )
        directive_leak_regex = re.compile(
            r"(<no[ _-]?speech>|<thought>|\[pause\]|<silent>|\[internal\]|"
            r"\bsystem:\s*|\binstruction:\s*)",
            re.IGNORECASE,
        )
        markdown_link_regex = re.compile(
            r"\[[^\]]+\]\((https?://[^\)]+|\/[^\)]+)\)|\bhttps?://[^\s<>]+",
            re.IGNORECASE,
        )

        for u in agent_utterances:
            words = u.split()
            if len(words) > 80:
                run_issues.append("wall_of_text")
            if early_ack_regex.match(u.strip()):
                early_ack_count += 1
            if subagent_transfer_regex.search(u):
                run_issues.append("subagent_transfer_verbalization")
            if directive_leak_regex.search(u):
                run_issues.append("prompt_or_directive_leakage")
            if markdown_link_regex.search(u):
                run_issues.append("markdown_link_tts_leakage")

        if early_ack_count >= 2:
            run_issues.append("early_ack_redundancy")

        trace_str = (
            " ".join(str(x) for x in (r.get("detailed_trace") or []))
            + " "
            + str(r.get("transcript") or "")
        )
        transfer_matches = re.findall(
            r"\b(?:transfer|routing|handoff)\b", trace_str, re.IGNORECASE
        )
        if (
            len(transfer_matches) >= 3
            or r.get("subagent_ping_pong_loop")
            or r.get("transfer_loop_count", 0) >= 2
        ):
            run_issues.append("subagent_ping_pong_loop")

        caller_text = " ".join(caller_utterances).lower()
        human_req_keywords = [
            "human",
            "representative",
            "operator",
            "speak to someone",
            "talk to someone",
            "real person",
            "customer service rep",
        ]
        caller_wants_human = (
            any(k in caller_text for k in human_req_keywords)
            or "dtmf: 0" in caller_text
            or "dtmf 0" in caller_text
            or bool(r.get("dtmf_zero"))
        )
        human_req_count = sum(caller_text.count(k) for k in human_req_keywords)
        if (
            "dtmf: 0" in caller_text
            or "dtmf 0" in caller_text
            or r.get("dtmf_zero")
        ):
            human_req_count += 1

        if caller_wants_human or human_req_count > 0:
            run_issues.append("caller_requested_human")
            if human_req_count >= 2:
                has_human_transfer = any(
                    k in trace_str.lower()
                    for k in (
                        "transfertohuman",
                        "transfertoagent",
                        "live_agent",
                        "escalat",
                        "human_agent",
                    )
                )
                if not has_human_transfer:
                    run_issues.append("ignored_user")

        unique_run_issues = sorted(list(set(run_issues)))
        for iss in unique_run_issues:
            if iss in issue_counts:
                issue_counts[iss] += 1

        run_status_label = lang_name
        if is_lang_switch:
            run_status_label = (
                "Switch (Mismatch)" if is_mismatch else "Switch (OK)"
            )
        elif is_mismatch:
            run_status_label = f"{lang_name} (Mismatch)"

        summary_txt = ""
        if isinstance(nat_details, dict) and nat_details.get("summary"):
            summary_txt = nat_details["summary"]
        elif is_mismatch:
            summary_txt = (
                "Language switch or language adherence mismatch detected."
            )

        pl_p50 = None
        conv_pl = r.get("_conv_latency")
        if isinstance(conv_pl, dict) and conv_pl.get("p50_ms") is not None:
            pl_p50 = float(conv_pl["p50_ms"])

        eval_type_str = str(r.get("_eval_type", "sim"))
        eval_name_str = str(r.get("name", f"eval_{idx}"))
        eval_key = (eval_type_str, eval_name_str)
        per_eval_run_idx = eval_run_indices.get(eval_key, 0)
        eval_run_indices[eval_key] = per_eval_run_idx + 1

        row_id = f"conv-{idx}"

        if isinstance(r.get("turns"), int) and r["turns"] > 0:
            turns_count = r["turns"]
        elif isinstance(r.get("turns"), list) and r["turns"]:
            turns_count = len(r["turns"])
        elif isinstance(conv_pl, dict) and conv_pl.get("total_turns"):
            turns_count = int(conv_pl["total_turns"])
        elif isinstance(r.get("turn_traces"), list) and r["turn_traces"]:
            turns_count = len(r["turn_traces"])
        elif isinstance(r.get("conversation"), list) and r["conversation"]:
            turns_count = len(r["conversation"])
        elif (
            isinstance(nat_details, dict)
            and isinstance(nat_details.get("turns"), list)
            and nat_details["turns"]
        ):
            turns_count = len(nat_details["turns"])
        elif isinstance(r.get("detailed_trace"), list) and r["detailed_trace"]:
            agent_l = 0
            user_l = 0
            for dt in r["detailed_trace"]:
                for raw_l in str(dt).splitlines():
                    ls = raw_l.strip()
                    if ls.startswith("Agent Text:"):
                        agent_l += 1
                    elif ls.startswith("User:"):
                        user_l += 1
            turns_count = (
                agent_l if agent_l > 0 else (user_l if user_l > 0 else 1)
            )
        else:
            turns_count = 1

        if isinstance(r.get("llm_turns"), int) and r["llm_turns"] > 0:
            llm_turns_count = r["llm_turns"]
        elif isinstance(r.get("llm_calls"), int) and r["llm_calls"] > 0:
            llm_turns_count = r["llm_calls"]
        elif (
            isinstance(conv_pl, dict)
            and isinstance(conv_pl.get("lat_llm_calls"), list)
            and conv_pl["lat_llm_calls"]
        ):
            llm_turns_count = len(conv_pl["lat_llm_calls"])
        elif isinstance(r.get("turn_traces"), list) and r["turn_traces"]:
            llm_span_count = 0

            def _walk_spans(node: Any) -> None:
                nonlocal llm_span_count
                if isinstance(node, dict):
                    nm = str(node.get("name", "")).strip().upper()
                    if (
                        nm == "LLM"
                        or nm.startswith("LLM:")
                        or nm.startswith("LLM ")
                    ):
                        llm_span_count += 1
                    for ck in ("childSpans", "child_spans", "spans"):
                        if isinstance(node.get(ck), list):
                            _walk_spans(node[ck])
                    root = node.get("rootSpan") or node.get("root_span")
                    if isinstance(root, dict):
                        _walk_spans(root)
                elif isinstance(node, list):
                    for item in node:
                        _walk_spans(item)

            _walk_spans(r["turn_traces"])
            llm_turns_count = (
                llm_span_count if llm_span_count > 0 else turns_count
            )
        elif isinstance(r.get("detailed_trace"), list) and r["detailed_trace"]:
            dt_llm = 0
            for dt in r["detailed_trace"]:
                for raw_l in str(dt).splitlines():
                    ls = raw_l.strip()
                    if ls.startswith("Agent Text:") or ls.startswith(
                        "Tool Call:"
                    ):
                        dt_llm += 1
            llm_turns_count = dt_llm if dt_llm > 0 else turns_count
        else:
            llm_turns_count = turns_count

        dur_val = (
            r.get("duration_s")
            or r.get("duration")
            or r.get("duration_seconds")
        )
        dur_s = None
        if dur_val is not None:
            with contextlib.suppress(ValueError, TypeError):
                dur_s = float(dur_val)

        pl_max = None
        if isinstance(conv_pl, dict) and conv_pl.get("max_ms"):
            with contextlib.suppress(ValueError, TypeError):
                pl_max = float(conv_pl["max_ms"])

        wer_rounded = None
        if wer_val is not None:
            with contextlib.suppress(ValueError, TypeError):
                wer_rounded = round(float(wer_val), 3)

        audio_checks_detail = []
        for ck, ck_lbl in check_keys:
            val = audio_checks_map.get(ck)
            if val is not None:
                res_str = str(
                    val.get("result", "") if isinstance(val, dict) else val
                ).upper()
                res = (
                    "PASS"
                    if "PASS" in res_str
                    else ("FAIL" if "FAIL" in res_str else "SKIP")
                )
                just = ""
                if isinstance(val, dict):
                    just = str(
                        val.get("justification")
                        or val.get("reason")
                        or val.get("details")
                        or ""
                    )
                audio_checks_detail.append(
                    {
                        "key": ck,
                        "label": ck_lbl,
                        "result": res,
                        "justification": just,
                    }
                )

        conv_factors_detail = []
        cf_lbl_map = dict(conv_factors_meta)
        if isinstance(nat_details, dict) and isinstance(
            nat_details.get("conversation_factors"), list
        ):
            for cf in nat_details["conversation_factors"]:
                if isinstance(cf, dict):
                    q = cf.get("quality", "")
                    sc = cf.get("score")
                    sc_f = None
                    if sc is not None:
                        with contextlib.suppress(ValueError, TypeError):
                            sc_f = float(sc)
                    just = str(
                        cf.get("justification") or cf.get("reason") or ""
                    )
                    conv_factors_detail.append(
                        {
                            "key": q,
                            "label": cf_lbl_map.get(q, q),
                            "score": sc_f,
                            "justification": just,
                        }
                    )

        turn_naturalness_detail = []
        if isinstance(nat_details, dict) and isinstance(
            nat_details.get("turns"), list
        ):
            for i, t in enumerate(nat_details["turns"]):
                if isinstance(t, dict):
                    t_idx = (
                        t.get("turn_number")
                        or t.get("turn_idx")
                        or t.get("turn")
                        or (i + 1)
                    )
                    t_sc = t.get("score")
                    t_sc_f = None
                    if t_sc is not None:
                        with contextlib.suppress(ValueError, TypeError):
                            t_sc_f = float(t_sc)
                    t_lbl = str(t.get("label") or "")
                    t_just = str(
                        t.get("justification") or t.get("reason") or ""
                    )
                    low_f = []
                    for f in t.get("factors") or []:
                        if isinstance(f, dict):
                            fq = f.get("quality")
                            fs = f.get("score")
                            if fq and fs is not None:
                                with contextlib.suppress(ValueError, TypeError):
                                    if float(fs) <= 2:
                                        low_f.append(fq)
                    turn_naturalness_detail.append(
                        {
                            "turn_index": t_idx,
                            "score": t_sc_f,
                            "label": t_lbl,
                            "justification": t_just,
                            "low_factors": low_f,
                        }
                    )

        transcript_messages = _extract_transcript_messages(r)

        failure_reasons = []
        if isinstance(r.get("failure_reasons"), list):
            failure_reasons = [str(fr) for fr in r["failure_reasons"]]
        elif isinstance(nat_details, dict) and isinstance(
            nat_details.get("failure_reasons"), list
        ):
            failure_reasons = [str(fr) for fr in nat_details["failure_reasons"]]

        if is_graded:
            graded_runs_count += 1

        run_rows.append(
            {
                "id": row_id,
                "row_id": row_id,
                "is_graded": bool(is_graded),
                "intent": run_intent,
                "eval_name": eval_name_str,
                "run_num": r.get("run", 1),
                "run_idx": per_eval_run_idx,
                "eval_type": eval_type_str,
                "session_id": r.get("session_id", ""),
                "audio_src": (
                    f"audio/{r['session_id']}/"
                    + (
                        "full-session.wav"
                        if eval_type_str == "golden"
                        else "full-scenario.wav"
                    )
                    if r.get("session_id")
                    else ""
                ),
                "passed": bool(r.get("passed")),
                "outcome": "passed" if bool(r.get("passed")) else "failed",
                "grade_key": grade_key,
                "grade_label": grade_label,
                "score": nat_score,
                "language": lang_name,
                "language_switch": is_lang_switch,
                "language_mismatch": is_mismatch,
                "language_status_label": run_status_label,
                "audio_checks_passed": checks_passed,
                "audio_checks_total": checks_total,
                "audio_checks_status": (
                    "PASS"
                    if (checks_total and checks_passed == checks_total)
                    else ("FAIL" if checks_total else "N/A")
                ),
                "turns_count": turns_count,
                "llm_turns_count": llm_turns_count,
                "acoustic_measured": bool(is_measured),
                "duration_s": dur_s,
                "pl_p50_ms": pl_p50,
                "pl_max_ms": pl_max,
                "barge_turns": run_barge_turns,
                "max_tts_ms": (
                    round(run_max_tts_ms, 1) if run_max_tts_ms else None
                ),
                "agent_speech_ms": (
                    round(run_agent_speech_ms, 1)
                    if run_agent_speech_ms
                    else None
                ),
                "caller_requested_human": bool(caller_wants_human),
                "wer": wer_rounded,
                "issue_keys": unique_run_issues,
                "issue_labels": [
                    issue_dict[k][0]
                    for k in unique_run_issues
                    if k in issue_dict
                ],
                "flag_labels": [
                    issue_short_dict.get(k, issue_dict[k][0])
                    for k in unique_run_issues
                    if k in issue_dict
                ],
                "summary_text": summary_txt,
                "failure_reasons": failure_reasons,
                "audio_checks_detail": audio_checks_detail,
                "conv_factors_detail": conv_factors_detail,
                "turn_naturalness_detail": turn_naturalness_detail,
                "transcript_messages": transcript_messages,
                "acoustic_detail": acoustic_detail,
            }
        )
        suite_total_turns += turns_count
        suite_total_llm_turns += llm_turns_count

    mean_nat = (
        round(sum(nat_scores) / len(nat_scores), 2) if nat_scores else None
    )
    mean_gpa = (
        round(sum(gpa_scores) / len(gpa_scores), 2) if gpa_scores else None
    )
    grade_denom = naturalness_runs_count or graded_runs_count

    grade_bands = [
        {
            "key": "human_like",
            "label": "Human-like (4.0–5.0)",
            "short": "Human-like",
            "color": "var(--good)",
            "count": grade_counts["human_like"],
            "pct": (
                round(grade_counts["human_like"] / grade_denom * 100)
                if grade_denom
                else 0
            ),
        },
        {
            "key": "transitional",
            "label": "Transitional (2.5–3.9)",
            "short": "Transitional",
            "color": "var(--warn)",
            "count": grade_counts["transitional"],
            "pct": (
                round(grade_counts["transitional"] / grade_denom * 100)
                if grade_denom
                else 0
            ),
        },
        {
            "key": "bot_like",
            "label": "Bot-like (1.0–2.4)",
            "short": "Bot-like",
            "color": "var(--critical)",
            "count": grade_counts["bot_like"],
            "pct": (
                round(grade_counts["bot_like"] / grade_denom * 100)
                if grade_denom
                else 0
            ),
        },
    ]

    turn_factors_list = []
    for k, lbl in turn_factors_meta:
        scores = turn_factor_scores.get(k, [])
        m = round(sum(scores) / len(scores), 2) if scores else None
        col = (
            "var(--good)"
            if (m and m >= 4.0)
            else ("var(--warn)" if (m and m >= 3.0) else "var(--critical)")
        )
        turn_factors_list.append(
            {
                "key": k,
                "label": lbl,
                "mean_score": m,
                "pct": round(m / 5.0 * 100) if m is not None else 0,
                "count": len(scores),
                "low_count": sum(1 for s in scores if s <= 2),
                "color": col,
            }
        )

    conv_factors_list = []
    for k, lbl in conv_factors_meta:
        scores = conv_factor_scores.get(k, [])
        m = round(sum(scores) / len(scores), 2) if scores else None
        col = (
            "var(--good)"
            if (m and m >= 4.0)
            else ("var(--warn)" if (m and m >= 3.0) else "var(--critical)")
        )
        conv_factors_list.append(
            {
                "key": k,
                "label": lbl,
                "mean_score": m,
                "pct": round(m / 5.0 * 100) if m is not None else 0,
                "count": len(scores),
                "low_count": sum(1 for s in scores if s <= 2),
                "color": col,
            }
        )

    language_bars = []
    for l_name, cnt in sorted(lang_counts.items(), key=lambda x: -x[1]):
        m_cnt = lang_mismatches.get(l_name, 0)
        language_bars.append(
            {
                "key": l_name.lower(),
                "label": l_name,
                "count": cnt,
                "pct": round(cnt / total_runs * 100) if total_runs else 0,
                "mismatch_count": m_cnt,
                "color": "var(--critical)" if m_cnt > 0 else "var(--good)",
            }
        )
    if total_switches > 0:
        language_bars.append(
            {
                "key": "language_switch",
                "label": "Language Switch",
                "count": total_switches,
                "pct": (
                    round(total_switches / total_runs * 100)
                    if total_runs
                    else 0
                ),
                "mismatch_count": mismatch_switches,
                "color": "var(--critical)"
                if mismatch_switches > 0
                else "var(--good)",
            }
        )

    audio_checks_list = []
    for ck, lbl in check_keys:
        st = check_stats[ck]
        c_tot = st["total"]
        c_pass = st["pass"]
        c_fail = st["fail"]
        p_pct = round(c_pass / c_tot * 100) if c_tot else 0
        audio_checks_list.append(
            {
                "key": ck,
                "label": lbl,
                "pass_count": c_pass,
                "fail_count": c_fail,
                "total_count": c_tot,
                "pct": p_pct,
                "color": "var(--critical)" if c_fail > 0 else "var(--good)",
            }
        )

    issue_bars = []
    for k, cnt in sorted(issue_counts.items(), key=lambda x: -x[1]):
        if cnt > 0:
            lbl, col = issue_dict[k]
            issue_bars.append(
                {
                    "key": k,
                    "label": lbl,
                    "count": cnt,
                    "pct": round(
                        cnt / (graded_runs_count or total_runs or 1) * 100
                    ),
                    "color": col,
                }
            )

    intents_count: dict[str, int] = {}
    for r_row in run_rows:
        it = r_row.get("intent", "unknown")
        intents_count[it] = intents_count.get(it, 0) + 1
    intents_list = [
        {
            "key": k,
            "label": (
                k.replace("_", " ").replace("-", " ").strip().capitalize()
                if k != "unknown"
                else "Unknown"
            ),
            "count": c,
        }
        for k, c in sorted(intents_count.items(), key=lambda x: -x[1])
    ]
    barge_in_rate = round(suite_barge_turns / max(1, suite_total_turns), 3)

    mean_score_str = f"{mean_nat:.2f}" if mean_nat is not None else "—"
    gpa_str = f"{mean_gpa:.2f}" if mean_gpa is not None else "—"
    gpa_meta = (
        f"{graded_runs_count} of {total_runs} runs graded &middot; "
        f"Mean Naturalness {mean_score_str}/5.0 &middot; GPA {gpa_str} / 4"
    )

    audio_check_tips = _get_audio_analysis_tooltips()
    turn_tips, conv_tips, grade_tips = _parse_naturalness_prompt_tooltips()
    wer_tip = _get_wer_tooltip()
    acoustic_tips = _get_acoustic_metrics_tooltips()
    issue_tips = _get_issue_tooltips(
        audio_check_tips, turn_tips, conv_tips, wer_tip, acoustic_tips
    )

    acoustic_consistency_pct = (
        round(suite_clean_acoustic_runs / suite_measured_acoustic_runs * 100)
        if suite_measured_acoustic_runs
        else 100
    )
    acoustic_summary = {
        "measured_runs": suite_measured_acoustic_runs,
        "clean_runs": suite_clean_acoustic_runs,
        "seam_runs": suite_seam_runs,
        "loud_runs": suite_loud_runs,
        "midturn_runs": suite_midturn_runs,
        "trunc_runs": suite_trunc_runs,
        "jump_runs": suite_jump_runs,
        "jump_turns": suite_jump_turns,
        "join_runs": suite_join_runs,
        "jump_free_pct": (
            round((1 - suite_jump_runs / suite_join_runs) * 100)
            if suite_join_runs
            else None
        ),
        "consistency_pct": acoustic_consistency_pct,
        "mean_lufs": (
            round(sum(suite_lufs_means) / len(suite_lufs_means), 2)
            if suite_lufs_means
            else None
        ),
        "mean_artic_rate": (
            round(sum(suite_rate_means) / len(suite_rate_means), 2)
            if suite_rate_means
            else None
        ),
        "mean_pitch_sd_st": (
            round(sum(suite_f0_sds) / len(suite_f0_sds), 2)
            if suite_f0_sds
            else None
        ),
        "max_seam_st": round(suite_max_seam_st, 1),
    }

    checks_pass_runs = sum(
        1 for r_row in run_rows if r_row.get("audio_checks_status") == "PASS"
    )
    checks_total_runs = sum(
        1
        for r_row in run_rows
        if r_row.get("audio_checks_status") in ("PASS", "FAIL")
    )
    checks_pass_pct = (
        round(100 * checks_pass_runs / checks_total_runs)
        if checks_total_runs
        else None
    )

    mismatch_runs = sum(
        1 for r_row in run_rows if r_row.get("language_mismatch") is True
    )
    lang_matched_runs = total_runs - mismatch_runs
    lang_adherence_pct = (
        round(100 * lang_matched_runs / total_runs) if total_runs else None
    )

    flagged_runs = sum(
        1 for r_row in run_rows if len(r_row.get("issue_keys", [])) > 0
    )
    clean_issue_runs = total_runs - flagged_runs
    clean_issue_pct = (
        round(100 * clean_issue_runs / total_runs) if total_runs else None
    )

    avg_llm_turns = (
        round(suite_total_llm_turns / total_runs, 1) if total_runs else 0.0
    )
    avg_conv_turns = (
        round(suite_total_turns / total_runs, 1) if total_runs else 0.0
    )

    overview_metrics = {
        "checks_pass_runs": checks_pass_runs,
        "checks_total_runs": checks_total_runs,
        "checks_pass_pct": checks_pass_pct,
        "lang_matched_runs": lang_matched_runs,
        "lang_mismatch_runs": mismatch_runs,
        "lang_adherence_pct": lang_adherence_pct,
        "flagged_runs": flagged_runs,
        "clean_issue_runs": clean_issue_runs,
        "clean_issue_pct": clean_issue_pct,
        "total_llm_turns": suite_total_llm_turns,
        "avg_llm_turns": avg_llm_turns,
        "total_conv_turns": suite_total_turns,
        "avg_conv_turns": avg_conv_turns,
    }

    for b in grade_bands:
        b["tip"] = grade_tips.get(b["key"], "")
    for chk in audio_checks_list:
        chk["tip"] = audio_check_tips.get(chk["key"], "")
    for cf in conv_factors_list:
        cf["tip"] = conv_tips.get(cf["key"], "")
    for tf in turn_factors_list:
        tf["tip"] = turn_tips.get(tf["key"], "")
    for ib in issue_bars:
        ib["tip"] = issue_tips.get(ib["key"], "")

    tip_grade_dist = (
        "Distribution of overall conversation naturalness grades across"
        " evaluated runs. Graded on a 1.0–5.0 scale by Gemini multimodal"
        " audio/transcript evaluation and mapped to a 4.0 GPA scale (Human-like"
        " = 4.0, Transitional = 2.0–3.0, Bot-like = 0.0–1.0)."
    )
    tip_gpa_sub = (
        "Mean Naturalness is the average 1.0–5.0 overall_score across graded"
        " runs. GPA maps each run to a 4.0 scale: ≥4.0 (Human-like) = 4.0,"
        " 3.5–3.9 = 3.0, 2.5–3.4 (Transitional) = 2.0, 1.5–2.4 = 1.0, <1.5 ="
        " 0.0."
    )
    tip_audio_checks_head = (
        "Pass rates for multimodal Gemini audio checks (inspecting WAV"
        " recordings for voice consistency, >20s silence, looping, cutoff, and"
        " error fallbacks) alongside whole-conversation coherence factors"
        " (1–5 scale)."
    )
    tip_lang_head = (
        "Compares the routed/configured session language against the language"
        " spoken by the agent, and tracks whether mid-call caller language"
        " switches (e.g. English → Spanish) were adhered to without mismatch."
    )
    tip_ux_head = (
        "Ranked frequency of spoken UX defects across all runs, combining low"
        " naturalness factor scores (≤2/5), failed multimodal audio checks,"
        " language switch mismatches, slow perceived latency (>2,500 ms), and"
        " high ASR Word Error Rate (>25%). Click any bar to filter affected"
        " runs."
    )
    tip_turn_head = (
        "Mean score (1.0 = bot-like, 5.0 = human-like) for each of the 11"
        " per-turn vocal and linguistic dimensions evaluated across all graded"
        " agent turns. Flags turns scoring ≤2/5."
    )
    tip_conv_head = (
        "Whole-call coherence ratings (1–5 scale), multimodal WAV recording"
        " checks (PASS/FAIL), and user speech transcription Word Error Rate"
        " (WER)."
    )
    tip_th_grade = (
        "Mapped from 1.0–5.0 overall naturalness score: Human-like (≥4.0),"
        " Transitional (2.5–3.9), or Bot-like (<2.5)."
    )
    tip_th_score = (
        "Overall naturalness score on a 1.0–5.0 scale evaluated across vocal"
        " and linguistic factors."
    )
    tip_th_lang = (
        "Configured or routed language code vs agent spoken language. Flags"
        " language switch adherence or mismatches."
    )
    tip_th_checks = (
        "Status of multimodal checks inspecting audio recordings for voice"
        " drift, silence >20s, loops, cutoff, or error fallbacks."
    )
    tip_th_pl = (
        "Median turn perceived latency (p50 ms) measured from end of caller"
        " speech to start of agent audio."
    )
    tip_th_issues = (
        "Audio and conversational quality issues flagged across factors,"
        " checks, language adherence, and latency."
    )

    tooltips = {
        "tip_grade_dist": tip_grade_dist,
        "tip_gpa_sub": tip_gpa_sub,
        "tip_audio_checks_head": tip_audio_checks_head,
        "tip_lang_head": tip_lang_head,
        "tip_ux_head": tip_ux_head,
        "tip_turn_head": tip_turn_head,
        "tip_conv_head": tip_conv_head,
        "tip_th_grade": tip_th_grade,
        "tip_th_score": tip_th_score,
        "tip_th_lang": tip_th_lang,
        "tip_th_checks": tip_th_checks,
        "tip_th_pl": tip_th_pl,
        "tip_th_issues": tip_th_issues,
        "wer_tip": wer_tip,
        "issue_tips": issue_tips,
        "grade_tips": grade_tips,
        "turn_tips": turn_tips,
        "conv_tips": conv_tips,
        "audio_check_tips": audio_check_tips,
        "acoustic_tips": acoustic_tips,
        "acoustic_consistency": acoustic_tips.get("acoustic_consistency", ""),
        "voice_jump": acoustic_tips.get("voice_jump", ""),
    }

    conv_data_json = json.dumps(
        {
            "run_rows": run_rows,
            "issue_bars": issue_bars,
            "acoustic_summary": acoustic_summary,
            "tooltips": tooltips,
        }
    ).replace("</", "<\\/")

    if audio_analysis is False:
        has_audio_analysis = False
    elif audio_analysis is True:
        has_audio_analysis = True
    else:
        has_audio_analysis = bool(
            graded_runs_count > 0
            or total_switches > 0
            or mismatch_runs > 0
            or any(
                r.get("modality") == "audio"
                or r.get("routed_language")
                or r.get("language")
                or r.get("audio_analysis")
                or r.get("audio_metrics")
                or r.get("voice_metrics")
                or r.get("audio_drift")
                or r.get("naturalness_details")
                or r.get("naturalness")
                or r.get("wer") is not None
                for r in all_runs
            )
        )

    return {
        "total_runs": total_runs,
        "has_audio_analysis": has_audio_analysis,
        "graded_runs": graded_runs_count,
        "naturalness_runs": naturalness_runs_count,
        "mean_score": mean_nat,
        "gpa_4": mean_gpa,
        "gpa_meta": gpa_meta,
        "grade_bands": grade_bands,
        "turn_grade_counts": turn_grade_counts,
        "total_graded_turns": total_graded_turns,
        "turn_factors": turn_factors_list,
        "conv_factors": conv_factors_list,
        "language_bars": language_bars,
        "audio_checks": audio_checks_list,
        "acoustic_summary": acoustic_summary,
        "issue_bars": issue_bars,
        "intents_list": intents_list,
        "barge_stats": {
            "barge_turns": suite_barge_turns,
            "sessions_with_barge_in": sessions_with_barge_in,
            "barge_in_rate": barge_in_rate,
        },
        "wer_summary": {
            "mean_wer": (
                round(sum(wer_vals) / len(wer_vals), 3) if wer_vals else None
            ),
            "high_wer_count": high_wer_count,
            "total_count": len(wer_vals),
        },
        "checks_pass_runs": checks_pass_runs,
        "checks_total_runs": checks_total_runs,
        "checks_pass_pct": checks_pass_pct,
        "lang_matched_runs": lang_matched_runs,
        "lang_mismatch_runs": mismatch_runs,
        "lang_adherence_pct": lang_adherence_pct,
        "flagged_runs": flagged_runs,
        "clean_issue_runs": clean_issue_runs,
        "clean_issue_pct": clean_issue_pct,
        "total_llm_turns": suite_total_llm_turns,
        "avg_llm_turns": avg_llm_turns,
        "total_conv_turns": suite_total_turns,
        "avg_conv_turns": avg_conv_turns,
        "overview_metrics": overview_metrics,
        "tooltips": tooltips,
        "conv_data_json": conv_data_json,
        "run_rows": run_rows,
    }


def _get_audio_analysis_tooltips() -> dict[str, str]:
    """Extract tooltips dynamically from ANALYSIS_REGISTRY prompts."""
    tips: dict[str, str] = {}
    for key, analysis in audio_analysis.ANALYSIS_REGISTRY.items():
        prompt = analysis.prompt.strip()
        task_m = re.search(
            r"#\s*Task\s*\n(.*?)(?=\n#|\Z)", prompt, re.DOTALL | re.IGNORECASE
        )
        out_m = re.search(
            r"#\s*Output\s*\n(.*?)(?=\n#|\Z)", prompt, re.DOTALL | re.IGNORECASE
        )
        task_str = " ".join(task_m.group(1).split()) if task_m else ""
        out_str = " ".join(out_m.group(1).split()) if out_m else ""
        if task_str and out_str:
            tips[key] = f"Task: {task_str} Output: {out_str}"
        elif task_str:
            tips[key] = task_str
        else:
            tips[key] = " ".join(prompt.split())
    return tips


def _parse_naturalness_prompt_tooltips() -> tuple[
    dict[str, str], dict[str, str], dict[str, str]
]:
    """Extract turn and conversation quality tooltips dynamically.

    Parses naturalness_prompts constants and NaturalnessConfig defaults.
    """
    turn_tips: dict[str, str] = {}
    for match in re.finditer(
        r"[-*]\s*`([^`]+)`\s*[—–-]\s*(.*?)(?=\n[-*]\s*`|\Z)",
        naturalness_prompts.DEFAULT_TURN_QUALITIES,
        re.DOTALL,
    ):
        q_name = match.group(1).strip()
        q_desc = " ".join(match.group(2).split())
        turn_tips[q_name] = q_desc

    cfg = naturalness.NaturalnessConfig()
    b = cfg.latency_bands_ms
    turn_tips["perceivedLatency"] = (
        f"Turn perceived latency rating (bands: <{b[0]}ms = 5, "
        f"<{b[1]}ms = 4, <{b[2]}ms = 3, <{b[3]}ms = 2, ≥{b[3]}ms = 1)."
    )

    conv_tips: dict[str, str] = {}
    for match in re.finditer(
        r"[-*]\s*`([^`]+)`\s*[—–-]\s*(.*?)(?=\n[-*]\s*`|\Z)",
        naturalness_prompts.DEFAULT_CONVERSATION_QUALITIES,
        re.DOTALL,
    ):
        q_name = match.group(1).strip()
        q_desc = " ".join(match.group(2).split())
        conv_tips[q_name] = q_desc

    grade_tips: dict[str, str] = {}
    for match in re.finditer(
        r"[-*]\s*`([^`]+)`\s*[—–-]\s*(.*?)(?=\n[-*]\s*`|\Z)",
        naturalness_prompts.NATURALNESS_RUBRIC,
        re.DOTALL,
    ):
        lbl = match.group(1).strip()
        desc = " ".join(match.group(2).split())
        if lbl == "Human-like":
            grade_tips["human_like"] = (
                f"Human-like (≥{cfg.human_like_at_or_above}/5.0, GPA 4.0):"
                f" {desc}"
            )
        elif lbl == "Transitional":
            grade_tips["transitional"] = (
                f"Transitional ({cfg.bot_like_below}–"
                f"{cfg.human_like_at_or_above}/5.0, GPA 2.0–3.0): {desc}"
            )
        elif lbl == "Bot-like":
            grade_tips["bot_like"] = (
                f"Bot-like (<{cfg.bot_like_below}/5.0, GPA 0.0–1.0): {desc}"
            )

    return turn_tips, conv_tips, grade_tips


def _get_wer_tooltip() -> str:
    """Extract WER tooltip from audio_transcription module."""
    doc = audio_transcription.calculate_wer.__doc__ or ""
    lines = [
        line.strip()
        for line in doc.splitlines()
        if "WER =" in line or "Word Error Rate" in line
    ]
    formula = " ".join(lines) if lines else "Word Error Rate"
    return (
        f"{formula}. Evaluated by comparing ASR transcripts against Gemini"
        " reference transcription."
    )


def _get_acoustic_metrics_tooltips() -> dict[str, str]:
    """Dynamically extract acoustic and voice drift tooltips."""
    drift_doc = audio_drift.__doc__ or ""
    metrics_doc = audio_metrics.__doc__ or ""

    def _extract_drift_flag(header_pattern: str) -> str:
        m = re.search(
            rf"^\s*{header_pattern}\s+(.*?)(?=\n\s*(?:SEAM|LOUD|TRUNC|MIDTURN|JUMP)\s|\n\n|\Z)",
            drift_doc,
            re.DOTALL | re.MULTILINE,
        )
        return " ".join(m.group(1).split()) if m else ""

    seam_doc = _extract_drift_flag(r"SEAM")
    loud_doc = _extract_drift_flag(r"LOUD")
    trunc_doc = _extract_drift_flag(r"TRUNC")
    midturn_doc = _extract_drift_flag(r"MIDTURN\s*/\s*MIDDRIFT")
    jump_doc = _extract_drift_flag(r"JUMP")

    bullets = [
        " ".join(m.group(1).split()).rstrip(",.")
        for m in re.finditer(
            r"^\*\s+(.*?)(?=\n\*\s+|\n\n|\Z)",
            metrics_doc,
            re.DOTALL | re.MULTILINE,
        )
    ]
    consistency_summary = (
        "; ".join(bullets)
        if bullets
        else (
            "integrated loudness (LUFS), articulation rate (syllables/s),"
            " median pitch (Hz), and within-turn voice seams"
        )
    )

    loud_step = getattr(audio_drift, "DEFAULT_LOUD_DB", 3.0)
    seam_strong = getattr(audio_metrics, "SEAM_SPEAKER_CHANGE_ST", 11.0)
    seam_mild = getattr(audio_metrics, "SEAM_DRIFT_ST", 7.0)
    trunc_ratio = getattr(audio_drift, "TRUNC_RATIO", 0.6)
    trunc_min_syl = getattr(audio_drift, "TRUNC_MIN_SYLLABLES", 8)

    return {
        "tts_cache_seam": (
            f"SEAM ({seam_doc})"
            if seam_doc
            else (
                "Triggered when adjacent agent turns transition between cached"
                " and fresh TTS synthesis (vintage seam)."
            )
        ),
        "loudness_jump": (
            f"LOUD ({loud_doc})"
            if loud_doc
            else (
                "Triggered when adjacent agent turns exhibit an integrated"
                f" loudness jump exceeding {loud_step:.1f} dB LUFS."
            )
        ),
        "midturn_voice_seam": (
            f"MIDTURN / MIDDRIFT ({midturn_doc}; thresholds:"
            f" ≥{seam_mild:.1f} st drift, ≥{seam_strong:.1f} st speaker change)"
            if midturn_doc
            else (
                "Triggered when median pitch shifts across an intra-turn pause"
                f" by ≥{seam_mild:.1f} st or ≥{seam_strong:.1f} st."
            )
        ),
        "audio_truncation": (
            f"TRUNC ({trunc_doc})"
            if trunc_doc
            else (
                "Triggered when agent audio syllable count falls below"
                f" ratio < {trunc_ratio:.1f} (≥{trunc_min_syl} syllables)."
            )
        ),
        "voice_jump": (
            f"JUMP ({jump_doc})"
            if jump_doc
            else (
                "Triggered when the voice steps up louder and higher where a"
                " turn's next rendered part starts."
            )
        ),
        "acoustic_consistency": (
            f"Signal-level voice consistency ({consistency_summary})."
        ),
    }


def _get_issue_tooltips(
    audio_check_tips: dict[str, str],
    turn_tips: dict[str, str],
    conv_tips: dict[str, str],
    wer_tip: str,
    acoustic_tips: dict[str, str] | None = None,
) -> dict[str, str]:
    """Derive issue tooltips from prompt-extracted descriptions."""
    ac_tips = acoustic_tips or _get_acoustic_metrics_tooltips()
    return {
        "robotic_delivery": (
            "Triggered when run is Bot-like or scriptedness ≤2/5"
            f" ({turn_tips.get('scriptedness', '')})"
        ),
        "language_mismatch": (
            "Triggered when the agent speaks the wrong language, ignores a"
            " caller's mid-call language switch request, or fails a language"
            " expectation."
        ),
        "voice_drift": (
            "Triggered when audio check fails"
            f" ({audio_check_tips.get('agent_voice_consistency', '')})"
            " or personaConsistency ≤2/5"
            f" ({conv_tips.get('personaConsistency', '')})."
        ),
        "long_pauses": (
            "Triggered when audio check fails"
            f" ({audio_check_tips.get('no_long_pauses', '')})"
            " or perceived latency factor ≤2/5."
        ),
        "agent_looping": (
            "Triggered when audio check fails"
            f" ({audio_check_tips.get('agent_looping', '')})"
            " or repetitionAvoidance ≤2/5"
            f" ({conv_tips.get('repetitionAvoidance', '')})."
        ),
        "audio_cutoff": (
            "Triggered when audio check fails"
            f" ({audio_check_tips.get('agent_cutoff', '')})."
        ),
        "having_trouble": (
            "Triggered when audio check fails"
            f" ({audio_check_tips.get('agent_having_trouble', '')})."
        ),
        "flat_emotion": (
            "Triggered when emotion/empathy ≤2/5"
            f" ({turn_tips.get('emotion', '')})."
        ),
        "unnatural_pacing": (
            f"Triggered when pacing ≤2/5 ({turn_tips.get('pacing', '')})."
        ),
        "written_grammar": (
            "Triggered when grammarStyle or spokenNumbers ≤2/5"
            f" ({turn_tips.get('grammarStyle', '')})."
        ),
        "overlong_turns": (
            f"Triggered when concision ≤2/5 ({turn_tips.get('concision', '')})."
        ),
        "turn_taking": (
            "Triggered when turnTaking or disfluency ≤2/5"
            f" ({turn_tips.get('turnTaking', '')})."
        ),
        "high_wer": f"Triggered when {wer_tip} exceeds 25%.",
        "audio_exp_failed": (
            "Triggered when a custom audio_expectation evaluated against"
            " recorded WAV files is Not Met."
        ),
        "verdict_barged_missed": (
            "Triggered when a decisive turn (tool call, parameter collection,"
            " or verdict) was interrupted mid-speech by caller barge-in."
        ),
        "wall_of_text": (
            "Triggered when an agent turn exceeds 12 seconds of speech or 80"
            " spoken words without pausing for caller input."
        ),
        "markdown_link_tts_leakage": (
            "Triggered when unrendered markdown link syntax (e.g. [text](url))"
            " or raw web URLs are verbalized in agent TTS audio."
        ),
        "prompt_or_directive_leakage": (
            "Triggered when internal prompt instructions, thoughts, or"
            " directives (e.g. <thought>, [pause], <no speech>) leak into"
            " spoken agent audio."
        ),
        "early_ack_redundancy": (
            "Triggered when the agent repeatedly prefaces responses with"
            " redundant acknowledgments or filler phrases across consecutive"
            " turns."
        ),
        "subagent_transfer_verbalization": (
            "Triggered when internal architectural subagent or router names"
            " (e.g. transfer to billing_agent) are verbalized to the caller."
        ),
        "subagent_ping_pong_loop": (
            "Triggered when multiple subagent handoffs occur in a loop or"
            " ping-pong back and forth within a single conversation."
        ),
        "ignored_user": (
            "Triggered when the caller requests a human representative or"
            " presses DTMF 0 two or more times without being transferred."
        ),
        "caller_requested_human": (
            "Triggered when the caller requested transfer to a human"
            " representative or agent."
        ),
        "tts_cache_seam": ac_tips.get(
            "tts_cache_seam",
            "Triggered when adjacent agent turns transition between cached and"
            " fresh TTS synthesis (vintage seam).",
        ),
        "loudness_jump": ac_tips.get(
            "loudness_jump",
            "Triggered when adjacent agent turns exhibit an integrated"
            " loudness jump exceeding threshold.",
        ),
        "midturn_voice_seam": ac_tips.get(
            "midturn_voice_seam",
            "Triggered when median pitch shifts abruptly across an intra-turn"
            " pause.",
        ),
        "voice_jump": ac_tips.get(
            "voice_jump",
            "Triggered when the voice steps up louder and higher where a"
            " turn's next rendered part starts.",
        ),
        "audio_truncation": ac_tips.get(
            "audio_truncation",
            "Triggered when agent audio duration falls well under expected turn"
            " text.",
        ),
    }


def _render_suite_audio_analysis(audio_data: dict[str, Any] | None) -> str:
    """Render Audio Analysis & Quality tab HTML via Jinja2 template macro."""
    if not isinstance(audio_data, dict) or not audio_data.get(
        "has_audio_analysis"
    ):
        return ""
    mod = _get_combined_template_module()
    return str(mod.render_suite_audio(audio_data))


def _resolve_conv_trace_dict(
    r: dict[str, Any],
    output_path: str = "",
    remote_traces: dict[str, Any] | None = None,
    traces_dir: str | None = None,
) -> dict[str, Any] | None:
    """Resolve conversation trace dict with per-turn root_span."""
    if not isinstance(r, dict):
        return None

    turn_traces = r.get("turn_traces")
    if (
        isinstance(turn_traces, list)
        and turn_traces
        and any(
            isinstance(t, dict) and (t.get("root_span") or t.get("rootSpan"))
            for t in turn_traces
        )
    ):
        return {
            "session_id": r.get("session_id", ""),
            "turns": turn_traces,
        }

    turns_field = r.get("turns")
    if (
        isinstance(turns_field, list)
        and turns_field
        and any(
            isinstance(t, dict) and (t.get("root_span") or t.get("rootSpan"))
            for t in turns_field
        )
    ):
        return {
            "session_id": r.get("session_id", ""),
            "turns": turns_field,
        }

    sid = str(r.get("session_id", "") or "").strip()
    if not sid:
        return None

    candidate_paths = [f"/tmp/scrapi_traces/{sid}.json"]
    if traces_dir:
        candidate_paths.extend(
            [
                os.path.join(traces_dir, f"{sid}_trace.json"),
                os.path.join(traces_dir, f"{sid}.json"),
            ]
        )
    if output_path and not output_path.startswith("gs://"):
        abs_out = os.path.abspath(output_path)
        out_dir = os.path.dirname(abs_out)
        parent_dir = os.path.dirname(out_dir)
        candidate_paths.extend(
            [
                os.path.join(out_dir, "traces", f"{sid}_trace.json"),
                os.path.join(parent_dir, "traces", f"{sid}_trace.json"),
                os.path.join(out_dir, "scratch", f"{sid}_trace.json"),
                os.path.join(parent_dir, "scratch", f"{sid}_trace.json"),
            ]
        )
    candidate_paths.extend(
        [
            os.path.join("reports", "traces", f"{sid}_trace.json"),
            os.path.join("scratch", f"{sid}_trace.json"),
        ]
    )

    for cp in candidate_paths:
        if os.path.isfile(cp):
            with contextlib.suppress(Exception):
                with _ORIG_OPEN(cp, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and isinstance(
                    data.get("turns"), list
                ):
                    return data

    if remote_traces and sid in remote_traces:
        rem = remote_traces[sid]
        if isinstance(rem, dict) and isinstance(rem.get("turns"), list):
            return rem

    return None


def _attach_perceived_latency(
    results_list: list[dict[str, Any]],
    app_name: str = "",
    output_path: str = "",
    traces_dir: str | None = None,
    eval_type: str = "",
    modality: str = "",
) -> list[dict[str, Any]]:
    """Resolve traces and attach latency fields to each result dict."""
    if not results_list:
        return []

    # Check if any results need remote fetching from ConversationHistory
    missing_sids: list[str] = []
    for r in results_list:
        if not isinstance(r, dict):
            continue
        if (
            _resolve_conv_trace_dict(
                r, output_path=output_path, traces_dir=traces_dir
            )
            is None
        ):
            sid = str(r.get("session_id", "") or "").strip()
            if sid:
                missing_sids.append(sid)

    remote_traces: dict[str, Any] = {}
    if (
        missing_sids
        and app_name
        and os.environ.get("CXAS_OAUTH_TOKEN") != "mock_token_for_tests"
    ):
        with contextlib.suppress(Exception):
            ch = conversation_history.ConversationHistory(app_name=app_name)
            for sid in set(missing_sids):
                with contextlib.suppress(Exception):
                    conv_obj = ch.get_conversation(sid)
                    conv_d = (
                        type(conv_obj).to_dict(conv_obj)
                        if not isinstance(conv_obj, dict)
                        else conv_obj
                    )
                    if isinstance(conv_d, dict) and isinstance(
                        conv_d.get("turns"), list
                    ):
                        remote_traces[sid] = conv_d

    callback_catalog = LatencyParser.discover_callback_catalog(
        [output_path, traces_dir or ""]
    )
    conv_analyses: list[dict[str, Any]] = []
    for r in results_list:
        if not isinstance(r, dict):
            continue
        conv_dict = _resolve_conv_trace_dict(
            r,
            output_path=output_path,
            remote_traces=remote_traces,
            traces_dir=traces_dir,
        )
        if not conv_dict:
            continue
        r_eval_type = str(r.get("type", "") or eval_type or "")
        r_modality = str(r.get("modality", "") or modality or "")
        conv_pl = LatencyParser.analyze_conversation_perceived_latency(
            conv_dict,
            session_id=str(r.get("session_id", "") or ""),
            conv_name=str(r.get("name", "") or ""),
            eval_type=r_eval_type,
            modality=r_modality,
            callback_catalog=callback_catalog,
        )
        if conv_pl:
            run_lbl = f"Run {r['run']}" if "run" in r else ""
            r["_conv_latency"] = conv_pl
            r["_conv_latency_html"] = _render_conv_latency_drawer(
                conv_pl, run_label=run_lbl
            )
            r["_turn_latencies"] = conv_pl.get("turns", [])
            # Also attach _turn_latency_html to golden turns
            if isinstance(r.get("turns"), list):
                for idx, gt in enumerate(r["turns"]):
                    if isinstance(gt, dict) and idx < len(r["_turn_latencies"]):
                        gt["_turn_latency_html"] = _render_turn_latency_drawer(
                            r["_turn_latencies"][idx]
                        )
            conv_analyses.append(conv_pl)

    return conv_analyses


def _inject_turn_latency_items(
    merged: list[Any], turn_latencies: list[dict[str, Any]] | None
) -> list[Any]:
    """Inject ('turn_latency', html) items at the end of each user turn."""
    if not merged or not turn_latencies:
        return merged
    out: list[Any] = []
    turn_idx = -1
    for item in merged:
        kind = item[0]
        if kind == "user":
            if 0 <= turn_idx < len(turn_latencies):
                out.append(
                    (
                        "turn_latency",
                        _render_turn_latency_drawer(turn_latencies[turn_idx]),
                    )
                )
            turn_idx += 1
        out.append(item)
    if 0 <= turn_idx < len(turn_latencies):
        out.append(
            (
                "turn_latency",
                _render_turn_latency_drawer(turn_latencies[turn_idx]),
            )
        )
    return out


def _get_summary_block(
    passed: typing.Any,
    total: typing.Any,
    errors: typing.Any,
    modality: typing.Any,
    model: typing.Any,
    ts: typing.Any,
    wall_clock_s: typing.Any,
    suite_pl: dict[str, Any] | None = None,
) -> str:
    """Return the HTML summary block."""
    pct = 100 * passed / total if total else 0
    pass_threshold = 90
    cls = "pass" if pct >= pass_threshold else "fail"
    pl_btn = (
        '<button id="btn-latency" onclick="toggleAllLatency()">'
        "⏱ Expand All Latency</button>"
        if suite_pl
        else ""
    )
    controls_html = (
        '<div class="controls">\n'
        '  <button id="btn-failures" onclick="toggleFailures()">'
        "Show Failures Only</button>\n"
        '  <button onclick="expandAll()">Expand All</button>\n'
        '  <button onclick="collapseAll()">Collapse All</button>\n'
        f"  {pl_btn}\n"
        "</div>\n"
    )
    suite_drawer = _render_suite_latency_drawer(suite_pl) if suite_pl else ""
    rt_str = (
        f" | Runtime: {_fmt_duration(wall_clock_s)}" if wall_clock_s else ""
    )
    return f"""<h1>
  <span>Simulation Eval Report</span>
  <button type="button" class="theme-toggle-btn" onclick="toggleTheme()">\
🌗 Toggle Light / Dark Theme</button>
</h1>
<div class="summary">
  <div class="summary-top">
    <div class="big {cls}">{pct:.1f}%</div>
    <div><b>{passed}/{total}</b> passed | {errors} errors |
      {modality} | model: <code>{model}</code><br>
      <span class="meta">Generated {ts}
      {rt_str}</span>
    </div>
  </div>
</div>
{controls_html}{suite_drawer}"""


def _get_results_table(eval_stats: typing.Any) -> typing.Any:
    """Return the HTML results table."""
    html = """
<h2>Results by Eval</h2>
<table>
  <tr><th>Score</th><th>Eval</th><th>Runs</th></tr>
"""
    for name, s in sorted(
        eval_stats.items(), key=lambda x: x[1]["pass"] / max(x[1]["total"], 1)
    ):
        score = f"{s['pass']}/{s['total']}"
        is_all_pass = s["pass"] == s["total"]
        cls = "pass" if is_all_pass else "fail"
        passed_str = "true" if is_all_pass else "false"
        safe_name = name.replace("'", "\\'")
        dots = ""
        for i, r in enumerate(s["runs"]):
            dot_cls = "p" if r.get("passed") else ("e" if "error" in r else "f")
            dots += (
                f'<span class="run-dot {dot_cls}" title="Run {r["run"]}" '
                'onclick="event.stopPropagation(); '
                f"jumpToRun('sim', '{safe_name}', {i})\"></span>"
            )
        html += (
            f'  <tr class="clickable" data-passed="{passed_str}" '
            f"onclick=\"jumpTo('sim', '{safe_name}')\">"
            f'<td class="{cls}"><b>{score}</b></td>'
            f"<td>{_escape(name)}</td><td>{dots}</td></tr>\n"
        )
    html += "</table>\n"
    return html


def _render_session_link(session_id: typing.Any, ces_base: typing.Any) -> str:
    """Render the session link."""
    if not session_id:
        return ""
    if ces_base:
        session_url = (
            f"{ces_base}?panel=conversation_list&id={session_id}&source=EVAL"
        )
        return (
            '<div class="session-link">Session: '
            f'<a href="{session_url}" target="_blank">'
            f"<code>{session_id}</code></a></div>\n"
        )
    return (
        f'<div class="session-link">Session: <code>{session_id}</code></div>\n'
    )


def _render_session_parameters(sparams: typing.Any) -> typing.Any:
    """Render session parameters."""
    if not sparams:
        return ""
    html = (
        '<details class="tool-details"><summary class="tool-summary">'
        "&#9881; <b>Session Parameters</b></summary>"
    )
    html += (
        '<pre class="tool-data">'
        f"{_escape(json.dumps(sparams, indent=2))}</pre></details>\n"
    )
    return html


def _render_step_details(step_details: typing.Any) -> typing.Any:
    """Render step details."""
    if not step_details:
        return ""
    html = ""
    for step in step_details:
        step_cls = "pass" if step["status"] == "Completed" else "fail"
        html += (
            f'<div class="step"><b>Goal:</b> {_escape(step["goal"])}<br>'
            f"<b>Criteria:</b> {_escape(step['success_criteria'])}<br>"
        )
        badge_cls = step_cls.replace("pass", "met").replace("fail", "not-met")
        html += (
            f'<b>Status:</b> <span class="badge {badge_cls}">'
            f"{_escape(step['status'])}</span><br>"
        )
        if step.get("justification"):
            html += f"<b>Justification:</b> {_escape(step['justification'])}"
        html += "</div>\n"
    return html


def _render_expectation_details(expectation_details: typing.Any) -> typing.Any:
    """Render expectation details."""
    if not expectation_details:
        return ""
    html = ""
    for exp in expectation_details:
        exp_cls = "met" if exp["status"] == "Met" else "not-met"
        html += f'<div class="expectation"><span class="badge {exp_cls}">'
        html += f"{_escape(exp['status'])}</span> {_escape(exp['expectation'])}"
        if exp.get("justification"):
            html += (
                f'<br><span class="meta">{_escape(exp["justification"])}</span>'
            )
        html += "</div>\n"
    return html


_NATURALNESS_BADGE = {
    "Human-like": "met",
    "Transitional": "",
    "Bot-like": "not-met",
}


def _render_naturalness_factors(factors: typing.Any) -> typing.Any:
    """Render one turn's scored qualities as a compact list."""
    if not factors:
        return ""
    parts = []
    for factor in factors:
        quality = _escape(str(factor.get("quality", "")))
        score = _escape(str(factor.get("score", "")))
        value = factor.get("value") or ""
        label = f"{quality} {score}/5"
        if value:
            label += f" ({_escape(str(value))})"
        reason = _escape(str(factor.get("reason", "")))
        parts.append(
            f"<li><b>{label}</b>"
            + (f' <span class="meta">{reason}</span>' if reason else "")
            + "</li>"
        )
    return "<ul>" + "".join(parts) + "</ul>"


def _render_naturalness_details(details: typing.Any) -> typing.Any:
    """Render the optional Naturalness Metric breakdown.

    Returns an empty string when the metric did not run, so reports for
    simulations that never opted in are byte-identical to before.
    """
    if not details:
        return ""

    label = str(details.get("overall_label", ""))
    badge = _NATURALNESS_BADGE.get(label, "")
    score = details.get("overall_score", "?")

    html = '<div class="expectation">'
    html += (
        f'<b>Naturalness:</b> <span class="badge {badge}">'
        f"{_escape(label)}</span> {_escape(str(score))}/5"
    )
    threshold = details.get("pass_threshold")
    if threshold is not None:
        verdict = "PASS" if details.get("passed") else "FAIL"
        html += (
            f' <span class="meta">(threshold {_escape(str(threshold))} '
            f"&rarr; {verdict})</span>"
        )
    # A hard failure, e.g. a turn past the latency limit, sinks the run on its
    # own, so it is called out rather than left to the factor list.
    for reason in details.get("failure_reasons") or []:
        html += (
            f'<br><span class="badge not-met">FAILED</span> '
            f'<span class="meta">{_escape(str(reason))}</span>'
        )
    if details.get("summary"):
        html += (
            f'<br><span class="meta">{_escape(str(details["summary"]))}</span>'
        )

    averages = details.get("factor_averages") or {}
    if averages:
        avg_text = ", ".join(f"{k} {v}/5" for k, v in averages.items())
        html += f'<br><span class="meta">Averages: {_escape(avg_text)}</span>'

    conv_factors = details.get("conversation_factors") or []
    if conv_factors:
        html += "<br><b>Conversation level:</b>"
        html += _render_naturalness_factors(conv_factors)

    for turn in details.get("turns") or []:
        turn_label = str(turn.get("label", ""))
        turn_badge = _NATURALNESS_BADGE.get(turn_label, "")
        html += (
            f'<details class="run-detail"><summary>Turn '
            f"{_escape(str(turn.get('turn_index', '?')))} &mdash; "
            f'<span class="badge {turn_badge}">{_escape(turn_label)}</span> '
            f"{_escape(str(turn.get('score', '?')))}/5</summary>"
        )
        if turn.get("agent_utterance"):
            html += (
                f'<div class="meta">'
                f"{_escape(str(turn['agent_utterance']))}</div>"
            )
        if turn.get("justification"):
            html += (
                f'<div class="meta">{_escape(str(turn["justification"]))}</div>'
            )
        html += _render_naturalness_factors(turn.get("factors"))
        html += "</details>\n"

    html += "</div>\n"
    return html


def _parse_trace(trace: typing.Any, tools_map: typing.Any) -> typing.Any:
    """Parse trace lines into typed entries."""
    parsed_lines = []
    for entry in trace:
        for line in entry.split("\n"):
            stripped_line = line.strip()
            if not stripped_line:
                continue
            formatted_line = _format_trace_line(stripped_line, tools_map)
            if formatted_line.startswith(
                "Agent Text (Diag):"
            ) or formatted_line.startswith("User Query:"):
                continue
            elif formatted_line.startswith("Agent Text:"):
                parsed_lines.append(
                    ("agent", formatted_line[len("Agent Text:") :].strip())
                )
            elif formatted_line.startswith("User:"):
                parsed_lines.append(("user", formatted_line[5:].strip()))
            elif formatted_line.startswith("Tool Call"):
                parsed_lines.append(("tool_call", formatted_line))
            elif formatted_line.startswith("Tool Response"):
                parsed_lines.append(("tool_resp", formatted_line))
            elif formatted_line.startswith("Agent Transfer:"):
                parsed_lines.append(
                    (
                        "agent_transfer",
                        formatted_line[len("Agent Transfer:") :].strip(),
                    )
                )
            elif formatted_line.startswith("Custom Payload:"):
                parsed_lines.append(
                    (
                        "custom_payload",
                        formatted_line[len("Custom Payload:") :].strip(),
                    )
                )
            elif formatted_line.startswith(
                "[Callback:"
            ) or formatted_line.startswith("Callback:"):
                parsed_lines.append(("callback_span", formatted_line))
            else:
                parsed_lines.append(("system", formatted_line))
    return parsed_lines


def _merge_trace_lines(parsed_lines: typing.Any) -> typing.Any:
    """Merge consecutive agent lines and pair tool calls with responses."""
    merged: list[Any] = []
    for kind, text in parsed_lines:
        if kind == "agent" and merged and merged[-1][0] == "agent":
            merged[-1] = ("agent", merged[-1][1] + " " + text)
        elif kind == "tool_resp" and merged and merged[-1][0] == "tool_call":
            merged[-1] = ("tool_pair", merged[-1][1], text)
        elif kind == "callback_span":
            if merged and merged[-1][0] == "callback_group":
                merged[-1][1].append(text)
            else:
                merged.append(("callback_group", [text]))
        else:
            merged.append((kind, text))
    return merged


def _render_merged_items(merged: typing.Any) -> typing.Any:
    """Render merged trace items to HTML."""
    html = ""
    for item in merged:
        kind = item[0]
        if kind == "user":
            html += f'<div class="user"><b>User:</b> {_escape(item[1])}</div>\n'
        elif kind == "agent":
            html += (
                f'<div class="agent"><b>Agent:</b> {_escape(item[1])}</div>\n'
            )
        elif kind in ("tool_call", "tool_pair"):
            call_text = item[1]
            lbl, _, args = call_text.partition(" with args ")
            lbl = lbl.replace("Tool Call: ", "").replace(
                "Tool Call (Output): ", ""
            )
            lbl = lbl.split("/")[-1] if "/" in lbl else lbl
            html += (
                '<details class="tool-details"><summary class="tool-summary">'
                f"&#128295; <b>{_escape(lbl)}</b></summary>"
            )
            if args:
                html += (
                    '<div class="tool-section"><b>Input:</b></div>'
                    f'<pre class="tool-data">{_escape(args)}</pre>'
                )
            if kind == "tool_pair":
                _, _, result = item[2].partition(" with result ")
                if result:
                    html += (
                        '<div class="tool-section"><b>Output:</b></div>'
                        f'<pre class="tool-data">{_escape(result)}</pre>'
                    )
            html += "</details>\n"
        elif kind == "tool_resp":
            lbl, _, result = item[1].partition(" with result ")
            lbl = lbl.replace("Tool Response: ", "").split("/")[-1]
            html += (
                '<details class="tool-details"><summary class="tool-summary">'
                f"&#128228; <b>{_escape(lbl)}</b> response</summary>"
            )
            if result:
                html += f'<pre class="tool-data">{_escape(result)}</pre>'
            html += "</details>\n"
        elif kind == "agent_transfer":
            html += (
                '<div class="system">&#128256; <b>Agent Transfer:</b>'
                f" {_escape(item[1])}</div>\n"
            )
        elif kind == "custom_payload":
            html += (
                '<details class="tool-details">'
                '<summary class="tool-summary">'
                "&#128230; <b>Custom Payload</b></summary>"
                f'<pre class="tool-data">{_escape(item[1])}</pre>'
                "</details>\n"
            )
        elif kind == "callback_group":
            cb_lines = item[1]
            cb_items_html = "".join(
                f'<div class="system">{_escape(cbl)}</div>\n'
                for cbl in cb_lines
            )
            html += (
                '<details class="cb-spans-details latency-drawer">'
                '<summary><span class="pl-arrow">&#9654;</span>'
                f"&#9201; <b>Callback Spans ({len(cb_lines)})</b></summary>\n"
                f'<div class="cb-spans-list">\n{cb_items_html}</div>'
                "</details>\n"
            )
        elif kind == "turn_latency":
            html += item[1]
        else:
            html += f'<div class="system">{_escape(item[1])}</div>\n'
    return html


def _render_trace(
    trace: typing.Any,
    tools_map: typing.Any,
    turns: typing.Any,
    turn_latencies: typing.Any = None,
) -> typing.Any:
    """Render the conversation trace."""
    if not trace:
        return ""
    html = (
        f"<details open><summary>Conversation Trace "
        f"({turns} turns)</summary>\n"
        f'<div class="transcript">\n'
    )

    parsed_lines = _parse_trace(trace, tools_map)

    merged = _merge_trace_lines(parsed_lines)
    if turn_latencies:
        merged = _inject_turn_latency_items(merged, turn_latencies)

    html += _render_merged_items(merged)

    html += "</div>\n</details>\n"
    return html


def _get_run_detail(
    r: typing.Any, ces_base: typing.Any, tools_map: typing.Any
) -> typing.Any:
    """Return the HTML for a single run detail."""
    html = ""
    run_cls = "pass" if r.get("passed") else "fail"
    failed_str = "false" if r.get("passed") else "true"
    dur_str = (
        f" | {_fmt_duration(r['duration_s'])}" if r.get("duration_s") else ""
    )
    session_id = r.get("session_id", "")
    html += f'<details class="run-detail" data-failed="{failed_str}">\n'
    html += (
        f"<summary>Run {r['run']} — "
        f'<span class="{run_cls}">'
        f"{'PASS' if r.get('passed') else 'FAIL'}</span>"
    )
    naturalness = r.get("naturalness")
    naturalness_note = (
        f" | naturalness: {_escape(str(naturalness))}" if naturalness else ""
    )
    html += (
        f" | goals: {r.get('goals', '?')} | "
        f"expectations: {r.get('expectations', '?')} | "
        f"turns: {r.get('turns', '?')}{dur_str}{naturalness_note}</summary>\n"
    )

    html += _render_session_link(session_id, ces_base)

    sparams = r.get("session_parameters", {})
    html += _render_session_parameters(sparams)

    if r.get("_conv_latency_html"):
        html += r["_conv_latency_html"]

    if "error" in r:
        html += (
            f'<div class="expectation"><b>Error:</b> '
            f"{_escape(r['error'])}</div>\n"
        )
    else:
        html += _render_step_details(r.get("step_details", []))

        html += _render_expectation_details(r.get("expectation_details", []))

        html += _render_naturalness_details(r.get("naturalness_details"))

        html += _render_trace(
            r.get("detailed_trace", []),
            tools_map,
            r.get("turns", "?"),
            turn_latencies=r.get("_turn_latencies"),
        )

    html += "</details>\n"
    return html


def _upload_to_gcs(
    output_path: str,
    content: str,
    content_type: str = "text/html; charset=utf-8",
) -> str | None:
    """Uploads the report to GCS and returns the mTLS URL or None on failure."""
    try:
        gcs = gcs_utils.GCSUtils()
        mtls_url = gcs.upload_string(
            output_path, content, content_type=content_type
        )
        print(f"Report uploaded to GCS: {output_path}")
        print(f"Authenticated URL: {mtls_url}")
        return mtls_url
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"WARNING: GCS upload failed ({e}). Falling back to local file.")
        return None


def generate_html_report(
    results: list[dict[str, Any]],
    output_path: str,
    modality: str,
    model: str,
    app_name: str = "",
    wall_clock_s: float | None = None,
    user_agent_extension: str | None = None,
    audio_analysis: bool | None = None,
) -> None:
    """Generate an HTML report and save it locally or upload to GCS.

    If output_path starts with 'gs://', the report is uploaded to GCS.
    If the upload fails, it falls back to saving a local file.

    Args:
      results: The list of evaluation result dicts.
      output_path: The local or GCS file path to write the HTML report to.
      modality: The modality used for the evaluation (e.g., 'text').
      model: The model name used for the evaluation.
      app_name: The CX Agent Studio (CXAS) agent resource name.
      wall_clock_s: Total elapsed execution time in seconds.
      user_agent_extension: Optional user agent extension string.
    """
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    errors = sum(1 for r in results if "error" in r)

    conv_analyses = _attach_perceived_latency(
        results, app_name=app_name, output_path=output_path
    )
    suite_pl = (
        LatencyParser.analyze_suite_perceived_latency(conv_analyses)
        if conv_analyses
        else None
    )

    eval_stats = {}
    for r in results:
        n = r["name"]
        if n not in eval_stats:
            eval_stats[n] = {"pass": 0, "total": 0, "runs": []}
        eval_stats[n]["total"] += 1
        if r.get("passed"):
            eval_stats[n]["pass"] += 1
        eval_stats[n]["runs"].append(r)

    tools_map = {}
    if app_name:
        try:  # noqa: SIM105
            tools_map = tools.Tools(
                app_name=app_name, user_agent_extension=user_agent_extension
            ).get_tools_map()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    parts = app_name.split("/") if app_name else []
    project_id_idx = 1
    location_idx = 3
    app_id_idx = 5
    project_id = parts[project_id_idx] if len(parts) > project_id_idx else ""
    location = parts[location_idx] if len(parts) > location_idx else ""
    app_id = parts[app_id_idx] if len(parts) > app_id_idx else ""
    ces_base = (
        f"https://ces.cloud.google.com/projects/{project_id}/locations/{location}/apps/{app_id}"
        if app_id
        else ""
    )

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    suite_audio = _aggregate_suite_audio_analysis(
        results, [], audio_analysis=audio_analysis
    )

    overview_html = _get_summary_block(
        passed,
        total,
        errors,
        modality,
        model,
        ts,
        wall_clock_s,
        suite_pl=suite_pl,
    )
    overview_html += _get_results_table(eval_stats)
    overview_html += '\n<h2 id="section-sims">Eval Details</h2>\n'

    for name, s in sorted(
        eval_stats.items(), key=lambda x: x[1]["pass"] / max(x[1]["total"], 1)
    ):
        score = f"{s['pass']}/{s['total']}"
        is_all_pass = s["pass"] == s["total"]
        cls = "pass-bg" if is_all_pass else "fail-bg"
        passed_str = "true" if is_all_pass else "false"
        overview_html += (
            f'<div class="eval-card" id="eval-{name}" '
            f'data-passed="{passed_str}">\n'
        )
        overview_html += (
            f'<div class="eval-header {cls}">'
            f'<span>{_escape(name)} <span class="badge sim">sim</span></span>'
            f"<span>{score}</span></div>\n"
        )
        overview_html += '<div class="eval-body">\n'

        for r in s["runs"]:
            overview_html += _get_run_detail(r, ces_base, tools_map)
        overview_html += "</div></div>\n"

    mod = _get_combined_template_module()
    scaffold = str(
        mod.render_standalone_report_scaffold(overview_html, suite_audio)
    )
    html = f"{_get_html_head(ts)}{scaffold}</body></html>"

    if output_path.startswith("gs://"):
        mtls_url = _upload_to_gcs(output_path, html)
        if mtls_url:
            return

        # Fallback to local file if upload failed
        filename = output_path.rsplit("/", maxsplit=1)[-1]
        if not filename.endswith(".html"):
            filename = "report_fallback.html"
        output_path = filename

    with open(output_path, "w") as f:
        f.write(html)
    print(f"Report saved locally to: {output_path}")


def generate_combined_html_report(
    golden_results: list[dict[str, Any]] | None = None,
    sim_results: list[dict[str, Any]] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    callback_results: list[dict[str, Any]] | None = None,
    output_path: str = "",
    app_name: str = "",
    golden_modality: str = "text",
    sim_modality: str = "text",
    sim_wall_clock_s: float | None = None,
    user_agent_extension: str | None = None,
    bg_noise_file: str | None = None,
    bg_noise_snr: float | None = None,
    burst_noise_files: list[str] | None = None,
    traces_dir: str | None = None,
    audio_analysis: bool | None = None,
) -> str:
    """Generate combined HTML report based on results from multiple sources.

    Args:
      golden_results: The list of golden evaluation results.
      sim_results: The list of simulation evaluation results.
      tool_results: The list of tool evaluation results.
      callback_results: The list of callback evaluation results.
      output_path: The path to save the HTML report (local or GCS).
      app_name: CX Agent Studio (CXAS) agent resource name.
      golden_modality: The modality used for the golden evaluations.
      sim_modality: The modality used for the simulation evaluations.
      sim_wall_clock_s: Total elapsed execution time for simulations in seconds.
      user_agent_extension: Optional user agent extension string.
      bg_noise_file: Path to background noise audio file to play during
        replay.
      burst_noise_files: List of paths to burst noise audio files injected
        during replay.

    Returns:
      The resolved output path or URL where the report was saved.
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    g_total = len(golden_results) if golden_results else 0
    g_passed = (
        sum(1 for r in golden_results if r.get("passed"))
        if golden_results
        else 0
    )
    s_total = len(sim_results) if sim_results else 0
    s_passed = (
        sum(1 for r in sim_results if r.get("passed")) if sim_results else 0
    )
    t_total = len(tool_results) if tool_results else 0
    t_passed = (
        sum(1 for r in tool_results if r.get("passed")) if tool_results else 0
    )
    c_total = len(callback_results) if callback_results else 0
    c_passed = (
        sum(1 for r in callback_results if r.get("passed"))
        if callback_results
        else 0
    )
    total = g_total + s_total + t_total + c_total
    passed = g_passed + s_passed + t_passed + c_passed
    pct = 100 * passed / total if total else 0

    unified = []
    if golden_results:
        for r in golden_results:
            scores = [
                t["semantic_score"]
                for t in r.get("turns", [])
                if t.get("semantic_score") is not None
            ]
            avg_sem = sum(scores) / len(scores) if scores else None
            unified.append(
                {
                    "name": r["name"],
                    "type": "golden",
                    "passed": r.get("passed", False),
                    "score": "PASS" if r.get("passed") else "FAIL",
                    "detail": f"sem {avg_sem:.1f}/4"
                    if avg_sem is not None
                    else "",
                    "runs": 1,
                }
            )
    if sim_results:
        sim_stats = {}
        for r in sim_results:
            n = r["name"]
            if n not in sim_stats:
                sim_stats[n] = {"pass": 0, "total": 0, "runs": []}
            sim_stats[n]["total"] += 1
            if r.get("passed"):
                sim_stats[n]["pass"] += 1
            sim_stats[n]["runs"].append(r)
        for name, s in sim_stats.items():
            # The detail column is otherwise unused for sims; show the mean
            # naturalness score when the metric ran.
            nat_scores = [
                run["naturalness_details"]["overall_score"]
                for run in s["runs"]
                if isinstance(run.get("naturalness_details"), dict)
                and run["naturalness_details"].get("overall_score") is not None
            ]
            detail = ""
            if nat_scores:
                detail = (
                    f"naturalness {sum(nat_scores) / len(nat_scores):.1f}/5"
                )
            unified.append(
                {
                    "name": name,
                    "type": "sim",
                    "passed": s["pass"] == s["total"],
                    "score": f"{s['pass']}/{s['total']}",
                    "detail": detail,
                    "runs": s["total"],
                    "run_results": s["runs"],
                }
            )

    if tool_results:
        for r in tool_results:
            unified.append(
                {
                    "name": r["name"],
                    "type": "tool",
                    "passed": r.get("passed", False),
                    "score": r.get("status", "?"),
                    "detail": (
                        f"{r.get('latency_ms', 0):.0f}ms"
                        if r.get("latency_ms")
                        else ""
                    ),
                    "runs": 1,
                }
            )

    if callback_results:
        for r in callback_results:
            unified.append(
                {
                    "name": r["name"],
                    "type": "callback",
                    "passed": r.get("passed", False),
                    "score": r.get("status", "?"),
                    "detail": r.get("callback_type", ""),
                    "runs": 1,
                }
            )

    unified.sort(key=lambda x: (x["passed"], x["name"]))

    parts = app_name.split("/") if app_name else []
    project_id = parts[1] if len(parts) > 1 else ""
    location = parts[3] if len(parts) > 3 else ""
    app_id = parts[5] if len(parts) > 5 else ""
    ces_base = (
        f"https://ces.cloud.google.com/projects/{project_id}/locations/{location}/apps/{app_id}"
        if app_id
        else ""
    )

    # Compute summary cards data
    g_duration = (
        sum(r.get("duration_s", 0) or 0 for r in golden_results)
        if golden_results
        else 0
    )
    s_dur = (
        sum(r.get("duration_s", 0) or 0 for r in sim_results)
        if sim_results
        else 0
    )

    # --- FAILURE GROUPING ---
    failure_groups = {}

    # Collect golden failures
    if golden_results:
        for r in golden_results:
            if r.get("passed"):
                continue
            for turn in r.get("turns", []):
                for comp in turn.get("comparisons", []):
                    if comp.get("outcome") == "FAIL":
                        ctype = comp.get("type", "?")
                        expected = str(comp.get("expected", ""))[:60]
                        actual = str(comp.get("actual", ""))[:60]
                        if ctype == "transfer":
                            if actual == "(missed)":
                                reason = (
                                    "Routing missed: expected transfer to"
                                    f" {expected}"
                                )
                            else:
                                reason = (
                                    f"Wrong routing: expected {expected},"
                                    f" got {actual}"
                                )
                        elif ctype == "tool_call" and actual == "(missed)":
                            if expected:
                                reason = f"Tool not called: {expected}"
                            else:
                                continue
                        elif ctype == "tool_call" and expected != actual:
                            reason = (
                                f"Wrong tool: expected {expected}, got {actual}"
                            )
                        elif ctype == "text":
                            reason = "Semantic similarity too low"
                        else:
                            continue
                        failure_groups.setdefault(reason, set()).add(
                            ("golden", r["name"])
                        )
            for exp in r.get("expectations", []):
                if exp.get("status") == "Not Met":
                    reason = str(exp.get("expectation", ""))[:80]
                    failure_groups.setdefault(
                        f"Expectation not met: {reason}", set()
                    ).add(("golden", r["name"]))

    # Collect sim failures
    if sim_results:
        for r in sim_results:
            if r.get("passed"):
                continue
            for step in r.get("step_details", []):
                if step.get("status") != "Completed":
                    reason = f"Goal not completed: {step.get('goal', '')[:60]}"
                    failure_groups.setdefault(reason, set()).add(
                        ("sim", r["name"])
                    )
            for exp in r.get("expectation_details", []):
                if exp.get("status") == "Not Met":
                    reason = (
                        "Expectation not met: "
                        f"{exp.get('expectation', '')[:60]}"
                    )
                    failure_groups.setdefault(reason, set()).add(
                        ("sim", r["name"])
                    )

    # Collect tool test failures
    if tool_results:
        for r in tool_results:
            if r.get("passed"):
                continue
            errors = str(r.get("errors", ""))
            if (
                "operator='Operator.CONTAINS'" in errors
                and "expected='PASSED'" in errors
            ):
                reason = (
                    "Default expectation: $.result contains PASSED "
                    "(needs customization)"
                )
            elif "operator='Operator" in errors:
                reason = (
                    errors.split(",", maxsplit=1)[0][:80]
                    if "," in errors
                    else errors[:80]
                )
            else:
                reason = errors[:80]
            failure_groups.setdefault(reason, set()).add(("tool", r["name"]))

    # Collect callback failures
    if callback_results:
        for r in callback_results:
            if r.get("passed"):
                continue
            reason = str(r.get("error", "Unknown error"))[:80]
            failure_groups.setdefault(f"Callback: {reason}", set()).add(
                ("callback", r["name"])
            )

    # Prepare tools map for template if needed
    tools_map = {}
    if app_name:
        try:  # noqa: SIM105
            tools_map = tools.Tools(
                app_name=app_name, user_agent_extension=user_agent_extension
            ).get_tools_map()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # Compute Perceived Latency telemetry across golden and simulation results
    conv_analyses = []
    if golden_results:
        conv_analyses.extend(
            _attach_perceived_latency(
                golden_results,
                app_name=app_name,
                output_path=output_path or "",
                traces_dir=traces_dir,
            )
        )
    if sim_results:
        conv_analyses.extend(
            _attach_perceived_latency(
                sim_results,
                app_name=app_name,
                output_path=output_path or "",
                traces_dir=traces_dir,
            )
        )
    suite_pl = (
        LatencyParser.analyze_suite_perceived_latency(conv_analyses)
        if conv_analyses
        else None
    )

    # Suite Audio Analysis & Quality
    suite_audio = _aggregate_suite_audio_analysis(
        golden_results=golden_results,
        sim_results=sim_results,
        audio_analysis=audio_analysis,
    )
    suite_audio_html = _render_suite_audio_analysis(suite_audio)

    # Process traces for simulation results to simplify template
    if sim_results:
        for r in sim_results:
            trace = r.get("detailed_trace", [])
            if trace:
                parsed = []
                for entry in trace:
                    for raw_line in entry.split("\n"):
                        line = raw_line.strip()
                        if (
                            not line
                            or line.startswith("Agent Text (Diag):")
                            or line.startswith("User Query:")
                        ):
                            continue
                        for path, dname in tools_map.items():
                            line = line.replace(path, dname)
                        if line.startswith("Agent Text:"):
                            parsed.append(
                                ("agent", line[len("Agent Text:") :].strip())
                            )
                        elif line.startswith("User:"):
                            parsed.append(("user", line[5:].strip()))
                        elif line.startswith("Tool Call"):
                            parsed.append(("tool_call", line))
                        elif line.startswith("Tool Response"):
                            parsed.append(("tool_resp", line))
                        elif line.startswith("[Callback:") or line.startswith(
                            "Callback:"
                        ):
                            parsed.append(("callback_span", line))
                        else:
                            parsed.append(("system", line))

                merged: list[Any] = []
                for kind, text in parsed:
                    if kind == "agent" and merged and merged[-1][0] == "agent":
                        merged[-1] = ("agent", merged[-1][1] + " " + text)
                    elif (
                        kind == "tool_resp"
                        and merged
                        and merged[-1][0] == "tool_call"
                    ):
                        merged[-1] = ("tool_pair", merged[-1][1], text)
                    elif kind == "callback_span":
                        if merged and merged[-1][0] == "callback_group":
                            merged[-1][1].append(text)
                        else:
                            merged.append(("callback_group", [text]))
                    else:
                        merged.append((kind, text))
                r["_processed_trace"] = _inject_turn_latency_items(
                    merged, r.get("_turn_latencies")
                )

    # Compile Tool evaluation table via Python Component
    if tool_results:
        t_pct = 100 * t_passed / t_total if t_total else 0
        t_pct_str = f"{t_pct:.0f}"
        rows = [
            report_components.ToolRow(
                passed_str="true" if r["passed"] else "false",
                status_class="pass" if r["passed"] else "fail",
                status=r.get("status", "?"),
                tool_name=r.get("tool", "?"),
                test_name=r.get("name", "?"),
                latency=(
                    f"{r.get('latency_ms', 0):.0f}ms"
                    if r.get("latency_ms")
                    else "-"
                ),
                errors=str(r.get("errors", ""))[:100],
            )
            for r in sorted(tool_results, key=lambda x: x.get("passed", False))
        ]

    if tool_results:
        tool_results_html = report_components.ToolCard(
            passed=t_passed,
            total=t_total,
            pct_str=t_pct_str,
            tool_rows=base_components.Raw(
                "\n".join(row.render() for row in rows)
            ),
        ).render()
    else:
        tool_results_html = ""

    # Compile Callback evaluation table via Python Component
    if callback_results:
        c_pct = 100 * c_passed / c_total if c_total else 0
        c_pct_str = f"{c_pct:.0f}"
        rows = [
            report_components.CallbackRow(
                passed_str="true" if r["passed"] else "false",
                status_class="pass" if r["passed"] else "fail",
                status=r.get("status", "?"),
                agent_name=r.get("agent", "?"),
                callback_type=r.get("callback_type", "?"),
                test_name=r.get("name", "?"),
                error=str(r.get("error", ""))[:100],
            )
            for r in sorted(
                callback_results, key=lambda x: x.get("passed", False)
            )
        ]

    if callback_results:
        callback_results_html = report_components.CallbackCard(
            passed=c_passed,
            total=c_total,
            pct_str=c_pct_str,
            callback_rows=base_components.Raw(
                "\n".join(row.render() for row in rows)
            ),
        ).render()
    else:
        callback_results_html = ""

    f_patterns = report_components.FailurePatterns(
        failure_groups=failure_groups
    )
    failure_patterns_html = f_patterns.render()
    suite_latency_html = (
        _render_suite_latency_drawer(suite_pl) if suite_pl else ""
    )
    template = _get_combined_report_template()
    html = template.render(
        failure_patterns_html=failure_patterns_html,
        suite_latency_html=suite_latency_html,
        suite_pl=suite_pl,
        trend_chart=_prepare_trend_chart(suite_pl.get("turn_trend_buckets", []))
        if suite_pl
        else None,
        _fmt_ms=_fmt_ms,
        _pl_status_cls=_pl_status_cls,
        suite_audio=suite_audio,
        suite_audio_html=suite_audio_html,
        ts=ts,
        pct=pct,
        passed=passed,
        total=total,
        unified=unified,
        golden_results=golden_results or [],
        sim_results=sim_results or [],
        tool_results=tool_results or [],
        callback_results=callback_results or [],
        ces_base=ces_base,
        golden_modality=golden_modality,
        sim_modality=sim_modality,
        sim_wall_clock_s=sim_wall_clock_s,
        failure_groups=failure_groups,
        g_passed=g_passed,
        g_total=g_total,
        g_duration=g_duration,
        s_passed=s_passed,
        s_total=s_total,
        s_dur=s_dur,
        t_passed=t_passed,
        t_total=t_total,
        c_passed=c_passed,
        c_total=c_total,
        _escape=_escape,
        _fmt_duration=_fmt_duration,
        json=json,
        bg_noise_file=(
            os.path.basename(bg_noise_file) if bg_noise_file else None
        ),
        bg_noise_snr=bg_noise_snr,
        snr_to_noise_level=snr_to_noise_level,
        burst_noise_files=burst_noise_files,
        tool_results_html=tool_results_html,
        callback_results_html=callback_results_html,
    )

    # Wrap compiled body dynamically in declarative BaseShell scaffold envelope.
    report = report_components.BaseShell(
        title=f"Eval Report - {ts}",
        body_content=[base_components.Raw(html)],
    )
    html_out = report.render()

    if output_path:
        if output_path.startswith("gs://"):
            mtls_url = _upload_to_gcs(output_path, html_out)
            if not mtls_url:
                # Fallback to local file if upload failed
                filename = output_path.rsplit("/", maxsplit=1)[-1]
                if not filename.endswith(".html"):
                    filename = "report_fallback.html"
                output_path = filename
                with open(output_path, "w") as f:
                    f.write(html_out)
            else:
                output_path = mtls_url
        else:
            with open(output_path, "w") as f:
                f.write(html_out)

    return output_path


def _sanitize_for_json(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip internal keys (prefixed with '_') from result dicts."""
    return [
        {k: v for k, v in r.items() if not str(k).startswith("_")}
        for r in results
    ]


def generate_combined_json_report(
    golden_results: list[dict[str, Any]] | None = None,
    sim_results: list[dict[str, Any]] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    callback_results: list[dict[str, Any]] | None = None,
    output_path: str = "",
    app_name: str = "",
    golden_modality: str = "text",
    sim_modality: str = "text",
    sim_wall_clock_s: float | None = None,
    audio_analysis: bool | None = None,
) -> str:
    """Generate a combined JSON report based on results from multiple sources.

    Emits the same underlying evaluation data as the combined HTML report,
    but as a single machine-readable JSON document so downstream consumers
    do not need to scrape the HTML.

    Args:
      golden_results: The list of golden evaluation results.
      sim_results: The list of simulation evaluation results.
      tool_results: The list of tool evaluation results.
      callback_results: The list of callback evaluation results.
      output_path: The path to save the JSON report (local or GCS).
      app_name: CX Agent Studio (CXAS) agent resource name.
      golden_modality: The modality used for the golden evaluations.
      sim_modality: The modality used for the simulation evaluations.
      sim_wall_clock_s: Total elapsed execution time for simulations in
        seconds.

    Returns:
      The resolved output path or URL where the report was saved.
    """
    golden_results = golden_results or []
    sim_results = sim_results or []
    tool_results = tool_results or []
    callback_results = callback_results or []

    def _counts(results: typing.Any) -> typing.Any:
        return {
            "total": len(results),
            "passed": sum(1 for r in results if r.get("passed")),
        }

    per_type = {
        "golden": _counts(golden_results),
        "simulation": _counts(sim_results),
        "tool": _counts(tool_results),
        "callback": _counts(callback_results),
    }
    total = sum(c["total"] for c in per_type.values())
    passed = sum(c["passed"] for c in per_type.values())

    suite_audio = _aggregate_suite_audio_analysis(
        golden_results=golden_results,
        sim_results=sim_results,
        audio_analysis=audio_analysis,
    )

    report = {
        "schema_version": 1,
        "generated_at": datetime.datetime.now().isoformat(),
        "app_name": app_name,
        "modality": {
            "golden": golden_modality,
            "simulation": sim_modality,
        },
        "sim_wall_clock_s": sim_wall_clock_s,
        "summary": {
            "total": total,
            "passed": passed,
            "pass_rate_pct": round(100 * passed / total, 2) if total else 0,
            **per_type,
            "audio_analysis": suite_audio,
        },
        "results": {
            "golden": _sanitize_for_json(golden_results),
            "simulation": _sanitize_for_json(sim_results),
            "tool": _sanitize_for_json(tool_results),
            "callback": _sanitize_for_json(callback_results),
        },
    }
    json_out = json.dumps(report, indent=2, default=str)

    if output_path:
        if output_path.startswith("gs://"):
            mtls_url = _upload_to_gcs(
                output_path, json_out, content_type="application/json"
            )
            if not mtls_url:
                # Fallback to local file if upload failed
                filename = output_path.rsplit("/", maxsplit=1)[-1]
                if not filename.endswith(".json"):
                    filename = "report_fallback.json"
                output_path = filename
                with open(output_path, "w") as f:
                    f.write(json_out)
            else:
                output_path = mtls_url
        else:
            with open(output_path, "w") as f:
                f.write(json_out)

    return output_path


def _outcome_str(val: typing.Any) -> typing.Any:
    if isinstance(val, int):
        return {0: "UNSPECIFIED", 1: "PASS", 2: "FAIL"}.get(val, f"?{val}")
    return str(val) if val else "?"


def _compile_tool_results_card(
    *,
    tool_results: Sequence[Mapping[str, Any]],
    t_passed: int,
    t_total: int,
) -> report_components.ToolCard | str:
    """Compile the ToolCard component declaratively without premature rendering.

    Args:
      tool_results: Sequence of raw tool validation outcomes..
      t_passed: Number of successful tool test cases..
      t_total: Total number of tool test cases executed..

    Returns:
      A ToolCard component or empty string if empty.
    """
    if not tool_results:
        return ""
    t_pct = 100 * t_passed / t_total if t_total else 0
    rows = (
        report_components.ToolRow(
            passed=r["passed"],
            status_class="pass" if r["passed"] else "fail",
            status=r.get("status", "?"),
            tool_name=r.get("tool", "?"),
            test_name=r.get("name", "?"),
            latency_ms=r.get("latency_ms"),
            errors=r.get("errors", "")[:100],
        )
        for r in sorted(tool_results, key=lambda x: x.get("passed", False))
    )
    return report_components.ToolCard(
        passed=t_passed,
        total=t_total,
        pct_str=f"{t_pct:.0f}",
        tool_rows=base_components.ComponentGroup(list(rows)),
    )


def _compile_callback_results_card(
    *,
    callback_results: Sequence[Mapping[str, Any]],
    c_passed: int,
    c_total: int,
) -> report_components.CallbackCard | str:
    """Compile the CallbackCard component declaratively without premature

    rendering.

    Args:
      callback_results: Sequence of raw callback execution outcomes..
      c_passed: Number of successful callback test cases..
      c_total: Total number of callback test cases executed..

    Returns:
      A CallbackCard component or empty string if empty.
    """
    if not callback_results:
        return ""
    c_pct = 100 * c_passed / c_total if c_total else 0
    rows = (
        report_components.CallbackRow(
            passed=r["passed"],
            status_class="pass" if r["passed"] else "fail",
            status=r.get("status", "?"),
            agent_name=r.get("agent", "?"),
            callback_type=r.get("callback_type", "?"),
            test_name=r.get("name", "?"),
            error=r.get("error", "")[:100],
        )
        for r in sorted(callback_results, key=lambda x: x.get("passed", False))
    )
    return report_components.CallbackCard(
        passed=c_passed,
        total=c_total,
        pct_str=f"{c_pct:.0f}",
        callback_rows=base_components.ComponentGroup(list(rows)),
    )


def _build_turn_comparisons(
    turn: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build the expected/actual comparison rows for one replayed turn.

    Each ``expectation_outcome`` entry pairs an expected element with what was
    observed. An entry can also carry an *observed* agent response with no
    expectation at all: this happens when the agent says more than the golden
    modeled -- e.g. a short filler said before a tool call is matched to the
    expected reply, and the substantive answer produced after the tool returns
    is left unmatched. Those observed-only responses must still be rendered,
    otherwise the report looks like the agent never said them even though the
    platform recorded and the scorer evaluated the full turn.
    """
    comparisons = []
    for o in turn.get("expectation_outcome", []):
        exp = o.get("expectation", {})
        comp = {"outcome": _outcome_str(o.get("outcome"))}

        if "agent_response" in exp:
            comp["type"] = "text"
            comp["expected"] = _join_chunk_text(
                exp["agent_response"].get("chunks", [])
            )
            obs = o.get("observed_agent_response", {})
            comp["actual"] = (
                _join_chunk_text(obs.get("chunks", [])) if obs else "(missed)"
            )
        elif "tool_call" in exp:
            tc = exp["tool_call"]
            comp["type"] = "tool_call"
            comp["expected"] = (
                tc.get("display_name") or tc.get("tool", "").split("/")[-1]
            )
            comp["expected_args"] = tc.get("args", {})
            obs = o.get("observed_tool_call", {})
            comp["actual"] = (
                (obs.get("display_name") or obs.get("tool", "").split("/")[-1])
                if obs
                else "(missed)"
            )
            comp["actual_args"] = obs.get("args", {}) if obs else {}
            tir = o.get("toolInvocationResult", {})
            comp["tool_invocation_score"] = tir.get("parameterCorrectnessScore")
            comp["tool_invocation_explanation"] = tir.get("explanation")
        elif "agent_transfer" in exp:
            at = exp["agent_transfer"]
            comp["type"] = "transfer"
            comp["expected"] = at.get(
                "display_name", at.get("target_agent", "").split("/")[-1]
            )
            obs = o.get("observed_agent_transfer", {})
            comp["actual"] = (
                obs.get(
                    "display_name",
                    obs.get("target_agent", "").split("/")[-1],
                )
                if obs
                else "(missed)"
            )
        elif "tool_response" in exp:
            continue
        elif o.get("observed_agent_response"):
            comp["type"] = "text"
            comp["expected"] = "(none)"
            comp["actual"] = _join_chunk_text(
                o["observed_agent_response"].get("chunks", [])
            )
        else:
            continue

        comparisons.append(comp)
    return comparisons


def load_golden_results(
    run_id: str | list[str],
    app_name: str,
    include: list[str] | None = None,
    min_run_passes: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch golden results and parse into report-friendly format.

    Args:
      run_id: Evaluation run ID (or comma-separated IDs / list of IDs) to load.
      app_name: CX Agent Studio (CXAS) agent resource name.
      include: Categories of evaluations to include (e.g. 'goldens',
        'scenarios').
      min_run_passes: Optional k-of-N pass threshold when reconciling multiple
        evaluation runs.

    Returns:
      A list of formatted evaluation result dictionaries.
    """
    if include is None:
        include = ["goldens", "scenarios"]

    if isinstance(run_id, str):
        run_ids = [rid.strip() for rid in run_id.split(",") if rid.strip()]
    else:
        run_ids = [str(rid).strip() for rid in run_id if str(rid).strip()]

    utils = eval_utils.EvalUtils(app_name=app_name)
    raw_results = []
    for rid in run_ids:
        full_run_id = (
            rid
            if rid.startswith("projects/")
            else f"{app_name}/evaluationRuns/{rid}"
        )
        raw_results.extend(utils.list_evaluation_results_by_run(full_run_id))

    evals_map = utils.get_evaluations_map(app_name, reverse=False)
    name_lookup = {}
    for cat in ["goldens", "scenarios"]:
        for resource, display in evals_map.get(cat, {}).items():
            name_lookup[resource] = display

    evals_by_resource: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        for ev_item in utils.list_evaluations(app_name) or []:
            ev_name = getattr(ev_item, "name", "")
            if ev_name:
                evals_by_resource[ev_name] = ev_item

    results = []
    for r in raw_results:
        rd = type(r).to_dict(r)
        result_name = rd.get("name", "")
        eval_resource = "/".join(result_name.split("/")[:-2])

        is_golden = eval_resource in evals_map.get("goldens", {})
        is_scenario = eval_resource in evals_map.get("scenarios", {})

        if is_golden and "goldens" not in include:
            continue
        if is_scenario and "scenarios" not in include:
            continue

        display_name = name_lookup.get(
            eval_resource, eval_resource.split("/")[-1]
        )

        status_raw = rd.get("evaluation_status", 0)
        passed = (
            (status_raw == 1)
            if isinstance(status_raw, int)
            else str(status_raw).upper() == "PASS"
        )

        golden = rd.get("golden_result", {})

        turns = []
        for i, turn in enumerate(golden.get("turn_replay_results", [])):
            sem = turn.get("semantic_similarity_result", {})
            turn_data = {
                "index": i + 1,
                "semantic_score": sem.get("score"),
                "semantic_explanation": sem.get("explanation"),
                "comparisons": _build_turn_comparisons(turn),
            }
            turns.append(turn_data)

        expectations = []
        for ee in golden.get("evaluation_expectation_results", []):
            result_val = ee.get("outcome", ee.get("result"))
            exp_text = ee.get("prompt", ee.get("evaluation_expectation", ""))
            explanation = ee.get("explanation", "")
            met = (
                result_val == 1
                if isinstance(result_val, int)
                else str(result_val).upper() == "PASS"
            )
            expectations.append(
                {
                    "expectation": exp_text,
                    "status": "Met" if met else "Not Met",
                    "justification": explanation,
                }
            )

        session_id = ""
        if golden.get("turn_replay_results"):
            conv_path = golden["turn_replay_results"][0].get("conversation", "")
            if conv_path:
                # Extract the conversation ID (e.g. "evaluation-xxxx")
                session_id = conv_path.split("/")[-1]

        session_params = {}
        # One entry per golden turn: ("text", "...") or ("event", "...")
        turn_inputs = []
        try:
            ev_obj = evals_by_resource.get(eval_resource)
            if ev_obj is None:
                ev_obj = utils.get_evaluation(eval_resource)
                evals_by_resource[eval_resource] = ev_obj
            evd = type(ev_obj).to_dict(ev_obj)
            golden_def = evd.get("golden", {})
            for turn_def in golden_def.get("turns", []):
                turn_input = None
                for step in turn_def.get("steps", []):
                    ui = step.get("user_input", {})
                    if "variables" in ui:
                        session_params.update(ui["variables"])
                    if "text" in ui:
                        turn_input = ("text", ui["text"])
                    elif "event" in ui:
                        turn_input = ("event", str(ui["event"]))
                if turn_input:
                    turn_inputs.append(turn_input)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        for i, turn in enumerate(turns):
            if i < len(turn_inputs):
                kind, text = turn_inputs[i]
                turn["user_input"] = text if kind == "text" else None
            else:
                turn["user_input"] = None

        total_latency_s = 0
        for turn_result in golden.get("turn_replay_results", []):
            lat = turn_result.get("turn_latency", "")
            if isinstance(lat, str) and lat.endswith("s"):
                with contextlib.suppress(ValueError):
                    total_latency_s += float(lat.replace("s", ""))
            elif isinstance(lat, dict):
                total_latency_s += lat.get("seconds", 0) + (
                    lat.get("nanos", 0) / 1e9
                )

        results.append(
            {
                "name": display_name,
                "passed": passed,
                "turns": turns,
                "expectations": expectations,
                "session_id": session_id,
                "session_parameters": session_params,
                "duration_s": (
                    round(total_latency_s, 1) if total_latency_s > 0 else None
                ),
            }
        )

    if min_run_passes is not None or len(run_ids) > 1:
        return reconcile_golden_runs(
            results, min_run_passes=min_run_passes or 1
        )

    return results


def reconcile_golden_runs(
    results: list[dict[str, Any]],
    min_run_passes: int = 1,
) -> list[dict[str, Any]]:
    """Reconcile multiple golden runs per evaluation using k-of-N voting."""
    by_name: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for r in results:
        name = r.get("name", "?")
        if name not in by_name:
            by_name[name] = []
            order.append(name)
        by_name[name].append(r)

    reconciled: list[dict[str, Any]] = []
    for name in order:
        runs = by_name[name]
        passes = sum(1 for r in runs if r.get("passed"))
        passed = passes >= min_run_passes
        failing = [r for r in runs if not r.get("passed")]
        passing = [r for r in runs if r.get("passed")]
        chosen = dict(
            passing[0]
            if passed and passing
            else (failing[0] if failing else runs[0])
        )
        chosen["passed"] = passed
        chosen["passes"] = passes
        chosen["runs"] = len(runs)
        if failing:
            chosen["failed_runs"] = len(failing)
        reconciled.append(chosen)
    return reconciled


def _merge_naturalness(common: Any, case: Any) -> Any:
    """Merges a file-level naturalness block with a per-eval override.

    The per-eval value always wins: an explicit ``false`` opts a single eval
    out of a file-wide metric, and a mapping is merged key-by-key over the
    common block.
    """
    if case is None:
        return common
    if isinstance(case, bool) or not isinstance(case, dict):
        return case
    base = dict(common) if isinstance(common, dict) else {}
    base.update(case)
    return base


def _load_sim_test_cases(yaml_path: str) -> list[dict[str, Any]]:
    """Loads sim files and merges common params and expectations.

    Args:
      yaml_path: Path to the YAML test cases file.

    Returns:
      List of merged evaluation test case dicts.
    """
    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}
    if isinstance(data, list):
        return data

    common_session_params = (
        data.get("common_session_parameters")
        if isinstance(data.get("common_session_parameters"), dict)
        else {}
    )
    common_params_dict = (
        data.get("common_params")
        if isinstance(data.get("common_params"), dict)
        else {}
    )

    # Merge session parameters: common_session_parameters or common_params
    merged_common_session_params = common_session_params.copy()
    if "session_parameters" in common_params_dict and isinstance(
        common_params_dict["session_parameters"], dict
    ):
        merged_common_session_params.update(
            common_params_dict["session_parameters"]
        )
    else:
        # Non-noise, non-meta keys in common_params act as
        # common session parameters
        meta_and_noise_keys = {
            "background_noise",
            "background_noise_file",
            "common_background_noise",
            "bg_noise_level",
            "common_bg_noise_level",
            "noise_level",
            "bg_noise_snr",
            "common_bg_noise_snr",
            "burst_noise",
            "common_burst_noise",
            "burst_noise_files",
            "common_burst_noise_files",
            "burst_noise_level",
            "common_burst_noise_level",
            "burst_noise_snr",
            "common_burst_noise_snr",
            "config",
            "common_config",
            "expectations",
            "common_expectations",
            "naturalness_metric",
            "common_naturalness_metric",
        }
        for k, v in common_params_dict.items():
            if k not in meta_and_noise_keys:
                merged_common_session_params[k] = v

    common_expectations = data.get("common_expectations", []) or []
    common_naturalness = data.get("common_naturalness_metric")

    common_config = {}
    if isinstance(data.get("common_config"), dict):
        common_config = data["common_config"]
    elif isinstance(data.get("config"), dict):
        common_config = data["config"]

    # Extract common noise settings from data, common_params, or common_config
    def _find_common(keys: list[str]) -> Any:
        for src in (data, common_params_dict, common_config):
            if isinstance(src, dict):
                for k in keys:
                    if k in src and src[k] is not None:
                        return src[k]
        return None

    common_bg_noise = _find_common(
        ["common_background_noise", "background_noise", "background_noise_file"]
    )
    common_bg_level = _find_common(
        ["common_bg_noise_level", "bg_noise_level", "noise_level"]
    )
    common_bg_snr = _find_common(["common_bg_noise_snr", "bg_noise_snr"])
    common_burst_noise = _find_common(
        [
            "common_burst_noise",
            "burst_noise",
            "burst_noise_files",
            "common_burst_noise_files",
        ]
    )
    common_burst_level = _find_common(
        ["common_burst_noise_level", "burst_noise_level"]
    )
    common_burst_snr = _find_common(
        ["common_burst_noise_snr", "burst_noise_snr"]
    )

    cases = data.get("evals", [])
    if not isinstance(cases, list):
        return []

    merged_cases = []
    for c in cases:
        if isinstance(c, dict):
            case_copy = c.copy()
            # Merge session parameters
            case_params = case_copy.get("session_parameters", {}) or {}
            merged_params = merged_common_session_params.copy()
            merged_params.update(case_params)
            case_copy["session_parameters"] = merged_params

            # Merge config
            if common_config:
                case_cfg = (
                    case_copy.get("config").copy()
                    if isinstance(case_copy.get("config"), dict)
                    else {}
                )
                merged_cfg = common_config.copy()
                merged_cfg.update(case_cfg)
                case_copy["config"] = merged_cfg

            # Merge background noise defaults if present
            case_bg = case_copy.get("background_noise")
            case_cfg_bg = (
                case_copy["config"].get("background_noise")
                if isinstance(case_copy.get("config"), dict)
                else None
            )

            # Check if this case explicitly opted out of background noise
            if case_bg is False or case_cfg_bg is False:
                case_copy["background_noise"] = False
                if "config" not in case_copy or not isinstance(
                    case_copy["config"], dict
                ):
                    case_copy["config"] = {}
                case_copy["config"]["background_noise"] = False
            else:
                # Merge background_noise: if both common and case are dicts,
                # merge them
                if isinstance(common_bg_noise, dict) and isinstance(
                    case_bg, dict
                ):
                    merged_bg = common_bg_noise.copy()
                    merged_bg.update(case_bg)
                    case_copy["background_noise"] = merged_bg
                elif case_bg is not None:
                    case_copy["background_noise"] = case_bg
                elif common_bg_noise is not None:
                    case_copy["background_noise"] = (
                        common_bg_noise.copy()
                        if isinstance(common_bg_noise, dict)
                        else common_bg_noise
                    )

                # Propagate common bg noise level and SNR if not specified
                if common_bg_level is not None and (
                    "bg_noise_level" not in case_copy
                    and (
                        not isinstance(case_copy.get("config"), dict)
                        or "bg_noise_level" not in case_copy["config"]
                    )
                ):
                    case_copy["bg_noise_level"] = common_bg_level

                if common_bg_snr is not None and (
                    "bg_noise_snr" not in case_copy
                    and (
                        not isinstance(case_copy.get("config"), dict)
                        or "bg_noise_snr" not in case_copy["config"]
                    )
                ):
                    case_copy["bg_noise_snr"] = common_bg_snr

                # Merge burst noise
                case_burst = case_copy.get("burst_noise")
                case_cfg_burst = (
                    case_copy["config"].get("burst_noise")
                    if isinstance(case_copy.get("config"), dict)
                    else None
                )
                if case_burst is False or case_cfg_burst is False:
                    case_copy["burst_noise"] = False
                else:
                    if isinstance(common_burst_noise, dict) and isinstance(
                        case_burst, dict
                    ):
                        merged_burst = common_burst_noise.copy()
                        merged_burst.update(case_burst)
                        case_copy["burst_noise"] = merged_burst
                    elif case_burst is not None:
                        case_copy["burst_noise"] = case_burst
                    elif common_burst_noise is not None:
                        case_copy["burst_noise"] = (
                            common_burst_noise.copy()
                            if isinstance(common_burst_noise, dict)
                            else common_burst_noise
                        )

                    if common_burst_level is not None and (
                        "burst_noise_level" not in case_copy
                        and (
                            not isinstance(case_copy.get("config"), dict)
                            or "burst_noise_level" not in case_copy["config"]
                        )
                    ):
                        case_copy["burst_noise_level"] = common_burst_level

                    if common_burst_snr is not None and (
                        "burst_noise_snr" not in case_copy
                        and (
                            not isinstance(case_copy.get("config"), dict)
                            or "burst_noise_snr" not in case_copy["config"]
                        )
                    ):
                        case_copy["burst_noise_snr"] = common_burst_snr

            # Merge expectations
            case_expectations = case_copy.get("expectations", []) or []
            case_copy["expectations"] = common_expectations + case_expectations

            # Merge the optional naturalness metric block. A per-eval block
            # wins key-by-key, and an explicit `false` opts that eval out.
            if common_naturalness is not None:
                case_copy["naturalness_metric"] = _merge_naturalness(
                    common_naturalness,
                    case_copy.get("naturalness_metric"),
                )

            merged_cases.append(case_copy)
    return merged_cases


def load_sim_results(
    json_path: str, sim_evals_yaml: str | None = None
) -> typing.Any:
    """Load sim results from JSON file.

    Handles both old (list) and new (envelope) formats.

    Args:
      json_path: The JSON file path containing evaluation results.
      sim_evals_yaml: Optional path to simulation evals YAML definition file.

    Returns:
      A tuple containing the list of simulation results and the wall clock time.
    """
    with open(json_path) as f:
        data = json.load(f)

    wall_clock_s = None
    # New envelope format: {"wall_clock_s": N, "results": [...]}
    # Old format: [...]
    if isinstance(data, dict):
        wall_clock_s = data.get("wall_clock_s")
        results = data.get("results", [])
    else:
        results = data

    # Backfill session_parameters if missing
    if sim_evals_yaml:
        try:
            eval_list = _load_sim_test_cases(sim_evals_yaml)
            templates = {
                e["name"]: e
                for e in eval_list
                if isinstance(e, dict) and "name" in e
            }
            for r in results:
                if "session_parameters" not in r and r.get("name") in templates:
                    r["session_parameters"] = templates[r["name"]].get(
                        "session_parameters", {}
                    )
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    return results, wall_clock_s


def load_tool_test_results(csv_or_json_path: str) -> list[dict[str, Any]]:
    """Load tool test results from a CSV or JSON file.

    Args:
      csv_or_json_path: Path to the CSV or JSON tool test results file.

    Returns:
      A list of formatted tool test result dictionaries.
    """
    if csv_or_json_path.endswith(".csv"):
        df = pd.read_csv(csv_or_json_path)
    else:
        df = pd.read_json(csv_or_json_path)
    results = []
    for _, row in df.iterrows():
        results.append(
            {
                "name": row.get("test_name", row.get("test", "?")),
                "tool": row.get("tool", "?"),
                "passed": row.get("status", "").upper() == "PASSED",
                "status": row.get("status", "?"),
                "latency_ms": row.get("latency (ms)", 0),
                "errors": row.get("errors", ""),
            }
        )
    return results


def load_callback_test_results(csv_or_json_path: str) -> list[dict[str, Any]]:
    """Load callback test results from a CSV or JSON file.

    Args:
      csv_or_json_path: Path to the CSV or JSON callback test results file.

    Returns:
      A list of formatted callback test result dictionaries.
    """
    if csv_or_json_path.endswith(".csv"):
        df = pd.read_csv(csv_or_json_path)
    else:
        df = pd.read_json(csv_or_json_path)
    results = []
    for _, row in df.iterrows():
        results.append(
            {
                "name": row.get("test_name", "?"),
                "agent": row.get("agent_name", "?"),
                "callback_type": row.get("callback_type", "?"),
                "passed": row.get("status", "").upper() == "PASSED",
                "status": row.get("status", "?"),
                "error": row.get("error_message", ""),
            }
        )
    return results


def summarize_and_gate_evals(
    sim_results: list[dict[str, Any]] | None = None,
    golden_results: list[dict[str, Any]] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    callback_results: list[dict[str, Any]] | None = None,
    min_run_passes: int = 1,
    min_pass_rate: float | None = None,
) -> dict[str, Any]:
    """Compute k-of-N case pass rates and evaluate gate criteria."""
    all_cases: list[dict[str, Any]] = []

    def _collect_grouped(
        items: list[dict[str, Any]] | None, category: str
    ) -> None:
        if not items:
            return
        by_name: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for item in items:
            name = str(item.get("name", item.get("test_name", "?")))
            if name not in by_name:
                by_name[name] = []
                order.append(name)
            by_name[name].append(item)

        for name in order:
            runs = by_name[name]
            if len(runs) == 1 and "passes" in runs[0] and "runs" in runs[0]:
                passes = int(runs[0]["passes"])
                total_runs = int(runs[0]["runs"])
            else:
                passes = sum(1 for r in runs if r.get("passed"))
                total_runs = len(runs)
            passed = passes >= min_run_passes
            failed_expectations: list[str] = []
            for r in runs:
                for exp in r.get("expectations", []) or []:
                    if exp.get("status") != "Met":
                        exp_name = exp.get("expectation", "")
                        if exp_name and exp_name not in failed_expectations:
                            failed_expectations.append(exp_name)
            all_cases.append(
                {
                    "name": name,
                    "category": category,
                    "passed": passed,
                    "passes": passes,
                    "runs": total_runs,
                    "failed_expectations": failed_expectations,
                }
            )

    _collect_grouped(sim_results, "simulation")
    _collect_grouped(golden_results, "golden")
    _collect_grouped(tool_results, "tool")
    _collect_grouped(callback_results, "callback")

    total = len(all_cases)
    passed_count = sum(1 for c in all_cases if c["passed"])
    pass_rate = (passed_count / total) if total > 0 else 0.0
    max_runs = max((c["runs"] for c in all_cases), default=1)

    for c in all_cases:
        tag = "PASS" if c["passed"] else "FAIL"
        print(f"  [{tag}] {c['name']} ({c['passes']}/{c['runs']} runs)")
        if not c["passed"]:
            for exp_text in c["failed_expectations"]:
                print(f"         NOT MET: {exp_text}")

    rule_label = (
        f" ({min_run_passes}-of-{max_runs})"
        if max_runs > 1 or min_run_passes > 1
        else ""
    )
    print(f"----- {passed_count}/{total} cases passed{rule_label} -----")

    gate_passed = True
    gate_reason = ""
    if min_pass_rate is not None:
        if total == 0:
            gate_passed = False
            gate_reason = "No evaluations ran — failing closed"
            print(f"::error::{gate_reason}")
        elif pass_rate < min_pass_rate:
            gate_passed = False
            gate_reason = (
                f"Pass rate {passed_count}/{total} ({pass_rate:.0%}) "
                f"is below threshold {min_pass_rate:.0%}"
            )
            print(f"::error::{gate_reason}")

    return {
        "passed_cases": passed_count,
        "total_cases": total,
        "pass_rate": pass_rate,
        "min_run_passes": min_run_passes,
        "min_pass_rate": min_pass_rate,
        "gate_passed": gate_passed,
        "gate_reason": gate_reason,
        "cases": all_cases,
    }


def write_eval_stamp(
    output_dir: str,
    suite_name: str,
    gate_summary: dict[str, Any],
    modality: str = "text",
    runs: int = 1,
) -> str:
    """Write an EVAL_STAMP-<suite>.txt metadata file into output_dir."""
    safe_suite = (
        re.sub(r"[^a-zA-Z0-9._-]+", "-", suite_name).strip("-") or "evals"
    )
    stamp_path = os.path.join(output_dir, f"EVAL_STAMP-{safe_suite}.txt")
    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    lines = [
        f"suite:         {suite_name}",
        f"modality:      {modality}",
        f"runs:          {runs}",
        f"min_passes:    {gate_summary.get('min_run_passes', 1)}",
        f"min_pass_rate: {gate_summary.get('min_pass_rate')}",
        (
            f"cases_passed:  {gate_summary.get('passed_cases', 0)}/"
            f"{gate_summary.get('total_cases', 0)}"
        ),
        f"pass_rate:     {gate_summary.get('pass_rate', 0.0):.2%}",
        f"gate_passed:   {gate_summary.get('gate_passed', True)}",
        f"git_sha:       {os.environ.get('GITHUB_SHA', 'local')}",
        f"git_ref:       {os.environ.get('GITHUB_REF_NAME', 'local')}",
        f"run_id:        {os.environ.get('GITHUB_RUN_ID', 'local')}",
        f"timestamp_utc: {now_utc}",
    ]
    with _ORIG_OPEN(stamp_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return stamp_path


def publish_eval_report_bundle(
    output_dir: str,
    report_path: str,
    gcs_bundle_uri: str,
    suite_name: str | None = None,
) -> dict[str, str]:
    """Upload report HTML, raw artifacts, and latest pointer to GCS."""
    base_uri = gcs_bundle_uri.rstrip("/")
    uploader = gcs_utils.GCSUtils()
    uploaded: dict[str, str] = {}

    if os.path.isfile(report_path):
        report_uri = f"{base_uri}/report.html"
        uploaded["report_url"] = uploader.upload_file(
            report_uri,
            report_path,
            content_type="text/html; charset=utf-8",
        )
        uploaded["report_gcs_uri"] = report_uri

    patterns = [
        "sim_results*.json",
        "ces_results*.json",
        "sim_cases*.yaml",
        "tool_test_results*.csv",
        "tool_test_results*.json",
        "callback_test_results*.csv",
        "callback_test_results*.json",
        "EVAL_STAMP*.txt",
    ]
    for pat in patterns:
        for local_file in sorted(glob.glob(os.path.join(output_dir, pat))):
            if os.path.isfile(local_file):
                fname = os.path.basename(local_file)
                dest_uri = f"{base_uri}/{fname}"
                ctype = (
                    "application/json"
                    if fname.endswith(".json")
                    else "text/plain; charset=utf-8"
                )
                uploader.upload_file(dest_uri, local_file, content_type=ctype)

    if suite_name and os.path.isfile(report_path):
        safe_suite = (
            re.sub(r"[^a-zA-Z0-9._-]+", "-", suite_name).strip("-") or "evals"
        )
        parent_uri = "/".join(base_uri.split("/")[:-1])
        if parent_uri.startswith("gs://") and len(parent_uri) > len("gs://"):
            latest_uri = f"{parent_uri}/latest-{safe_suite}.html"
        else:
            latest_uri = f"{base_uri}/latest-{safe_suite}.html"
        uploaded["latest_url"] = uploader.upload_file(
            latest_uri,
            report_path,
            content_type="text/html; charset=utf-8",
            cache_control="no-cache, max-age=0",
        )
        uploaded["latest_gcs_uri"] = latest_uri

    return uploaded


def write_github_step_summary(
    gate_summary: dict[str, Any],
    suite_name: str | None = None,
    published_urls: dict[str, str] | None = None,
    summary_path: str | None = None,
) -> str | None:
    """Write a Markdown summary table and links to GITHUB_STEP_SUMMARY."""
    target_path = summary_path or os.environ.get("GITHUB_STEP_SUMMARY")
    if not target_path:
        return None

    title = f"### {suite_name}" if suite_name else "### Evaluation Summary"
    passed = gate_summary.get("passed_cases", 0)
    total = gate_summary.get("total_cases", 0)
    min_passes = gate_summary.get("min_run_passes", 1)
    cases = gate_summary.get("cases", [])
    max_runs = max((c.get("runs", 1) for c in cases), default=1)
    rule_note = (
        f" ({min_passes}-of-{max_runs} pass rule)"
        if max_runs > 1 or min_passes > 1
        else ""
    )

    lines = [
        title,
        "",
        f"**{passed} / {total} cases passed**{rule_note}",
        "",
    ]

    if published_urls:
        link_parts = []
        if published_urls.get("report_url"):
            link_parts.append(
                f"[Open HTML Report]({published_urls['report_url']})"
            )
        if published_urls.get("latest_url"):
            link_parts.append(
                f"[Latest for branch]({published_urls['latest_url']})"
            )
        if link_parts:
            lines.append(" · ".join(link_parts))
            lines.append("")

    if cases:
        lines.extend(
            [
                "| Status | Case | Runs Passed |",
                "| :---: | :--- | :---: |",
            ]
        )
        for c in cases:
            status_str = "PASS" if c.get("passed") else "FAIL"
            lines.append(
                f"| {status_str} | `{c.get('name', '?')}` | "
                f"{c.get('passes', 0)}/{c.get('runs', 1)} |"
            )
        lines.append("")

    content = "\n".join(lines) + "\n"
    with _ORIG_OPEN(target_path, "a") as f:
        f.write(content)
    return target_path


def generate_combined_report_from_dir(
    output_dir: str,
    golden_run: str | None = None,
    app_name: str | None = None,
    output_path: str | None = None,
    run: bool = False,
    app_dir: str | None = None,
    tool_test_file: str | None = None,
    goldens_dir: str | None = None,
    simulation_dir: str | None = None,
    include: list[str] | None = None,
    modality: str = "text",
    sim_user_model: str | None = None,
    eval_model: str | None = None,
    runs: int = 1,
    filter_files: list[str] | None = None,
    filter_tags: list[str] | None = None,
    filter_names: list[str] | None = None,
    parallel: int = 1,
    golden_parallel: int = 1,
    golden_timeout: int = 600,
    bg_noise_file: str | None = None,
    bg_noise_snr: float | None = None,
    burst_noise_files: list[str] | None = None,
    use_tool_fakes: bool = False,
    timestamp: str | None = None,
    expectations_only: bool = False,
    deployment_id: str | None = None,
    skip_playback_wait: bool = False,
    single_bidi_stream: bool = False,
    progress_callback: Callable[[str, int, int], None] | None = None,
    capture_agent_audio: bool = False,
    report_format: str = "html",
    vertex_location: str = "global",
    naturalness: bool | dict[str, Any] | None = None,
    audio_analysis: bool | None = None,
    infra_retries: int = 2,
    retry_parallel: int | None = None,
    retry_cooldown: float = 60.0,
    golden_run_method: str = "STABLE",
    min_run_passes: int | None = None,
    min_pass_rate: float | None = None,
    gcs_bundle_uri: str | None = None,
    suite_name: str | None = None,
    github_step_summary: bool = False,
    return_gate_status: bool = False,
    platform_sims: bool = False,
) -> str | tuple[str, dict[str, Any]]:
    """Load results from directory and generate a combined report.

    Args:
      output_dir: Directory containing the evaluation results.
      golden_run: The golden evaluation run ID.
      app_name: CX Agent Studio (CXAS) agent resource name.
      output_path: Optional GCS or local path to write the report to.
      run: If True, triggers execution of evals before compiling report.
      app_dir: Directory containing CX Agent Studio (CXAS) agent code.
      tool_test_file: Path to tool tests definition file.
      goldens_dir: Directory containing golden test cases.
      simulation_dir: Directory containing simulation test cases.
      include: List of evaluation types to include ('sims', 'goldens', etc).
      modality: The modality used for the evaluation (e.g., 'text').
      runs: Number of simulation runs.
      filter_files: List of specific files to filter evaluations by.
      filter_tags: List of specific tags to filter evaluations by.
      parallel: Degree of parallelism for the runs.
      golden_timeout: Golden run execution timeout in seconds.
      bg_noise_file: Path to background noise audio file to play during
        replay.
      burst_noise_files: List of paths to burst noise audio files injected
        during replay.
      use_tool_fakes: Use fake tools for the session if available.
      deployment_id: Optional deployment ID to target for simulations.
      report_format: Output format for the combined report, 'html'
        (default) or 'json'.
      naturalness: Optional run-level override for the naturalness metric.
        True enables it with defaults, False disables it, and a dict is
        merged over each test case's own config. None (default) defers to
        the test case.
      platform_sims: If True, executes platform-compatible simulation test
        cases as CXAS 2.0 Milestone evaluations on the CES platform.

    Returns:
      The resolved output path or URL where the report was saved.
    """
    if not os.path.isdir(output_dir):
        raise ValueError(f"{output_dir} is not a directory.")

    if report_format not in ("html", "json"):
        raise ValueError(
            f"Unsupported report format: {report_format!r}. "
            "Expected 'html' or 'json'."
        )

    if include is None or "all" in include:
        include = ["sims", "goldens", "tools", "callbacks"]

    sim_results = []
    tool_results = []
    callback_results = []
    golden_results = []

    resolved_timestamp = timestamp

    if run:
        extra_run_kwargs: dict[str, Any] = {}
        if infra_retries != 2:
            extra_run_kwargs["infra_retries"] = infra_retries
        if retry_parallel is not None:
            extra_run_kwargs["retry_parallel"] = retry_parallel
        if retry_cooldown != 60.0:
            extra_run_kwargs["retry_cooldown"] = retry_cooldown
        if golden_run_method != "STABLE":
            extra_run_kwargs["golden_run_method"] = golden_run_method
        run_results = run_all_evals(
            app_name=app_name,
            app_dir=app_dir,
            tool_test_file=tool_test_file,
            goldens_dir=goldens_dir,
            simulation_dir=simulation_dir,
            output_dir=output_dir,
            modality=modality,
            sim_user_model=sim_user_model,
            eval_model=eval_model,
            runs=runs,
            filter_files=filter_files,
            filter_tags=filter_tags,
            filter_names=filter_names,
            parallel=parallel,
            golden_parallel=golden_parallel,
            golden_timeout=golden_timeout,
            include=include,
            bg_noise_file=bg_noise_file,
            burst_noise_files=burst_noise_files,
            use_tool_fakes=use_tool_fakes,
            timestamp=timestamp,
            expectations_only=expectations_only,
            deployment_id=deployment_id,
            skip_playback_wait=skip_playback_wait,
            single_bidi_stream=single_bidi_stream,
            progress_callback=progress_callback,
            capture_agent_audio=capture_agent_audio,
            vertex_location=vertex_location,
            naturalness=naturalness,
            audio_analysis=bool(audio_analysis),
            **({"platform_sims": True} if platform_sims else {}),
            **extra_run_kwargs,
        )
        sim_results = run_results["simulation"] if "sims" in include else []
        # Map tool results to expected format if needed
        if "tools" in include:
            for r in run_results["tool"]:
                tool_results.append(
                    {
                        "name": r.get("test_name", r.get("test", "?")),
                        "tool": r.get("tool", "?"),
                        "passed": r.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": r.get("status", "?"),
                        "latency_ms": r.get("latency (ms)", 0),
                        "errors": r.get("errors", ""),
                    }
                )
        # Map callback results
        if "callbacks" in include:
            for r in run_results["callback"]:
                callback_results.append(
                    {
                        "name": r.get("test_name", "?"),
                        "agent": r.get("agent_name", "?"),
                        "callback_type": r.get("callback_type", "?"),
                        "passed": r.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": r.get("status", "?"),
                        "error": r.get("error_message", ""),
                    }
                )
        golden_results = run_results["golden"] if "goldens" in include else []
    else:
        sim_files = []
        if "sims" in include:
            sim_base, sim_ext = os.path.splitext(
                eval_utils.SIM_RESULTS_FILENAME
            )
            sim_files = glob.glob(
                os.path.join(output_dir, f"{sim_base}*{sim_ext}")
            )

        tool_files = []
        callback_files = []
        if "tools" in include:
            tool_base, tool_ext = os.path.splitext(
                eval_utils.TOOL_RESULTS_FILENAME
            )
            tool_files = glob.glob(
                os.path.join(output_dir, f"{tool_base}*{tool_ext}")
            )
            tool_files.extend(
                glob.glob(os.path.join(output_dir, f"{tool_base}*.json"))
            )
        if "callbacks" in include:
            cb_base, cb_ext = os.path.splitext(
                eval_utils.CALLBACK_RESULTS_FILENAME
            )
            callback_files = glob.glob(
                os.path.join(output_dir, f"{cb_base}*{cb_ext}")
            )
            callback_files.extend(
                glob.glob(os.path.join(output_dir, f"{cb_base}*.json"))
            )

        if sim_files:
            sim_files.sort(key=os.path.getmtime)
            latest_sim_file = sim_files[-1]

            sim_base, sim_ext = os.path.splitext(
                eval_utils.SIM_RESULTS_FILENAME
            )
            sim_base_esc = re.escape(sim_base)
            sim_ext_esc = re.escape(sim_ext)
            pattern = (
                rf"{sim_base_esc}_({eval_utils.TIMESTAMP_PATTERN}){sim_ext_esc}"
            )
            match = re.search(pattern, latest_sim_file)
            if match:
                resolved_timestamp = match.group(1)

            with open(latest_sim_file) as f:
                data = json.load(f)
                # New envelope format: {"wall_clock_s": N, "results": [...]}
                # Old format: [...]
                if isinstance(data, dict):
                    sim_results = data.get("results", [])
                else:
                    sim_results = data
            print(
                f"Loaded {len(sim_results)} sim results from {latest_sim_file}"
            )

        if tool_files:
            tool_files.sort(key=os.path.getmtime)
            tf = tool_files[-1]
            df = pd.read_csv(tf) if tf.endswith(".csv") else pd.read_json(tf)
            for _, row in df.iterrows():
                tool_results.append(
                    {
                        "name": row.get("test_name", row.get("test", "?")),
                        "tool": row.get("tool", "?"),
                        "passed": row.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": row.get("status", "?"),
                        "latency_ms": row.get("latency (ms)", 0),
                        "errors": row.get("errors", ""),
                    }
                )
            print(f"Loaded {len(tool_results)} tool results from {tf}")

        if callback_files:
            callback_files.sort(key=os.path.getmtime)
            cf = callback_files[-1]
            df = pd.read_csv(cf) if cf.endswith(".csv") else pd.read_json(cf)
            for _, row in df.iterrows():
                callback_results.append(
                    {
                        "name": row.get("test_name", "?"),
                        "agent": row.get("agent_name", "?"),
                        "callback_type": row.get("callback_type", "?"),
                        "passed": row.get("status", "").upper()
                        in ("PASSED", "PASS"),
                        "status": row.get("status", "?"),
                        "error": row.get("error_message", ""),
                    }
                )
            print(f"Loaded {len(callback_results)} callback results from {cf}")

        if golden_run:
            if not app_name:
                raise ValueError(
                    "--app-name is required when golden_run is specified."
                )
            extra_golden_kwargs: dict[str, Any] = {}
            if min_run_passes is not None:
                extra_golden_kwargs["min_run_passes"] = min_run_passes
            golden_results = load_golden_results(
                golden_run, app_name, include=include, **extra_golden_kwargs
            )
        elif "goldens" in include:
            ces_files = glob.glob(os.path.join(output_dir, "ces_results*.json"))
            if ces_files:
                ces_files.sort(key=os.path.getmtime)
                latest_ces_file = ces_files[-1]
                with open(latest_ces_file) as f:
                    ces_data = json.load(f)
                    if isinstance(ces_data, list):
                        golden_results = ces_data
                    elif isinstance(ces_data, dict):
                        golden_results = ces_data.get("results", [])

    if min_run_passes is not None and golden_results and not golden_run:
        golden_results = reconcile_golden_runs(
            golden_results, min_run_passes=min_run_passes
        )

    if not output_path:
        default_filename = (
            eval_utils.COMBINED_REPORT_JSON_FILENAME
            if report_format == "json"
            else eval_utils.COMBINED_REPORT_FILENAME
        )
        report_name = eval_utils.add_timestamp_suffix(
            default_filename, resolved_timestamp
        )
        output_path = os.path.join(output_dir, report_name)

    if report_format == "json":
        actual_path = generate_combined_json_report(
            golden_results=golden_results,
            sim_results=sim_results,
            tool_results=tool_results,
            callback_results=callback_results,
            output_path=output_path,
            app_name=app_name or "",
            golden_modality=modality,
            sim_modality=modality,
            audio_analysis=audio_analysis,
        )
    else:
        actual_path = generate_combined_html_report(
            golden_results=golden_results,
            sim_results=sim_results,
            tool_results=tool_results,
            callback_results=callback_results,
            output_path=output_path,
            app_name=app_name or "",
            golden_modality=modality,
            sim_modality=modality,
            bg_noise_file=bg_noise_file,
            burst_noise_files=burst_noise_files,
            audio_analysis=audio_analysis,
        )

    should_gate_or_publish = any(
        [
            min_run_passes is not None,
            min_pass_rate is not None,
            gcs_bundle_uri is not None,
            suite_name is not None,
            github_step_summary,
            return_gate_status,
        ]
    )
    if should_gate_or_publish:
        gate_summary = summarize_and_gate_evals(
            sim_results=sim_results,
            golden_results=golden_results,
            tool_results=tool_results,
            callback_results=callback_results,
            min_run_passes=min_run_passes or 1,
            min_pass_rate=min_pass_rate,
        )
        if suite_name:
            write_eval_stamp(
                output_dir=output_dir,
                suite_name=suite_name,
                gate_summary=gate_summary,
                modality=modality,
                runs=runs,
            )
        published_urls: dict[str, str] = {}
        if gcs_bundle_uri:
            published_urls = publish_eval_report_bundle(
                output_dir=output_dir,
                report_path=actual_path,
                gcs_bundle_uri=gcs_bundle_uri,
                suite_name=suite_name,
            )
            gate_summary["published_urls"] = published_urls
        if github_step_summary:
            write_github_step_summary(
                gate_summary=gate_summary,
                suite_name=suite_name,
                published_urls=published_urls,
            )
        if return_gate_status:
            return actual_path, gate_summary

    return actual_path


def run_all_evals(
    app_name: str,
    app_dir: str | None = None,
    tool_test_file: str | None = None,
    goldens_dir: str | None = None,
    simulation_dir: str | None = None,
    output_dir: str | None = None,
    modality: str = "text",
    sim_user_model: str | None = None,
    eval_model: str | None = None,
    runs: int = 1,
    filter_files: list[str] | None = None,
    filter_tags: list[str] | None = None,
    filter_names: list[str] | None = None,
    parallel: int = 1,
    golden_parallel: int = 1,
    golden_timeout: int = 600,
    include: list[str] | None = None,
    bg_noise_file: str | None = None,
    bg_noise_snr: float | None = None,
    burst_noise_files: list[str] | None = None,
    use_tool_fakes: bool = False,
    timestamp: str | None = None,
    expectations_only: bool = False,
    deployment_id: str | None = None,
    skip_playback_wait: bool = False,
    single_bidi_stream: bool = False,
    progress_callback: Callable[[str, int, int], None] | None = None,
    capture_agent_audio: bool = False,
    vertex_location: str = "global",
    naturalness: bool | dict[str, Any] | None = None,
    audio_analysis: bool = False,
    infra_retries: int = 2,
    retry_parallel: int | None = None,
    retry_cooldown: float = 60.0,
    golden_run_method: str = "STABLE",
    platform_sims: bool = False,
) -> dict[str, Any]:
    """Runs all 4 types of evaluations and returns aggregated results.

    Deprecated legacy wrapper. Use
    `cxas_scrapi.evals.runner.run_all_evals` directly.

    Args:
      app_name: CX Agent Studio (CXAS) agent resource name.
      app_dir: Directory containing CX Agent Studio (CXAS) agent code.
      tool_test_file: Path to tool tests definition file.
      goldens_dir: Directory containing golden test cases.
      simulation_dir: Directory containing simulation test cases.
      output_dir: Directory to write output evaluation results.
      modality: The modality used for the evaluation (e.g., 'text').
      runs: Number of simulation runs.
      filter_files: List of specific files to filter evaluations by.
      filter_tags: List of specific tags to filter evaluations by.
      parallel: Degree of parallelism for the runs.
      golden_timeout: Golden run execution timeout in seconds.
      include: List of evaluation types to include.
      bg_noise_file: Path to background noise audio file to play during
        replay.
      burst_noise_files: List of paths to burst noise audio files injected
        during replay.
      use_tool_fakes: Use fake tools for the session if available.
      timestamp: Optional timestamp to append to result filenames.
      expectations_only: Run simulations checking expectations only.
      vertex_location: Vertex AI location for evaluation LLM models.
        Defaults to 'global'.
      naturalness: Optional run-level override for the naturalness metric.
        True enables it with defaults, False disables it, and a dict is
        merged over each test case's own config. None (default) defers to
        the test case.
      platform_sims: If True, executes platform-compatible simulation test
        cases as CXAS 2.0 Milestone evaluations on the CES platform.

    Returns:
      A dict containing lists of results for 'simulation', 'golden', 'tool', and
      'callback'.
    """
    extra_kwargs: dict[str, Any] = {}
    if infra_retries != 2:
        extra_kwargs["infra_retries"] = infra_retries
    if retry_parallel is not None:
        extra_kwargs["retry_parallel"] = retry_parallel
    if retry_cooldown != 60.0:
        extra_kwargs["retry_cooldown"] = retry_cooldown
    if audio_analysis:
        extra_kwargs["audio_analysis"] = audio_analysis
    if golden_run_method != "STABLE":
        extra_kwargs["golden_run_method"] = golden_run_method
    if platform_sims:
        extra_kwargs["platform_sims"] = platform_sims
    return evals_runner.run_all_evals(
        app_name=app_name,
        modality=modality,
        sim_user_model=sim_user_model,
        eval_model=eval_model,
        runs=runs,
        goldens_dir=goldens_dir,
        tool_test_file=tool_test_file,
        simulation_dir=simulation_dir,
        app_dir=app_dir,
        output_dir=output_dir,
        filter_files=filter_files,
        filter_tags=filter_tags,
        filter_names=filter_names,
        parallel=parallel,
        golden_parallel=golden_parallel,
        golden_timeout=golden_timeout,
        include=include,
        bg_noise_file=bg_noise_file,
        burst_noise_files=burst_noise_files,
        use_tool_fakes=use_tool_fakes,
        timestamp=timestamp,
        expectations_only=expectations_only,
        deployment_id=deployment_id,
        skip_playback_wait=skip_playback_wait,
        single_bidi_stream=single_bidi_stream,
        progress_callback=progress_callback,
        capture_agent_audio=capture_agent_audio,
        vertex_location=vertex_location,
        naturalness=naturalness,
        **extra_kwargs,
    )
