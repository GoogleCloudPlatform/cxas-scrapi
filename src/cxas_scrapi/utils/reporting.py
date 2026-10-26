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
import re
import typing
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import jinja2
import pandas as pd
import yaml

from cxas_scrapi.core import conversation_history, tools
from cxas_scrapi.evals import runner as evals_runner
from cxas_scrapi.redaction.policy import merge_redaction_expectations
from cxas_scrapi.utils import (
    base_components,
    eval_utils,
    gcs_utils,
    report_components,
)
from cxas_scrapi.utils.latency_parser import LatencyParser

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
        "_fmt_duration": lambda s: f"{s:.1f}s",
        "_fmt_ms": _fmt_ms,
        "_pl_status_cls": _pl_status_cls,
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

    html = _get_html_head(ts)
    html += _get_summary_block(
        passed,
        total,
        errors,
        modality,
        model,
        ts,
        wall_clock_s,
        suite_pl=suite_pl,
    )

    html += _get_results_table(eval_stats)
    html += '\n<h2 id="section-sims">Eval Details</h2>\n'

    for name, s in sorted(
        eval_stats.items(), key=lambda x: x[1]["pass"] / max(x[1]["total"], 1)
    ):
        score = f"{s['pass']}/{s['total']}"
        is_all_pass = s["pass"] == s["total"]
        cls = "pass-bg" if is_all_pass else "fail-bg"
        passed_str = "true" if is_all_pass else "false"
        html += (
            f'<div class="eval-card" id="eval-{name}" '
            f'data-passed="{passed_str}">\n'
        )
        html += (
            f'<div class="eval-header {cls}">'
            f'<span>{_escape(name)} <span class="badge sim">sim</span></span>'
            f"<span>{score}</span></div>\n"
        )
        html += '<div class="eval-body">\n'

        for r in s["runs"]:
            html += _get_run_detail(r, ces_base, tools_map)
        html += "</div></div>\n"

    html += "</body></html>"

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
    burst_noise_files: list[str] | None = None,
    traces_dir: str | None = None,
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
    template = _get_combined_report_template()
    html = template.render(
        failure_patterns_html=failure_patterns_html,
        suite_pl=suite_pl,
        trend_chart=_prepare_trend_chart(suite_pl.get("turn_trend_buckets", []))
        if suite_pl
        else None,
        _fmt_ms=_fmt_ms,
        _pl_status_cls=_pl_status_cls,
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
        burst_noise_files=burst_noise_files,
        tool_results_html=tool_results_html,
        callback_results_html=callback_results_html,
    )

    # Wrap compiled body dynamically in declarative BaseShell scaffold envelope.
    report = report_components.BaseShell(
        title=f"Combined Eval Report - {ts}",
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


def load_golden_results(
    run_id: str, app_name: str, include: list[str] | None = None
) -> list[dict[str, Any]]:
    """Fetch golden results and parse into report-friendly format.

    Args:
      run_id: The evaluation run ID to load results for.
      app_name: CX Agent Studio (CXAS) agent resource name.
      include: Categories of evaluations to include (e.g. 'goldens',
        'scenarios').

    Returns:
      A list of formatted evaluation result dictionaries.
    """
    if include is None:
        include = ["goldens", "scenarios"]

    utils = eval_utils.EvalUtils(app_name=app_name)
    full_run_id = (
        run_id
        if run_id.startswith("projects/")
        else f"{app_name}/evaluationRuns/{run_id}"
    )

    raw_results = utils.list_evaluation_results_by_run(full_run_id)

    evals_map = utils.get_evaluations_map(app_name, reverse=False)
    name_lookup = {}
    for cat in ["goldens", "scenarios"]:
        for resource, display in evals_map.get(cat, {}).items():
            name_lookup[resource] = display

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
                "comparisons": [],
            }
            for o in turn.get("expectation_outcome", []):
                exp = o.get("expectation", {})
                outcome = _outcome_str(o.get("outcome"))
                comp = {"outcome": outcome}

                if "agent_response" in exp:
                    chunks = exp["agent_response"].get("chunks", [])
                    comp["type"] = "text"
                    comp["expected"] = (
                        chunks[0].get("text", "") if chunks else ""
                    )
                    obs = o.get("observed_agent_response", {})
                    comp["actual"] = (
                        obs.get("chunks", [{}])[0].get("text", "")
                        if obs
                        else "(missed)"
                    )
                elif "tool_call" in exp:
                    tc = exp["tool_call"]
                    comp["type"] = "tool_call"
                    comp["expected"] = (
                        tc.get("display_name")
                        or tc.get("tool", "").split("/")[-1]
                    )
                    comp["expected_args"] = tc.get("args", {})
                    obs = o.get("observed_tool_call", {})
                    comp["actual"] = (
                        (
                            obs.get("display_name")
                            or obs.get("tool", "").split("/")[-1]
                        )
                        if obs
                        else "(missed)"
                    )
                    comp["actual_args"] = obs.get("args", {}) if obs else {}
                    tir = o.get("toolInvocationResult", {})
                    comp["tool_invocation_score"] = tir.get(
                        "parameterCorrectnessScore"
                    )
                    comp["tool_invocation_explanation"] = tir.get("explanation")
                elif "tool_response" in exp:
                    continue
                elif "agent_transfer" in exp:
                    at = exp["agent_transfer"]
                    comp["type"] = "transfer"
                    comp["expected"] = at.get(
                        "display_name",
                        at.get("target_agent", "").split("/")[-1],
                    )
                    obs = o.get("observed_agent_transfer", {})
                    if obs:
                        comp["actual"] = obs.get(
                            "display_name",
                            obs.get("target_agent", "").split("/")[-1],
                        )
                    else:
                        comp["actual"] = "(missed)"
                else:
                    continue

                turn_data["comparisons"].append(comp)
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
            ev_obj = utils.get_evaluation(eval_resource)
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
                "conversations": [
                    t.get("conversation")
                    for t in golden.get("turn_replay_results", [])
                    if t.get("conversation")
                ],
                "session_parameters": session_params,
                "duration_s": (
                    round(total_latency_s, 1) if total_latency_s > 0 else None
                ),
            }
        )

    return results


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

    common_params = data.get("common_session_parameters", {}) or {}
    common_expectations = data.get("common_expectations", []) or []
    common_naturalness = data.get("common_naturalness_metric")
    common_redaction = data.get("common_redaction_expectations")
    cases = data.get("evals", [])
    if not isinstance(cases, list):
        return []

    merged_cases = []
    for c in cases:
        if isinstance(c, dict):
            case_copy = c.copy()
            # Merge session parameters
            case_params = case_copy.get("session_parameters", {}) or {}
            merged = common_params.copy()
            merged.update(case_params)
            case_copy["session_parameters"] = merged

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

            # Merge the optional redaction_expectations block and remember
            # where the file lives so relative policy paths resolve.
            redaction = merge_redaction_expectations(
                common_redaction, case_copy.get("redaction_expectations")
            )
            if redaction is not None:
                case_copy["redaction_expectations"] = redaction
                case_copy["_redaction_base_dir"] = os.path.dirname(
                    os.path.abspath(yaml_path)
                )
            else:
                case_copy.pop("redaction_expectations", None)

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
) -> str:
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
            golden_results = load_golden_results(
                golden_run, app_name, include=include
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
        return generate_combined_json_report(
            golden_results=golden_results,
            sim_results=sim_results,
            tool_results=tool_results,
            callback_results=callback_results,
            output_path=output_path,
            app_name=app_name or "",
            golden_modality=modality,
            sim_modality=modality,
        )

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
    )
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
    infra_retries: int = 0,
    retry_parallel: int | None = None,
    retry_cooldown: float = 60.0,
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

    Returns:
      A dict containing lists of results for 'simulation', 'golden', 'tool', and
      'callback'.
    """
    extra_kwargs: dict[str, Any] = {}
    if infra_retries:
        extra_kwargs["infra_retries"] = infra_retries
    if retry_parallel is not None:
        extra_kwargs["retry_parallel"] = retry_parallel
    if retry_cooldown != 60.0:
        extra_kwargs["retry_cooldown"] = retry_cooldown
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
