# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

"""Pre-migration HTML preview + multi-stage HTML report aggregator.

Inspects a freshly loaded :class:`DFCXAgentIR` and emits a self-contained
HTML report so users can preview the structure of a source DFCX agent
*before* kicking off a 30+ minute migration. No LLM, no API calls.

Topology rendering prefers graphviz (via
:class:`HighLevelGraphVisualizer`) and falls back to Mermaid only if
graphviz isn't available — graphviz is far more legible on graphs with
30+ nodes.

The per-resource detail panel uses nested ``<details>`` collapsibles:
each playbook expands to reveal its referenced tools and webhooks, and
each tool/webhook expands further to show its python code (for
``PYTHON`` tools) or OpenAPI schema / webhook URI (for HTTP-backed
ones). This makes large agents skimmable without a wall of text.

:class:`StageReport` accumulates labeled HTML sections across the
migrate/stage1/stage2/stage3 pipeline and emits a single multi-section
report at the end. Each section becomes a top-level ``<details open>``
block, so the result is one collapsible-friendly long-form doc.
"""

from __future__ import annotations

import html
import io
import json
import re
from collections import Counter
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree

from cxas_scrapi.migration.data_models import DFCXAgentIR
from cxas_scrapi.migration.dfcx_dep_analyzer import DependencyAnalyzer
from cxas_scrapi.migration.flow_visualizer import (
    FlowDependencyResolver,
    FlowTreeVisualizer,
)
from cxas_scrapi.migration.graph_visualizer import HighLevelGraphVisualizer
from cxas_scrapi.migration.playbook_visualizer import PlaybookTreeVisualizer

__all__ = [
    "StageReport",
    "build_mermaid_topology",
    "build_mermaid_tools_per_agent",
    "collect_resource_rows",
    "collect_stats",
    "generate_html_report",
    "render_flow_trees_html",
    "render_playbook_trees_html",
    "rich_to_html",
    "topology_svg",
    "write_mermaid_files",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9]")


def _short(name: str) -> str:
    return name.rsplit("/", 1)[-1] if "/" in name else name


def _mermaid_id(prefix: str, raw: str) -> str:
    """Mermaid node IDs must be alphanumeric + underscore. We use the
    suffix of the resource name plus a short hash to guarantee uniqueness
    — the ``projects/.../agents/<uuid>/`` prefix is identical across all
    resources and would otherwise collide.
    """
    suffix = _short(raw)
    safe = _SAFE_ID_RE.sub("_", suffix)[:40]
    digest = abs(hash(raw)) % 0xFFFFFF
    return f"{prefix}_{safe}_{digest:06x}"


def _mermaid_label(text: str, max_len: int = 60) -> str:
    if not text:
        return "(unnamed)"
    cleaned = text.replace('"', "'").replace("\n", " ")
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + "…"
    return cleaned


def rich_to_html(renderable, width: int = 140) -> str:
    """Capture a Rich renderable as HTML using Rich's exporter. Output is
    routed to an in-memory buffer so calling this is silent."""
    buf_console = Console(
        record=True,
        width=width,
        force_terminal=True,
        file=io.StringIO(),
    )
    buf_console.print(renderable)
    full = buf_console.export_html(
        inline_styles=True, code_format="<pre>{code}</pre>"
    )
    start = full.find("<body")
    if start == -1:
        return full
    body_open_end = full.find(">", start)
    body_close = full.rfind("</body>")
    return full[body_open_end + 1 : body_close] if body_close > 0 else full


# ---------------------------------------------------------------------------
# Mermaid renderers (fallback)
# ---------------------------------------------------------------------------


def build_mermaid_topology(
    agent_data: DFCXAgentIR, analyzer: DependencyAnalyzer
) -> str:
    """Render the source agent's resource graph as a Mermaid flowchart.

    Nodes: each playbook (rectangle) and each flow (rounded). Edges:
    dependency references. Tools are not exploded into the graph — they
    would multiply node counts past Mermaid's readable limit. Use
    :func:`build_mermaid_tools_per_agent` for tool inventory.
    """
    lines = ["flowchart LR"]
    type_styles = {"Playbook": ":::playbook", "Flow": ":::flow"}

    for full_name, display_name in analyzer.name_map.items():
        node_type = analyzer.type_map.get(full_name, "Resource")
        node_id = _mermaid_id("n", full_name)
        cls = type_styles.get(node_type, "")
        label = _mermaid_label(display_name)
        if node_type == "Flow":
            lines.append(f'  {node_id}(["{label}"]){cls}')
        else:
            lines.append(f'  {node_id}["{label}"]{cls}')

    for source, targets in analyzer.graph.items():
        if source not in analyzer.name_map:
            continue
        src_id = _mermaid_id("n", source)
        for tgt in targets:
            if tgt not in analyzer.name_map:
                continue
            tgt_id = _mermaid_id("n", tgt)
            lines.append(f"  {src_id} --> {tgt_id}")

    lines.extend(
        [
            "  classDef playbook fill:#dbeafe,stroke:#2563eb,color:#1e3a8a",
            "  classDef flow fill:#fef3c7,stroke:#d97706,color:#7c2d12",
        ]
    )
    return "\n".join(lines)


_TOOL_REF_RE = re.compile(r"\$\{TOOL:([^}]+)\}|\{@TOOL:\s*([^}]+)\}")


def _scan_text_for_tool_refs(text: str) -> set[str]:
    refs: set[str] = set()
    if not text:
        return refs
    for m in _TOOL_REF_RE.finditer(text):
        refs.add((m.group(1) or m.group(2) or "").strip())
    return refs


def _collect_agent_tool_refs(
    agent_data: DFCXAgentIR,
) -> list[tuple[str, str, str, list[str]]]:
    """Return ``(kind, full_name, display_name, tool_refs)`` per playbook
    + flow. Looks at explicit ``referencedTools`` AND scans
    instruction/page bodies for ``${TOOL:Name}`` / ``{@TOOL: name}`` so
    an empty ``referencedTools`` doesn't hide a real dependency.
    """
    out: list[tuple[str, str, str, list[str]]] = []

    for pb in agent_data.playbooks:
        full = pb.get("name", "")
        display = pb.get("displayName", "?")
        explicit = list(pb.get("referencedTools", []) or [])
        scanned = _scan_text_for_tool_refs(
            json.dumps(pb.get("instruction", {}))
        )
        merged = list(dict.fromkeys(explicit + sorted(scanned)))
        out.append(("Playbook", full, display, merged))

    for flow_wrapper in agent_data.flows:
        flow = flow_wrapper.flow_data
        full = flow.get("name", "")
        display = flow.get("displayName", "?")
        scanned = _scan_text_for_tool_refs(json.dumps(flow))
        for page in flow_wrapper.pages:
            scanned |= _scan_text_for_tool_refs(json.dumps(page.page_data))
        out.append(("Flow", full, display, sorted(scanned)))

    return out


def build_mermaid_tools_per_agent(
    agent_data: DFCXAgentIR, max_agents: int = 25
) -> str:
    """Render an agent → tools subgraph for the playbooks/flows that
    reference the most tools, capped at ``max_agents``."""
    lines = ["flowchart LR"]

    refs = _collect_agent_tool_refs(agent_data)
    refs.sort(key=lambda x: len(x[3]), reverse=True)

    seen_tools: set[str] = set()
    rendered = 0
    for kind, full_name, display, tools in refs:
        if not tools:
            continue
        if rendered >= max_agents:
            break
        agent_id = _mermaid_id("a", full_name)
        cls = "flow" if kind == "Flow" else "pb"
        if kind == "Flow":
            lines.append(f'  {agent_id}(["{_mermaid_label(display)}"]):::{cls}')
        else:
            lines.append(f'  {agent_id}["{_mermaid_label(display)}"]:::{cls}')
        for tool in tools:
            tool_id = _mermaid_id("t", tool)
            if tool not in seen_tools:
                tool_label = _short(tool)
                lines.append(
                    f'  {tool_id}(["{_mermaid_label(tool_label)}"]):::tool'
                )
                seen_tools.add(tool)
            lines.append(f"  {agent_id} -.-> {tool_id}")
        rendered += 1

    if len(lines) == 1:
        lines.append('  empty["No tool references found in instructions"]')

    lines.extend(
        [
            "  classDef pb fill:#dbeafe,stroke:#2563eb,color:#1e3a8a",
            "  classDef flow fill:#fef3c7,stroke:#d97706,color:#7c2d12",
            "  classDef tool fill:#dcfce7,stroke:#16a34a,color:#14532d",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Graphviz (primary) topology
# ---------------------------------------------------------------------------


def topology_svg(
    agent_data: DFCXAgentIR, show_code_blocks: bool = False
) -> str | None:
    """Render the source agent topology as an SVG string using
    :class:`HighLevelGraphVisualizer` (graphviz). Returns ``None`` if
    graphviz isn't available — callers should fall back to Mermaid."""
    try:
        dot = HighLevelGraphVisualizer(agent_data).build(
            show_code_blocks=show_code_blocks
        )
        svg_bytes = dot.pipe(format="svg")
        svg_text = svg_bytes.decode("utf-8")
        idx = svg_text.find("<svg")
        return svg_text[idx:] if idx >= 0 else svg_text
    except Exception:
        return None


def _topology_html_block(
    agent_data: DFCXAgentIR,
    analyzer: DependencyAnalyzer,
    svg_class: str = "svg-zoom",
    mermaid_class: str = "mermaid",
) -> str:
    """Graphviz SVG if available; otherwise Mermaid. Returns the inner
    HTML for embedding inside a panel/details block."""
    svg = topology_svg(agent_data)
    if svg:
        return f'<div class="{svg_class}">{svg}</div>'
    mermaid = build_mermaid_topology(agent_data, analyzer)
    return (
        "<p><em>graphviz not available; falling back to Mermaid.</em></p>"
        f'<div class="{mermaid_class}">{mermaid}</div>'
    )


# ---------------------------------------------------------------------------
# Stats & resource rows
# ---------------------------------------------------------------------------


def collect_stats(
    agent_data: DFCXAgentIR, analyzer: DependencyAnalyzer
) -> dict[str, Any]:
    """Stats dict the HTML template renders."""
    pb_count = len(agent_data.playbooks)
    flow_count = len(agent_data.flows)
    page_count = sum(len(f.pages) for f in agent_data.flows)
    tool_count = len(agent_data.tools)
    webhook_count = len(agent_data.webhooks)
    intent_count = len(agent_data.intents)
    entity_count = len(agent_data.entity_types)
    code_block_count = len(agent_data.code_blocks)
    routing_edge_count = sum(
        len(targets) for targets in analyzer.graph.values()
    )

    in_out: Counter[str] = Counter()
    for source, targets in analyzer.graph.items():
        in_out[source] += len(targets)
    for target, sources in analyzer.reverse_graph.items():
        in_out[target] += len(sources)
    top_connected = [
        {
            "name": analyzer.name_map.get(rid, rid),
            "type": analyzer.type_map.get(rid, "?"),
            "degree": deg,
        }
        for rid, deg in in_out.most_common(10)
    ]

    # Rough heuristic: ~40s per flow + ~10s per playbook through the LLM
    # pipeline, plus ~60s of fixed setup.
    est_seconds = flow_count * 40 + pb_count * 10 + 60
    est_minutes = max(1, est_seconds // 60)

    return {
        "playbook_count": pb_count,
        "flow_count": flow_count,
        "page_count": page_count,
        "tool_count": tool_count,
        "webhook_count": webhook_count,
        "intent_count": intent_count,
        "entity_count": entity_count,
        "code_block_count": code_block_count,
        "routing_edge_count": routing_edge_count,
        "top_connected": top_connected,
        "estimated_minutes": est_minutes,
        "agent_name": agent_data.display_name,
        "default_language": agent_data.default_language_code,
        "start_flow": agent_data.start_flow,
        "start_playbook": agent_data.start_playbook,
    }


def collect_resource_rows(
    agent_data: DFCXAgentIR, analyzer: DependencyAnalyzer
) -> list[dict[str, Any]]:
    """Per-resource row data for the HTML detail panel."""
    rows: list[dict[str, Any]] = []
    for pb in agent_data.playbooks:
        full = pb.get("name", "")
        rows.append(
            {
                "type": "Playbook",
                "name": pb.get("displayName", "?"),
                "id": _short(full),
                "full_name": full,
                "tools": list(pb.get("referencedTools", []) or []),
                "playbooks": list(pb.get("referencedPlaybooks", []) or []),
                "outgoing": sorted(
                    analyzer.name_map.get(t, t)
                    for t in analyzer.graph.get(full, set())
                ),
                "incoming": sorted(
                    analyzer.name_map.get(s, s)
                    for s in analyzer.reverse_graph.get(full, set())
                ),
                "step_count": len(
                    pb.get("instruction", {}).get("steps", []) or []
                ),
                "_pb_raw": pb,
            }
        )
    for flow_wrapper in agent_data.flows:
        flow = flow_wrapper.flow_data
        full = flow.get("name", "")
        scanned = _scan_text_for_tool_refs(json.dumps(flow))
        for page in flow_wrapper.pages:
            scanned |= _scan_text_for_tool_refs(json.dumps(page.page_data))
        rows.append(
            {
                "type": "Flow",
                "name": flow.get("displayName", "?"),
                "id": _short(full),
                "full_name": full,
                "tools": sorted(scanned),
                "playbooks": [],
                "outgoing": sorted(
                    analyzer.name_map.get(t, t)
                    for t in analyzer.graph.get(full, set())
                ),
                "incoming": sorted(
                    analyzer.name_map.get(s, s)
                    for s in analyzer.reverse_graph.get(full, set())
                ),
                "step_count": len(flow_wrapper.pages),
                "_flow_wrapper": flow_wrapper,
            }
        )
    rows.sort(
        key=lambda r: (-len(r["outgoing"]) - len(r["incoming"]), r["name"])
    )
    return rows


# ---------------------------------------------------------------------------
# Per-resource detail rendering — nested tool/webhook dropdowns
# ---------------------------------------------------------------------------


def _index_tools_by_resource(
    agent_data: DFCXAgentIR,
) -> dict[str, dict[str, Any]]:
    """Map full tool resource name AND short id AND displayName → DFCX
    tool dict, so playbook ``referencedTools`` entries (which are full
    resource names) and scanned ``{@TOOL: short_name}`` refs both resolve.
    """
    idx: dict[str, dict[str, Any]] = {}
    for t in agent_data.tools:
        name = t.get("name", "")
        if name:
            idx[name] = t
            idx[_short(name)] = t
        if t.get("displayName"):
            idx[t["displayName"]] = t
    return idx


def _index_webhooks_by_resource(
    agent_data: DFCXAgentIR,
) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for w in agent_data.webhooks:
        name = w.get("name", "")
        if name:
            idx[name] = w
            idx[_short(name)] = w
        if w.get("displayName"):
            idx[w["displayName"]] = w
    return idx


# DFCX serializes webhook references two ways inside fulfillments:
#   "webhook": "/projects/.../webhooks/<uuid>"   (full or partial resource path)
#   "webhook": "<displayName>"                   (short form)
# Match both, plus any bare /webhooks/<uuid> path that leaks elsewhere.
_WEBHOOK_PATH_RE = re.compile(r"/webhooks/[A-Za-z0-9-]+")
_WEBHOOK_FIELD_RE = re.compile(r'"webhook"\s*:\s*"([^"]+)"')


def _scan_for_webhook_refs(text: str) -> set[str]:
    if not text:
        return set()
    refs = set(_WEBHOOK_PATH_RE.findall(text))
    for m in _WEBHOOK_FIELD_RE.finditer(text):
        val = m.group(1).strip()
        if val:
            refs.add(val)
    return refs


def _render_tool_inner(tool_dict: dict[str, Any] | None, ref: str) -> str:
    """Render the inner HTML of a tool ``<details>`` (the part shown
    when the user expands it)."""
    if not tool_dict:
        return (
            "<div class='hint'>Tool definition not found in source agent. "
            f"Ref: <code>{html.escape(ref)}</code></div>"
        )

    parts: list[str] = []
    desc = tool_dict.get("description") or ""
    if desc:
        parts.append(f"<div class='meta'><em>{html.escape(desc)}</em></div>")
    tool_type = tool_dict.get("toolType") or tool_dict.get("type") or "?"
    parts.append(
        f"<div class='meta'><strong>Type:</strong> "
        f"<code>{html.escape(str(tool_type))}</code></div>"
    )

    # PYTHON tool — show code
    py = tool_dict.get("pythonFunction") or tool_dict.get("python_function")
    if py and (py.get("python_code") or py.get("pythonCode")):
        code = py.get("python_code") or py.get("pythonCode", "")
        parts.append(
            "<div class='code-label'>Python code:</div>"
            f"<pre class='code'>{html.escape(code)}</pre>"
        )

    # OpenAPI / toolset schema — show YAML
    open_api = tool_dict.get("openApiSpec") or {}
    schema = (
        open_api.get("textSchema")
        or open_api.get("text_schema")
        or open_api.get("open_api_schema")
        or ""
    )
    if not schema:
        toolset = (
            tool_dict.get("openApiToolset")
            or tool_dict.get("open_api_toolset")
            or {}
        )
        schema = toolset.get("open_api_schema") or toolset.get("textSchema", "")
    if schema:
        parts.append(
            "<div class='code-label'>OpenAPI schema:</div>"
            f"<pre class='code'>{html.escape(schema)}</pre>"
        )

    # Data store ref (data store tool)
    ds_spec = tool_dict.get("dataStoreSpec") or tool_dict.get("data_store_spec")
    if ds_spec:
        parts.append(
            "<div class='code-label'>Data store spec:</div>"
            f"<pre class='code'>{html.escape(json.dumps(ds_spec, indent=2))}</pre>"
        )

    # Catch-all: dump the rest as JSON if nothing useful was rendered
    if len(parts) <= 2:
        parts.append(
            "<div class='code-label'>Raw definition:</div>"
            f"<pre class='code'>{html.escape(json.dumps(tool_dict, indent=2, default=str))}</pre>"
        )
    return "\n".join(parts)


def _render_webhook_inner(webhook_dict: dict[str, Any] | None, ref: str) -> str:
    if not webhook_dict:
        return (
            "<div class='hint'>Webhook definition not found in source. "
            f"Ref: <code>{html.escape(ref)}</code></div>"
        )

    parts: list[str] = []
    gws = webhook_dict.get("genericWebService") or {}
    uri = gws.get("uri") or ""
    if uri:
        parts.append(
            f"<div class='meta'><strong>URI:</strong> "
            f"<code>{html.escape(uri)}</code></div>"
        )
    method = gws.get("httpMethod") or gws.get("http_method") or "POST"
    parts.append(
        f"<div class='meta'><strong>Method:</strong> "
        f"<code>{html.escape(str(method))}</code></div>"
    )
    timeout = webhook_dict.get("timeout") or {}
    if timeout:
        parts.append(
            f"<div class='meta'><strong>Timeout:</strong> "
            f"<code>{html.escape(str(timeout.get('seconds', '?')))}s</code></div>"
        )
    body = gws.get("requestBody") or gws.get("request_body")
    if body:
        parts.append(
            "<div class='code-label'>Request body:</div>"
            f"<pre class='code'>{html.escape(body if isinstance(body, str) else json.dumps(body, indent=2))}</pre>"
        )
    headers = gws.get("requestHeaders") or gws.get("request_headers")
    if headers:
        parts.append(
            "<div class='code-label'>Request headers:</div>"
            f"<pre class='code'>{html.escape(json.dumps(headers, indent=2))}</pre>"
        )

    if len(parts) <= 2:
        parts.append(
            "<div class='code-label'>Raw definition:</div>"
            f"<pre class='code'>{html.escape(json.dumps(webhook_dict, indent=2, default=str))}</pre>"
        )
    return "\n".join(parts)


def _pre(content: str) -> str:
    """Wrap pre-formatted text in a styled <pre> block."""
    return f"<pre class='code'>{html.escape(content)}</pre>"


def _nested_details(summary_html: str, body_html: str) -> str:
    return (
        f"<details class='nested'><summary>{summary_html}</summary>"
        f"<div class='nested-body'>{body_html}</div></details>"
    )


def _render_param_defs_table(defs: list[dict[str, Any]]) -> str:
    if not defs:
        return ""
    rows = []
    for d in defs:
        name = d.get("name", "?")
        type_ = (
            d.get("typeSchema", {}).get("inlineSchema", {}).get("type")
            or d.get("parameterType")
            or "?"
        )
        desc = d.get("description") or ""
        rows.append(
            f"<tr><td><code>{html.escape(str(name))}</code></td>"
            f"<td><code>{html.escape(str(type_))}</code></td>"
            f"<td>{html.escape(str(desc))}</td></tr>"
        )
    return (
        "<table><tr><th>Name</th><th>Type</th><th>Description</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def _render_playbook_steps(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return "<em class='dim'>No steps.</em>"
    parts = ["<ol class='steps'>"]
    for s in steps:
        text = s.get("text") or ""
        parts.append(f"<li>{_pre(text)}</li>")
    parts.append("</ol>")
    return "".join(parts)


def _render_playbook_examples(examples: list[dict[str, Any]]) -> str:
    if not examples:
        return "<em class='dim'>No examples.</em>"
    blocks = []
    for ex in examples:
        ex_name = _short(ex.get("name", "")) or "example"
        actions = ex.get("actions") or []
        turn_lines: list[str] = []
        for a in actions:
            if "userUtterance" in a:
                turn_lines.append(
                    f"<div class='turn user'><strong>user:</strong> "
                    f"{html.escape(a['userUtterance'].get('text', ''))}</div>"
                )
            elif "agentUtterance" in a:
                turn_lines.append(
                    f"<div class='turn agent'><strong>agent:</strong> "
                    f"{html.escape(a['agentUtterance'].get('text', ''))}</div>"
                )
            elif "toolUse" in a:
                tu = a["toolUse"]
                turn_lines.append(
                    f"<div class='turn tool'><strong>tool:</strong> "
                    f"<code>{html.escape(tu.get('tool', '') or tu.get('action', ''))}</code></div>"
                )
            elif "playbookInvocation" in a:
                pi = a["playbookInvocation"]
                turn_lines.append(
                    f"<div class='turn tool'><strong>playbook:</strong> "
                    f"<code>{html.escape(_short(pi.get('playbook', '')))}</code></div>"
                )
        body = "".join(turn_lines) or _pre(
            json.dumps(actions, indent=2, default=str)[:2000]
        )
        blocks.append(
            _nested_details(
                f"<code>{html.escape(ex_name)}</code> "
                f"<span class='dim'>· {len(actions)} turns</span>",
                body,
            )
        )
    return "".join(blocks)


def _render_playbook_content(pb: dict[str, Any]) -> str:
    """Return the playbook-specific content blocks: goal, params, steps,
    examples. Each shown as a nested ``<details>`` so the parent row
    stays scannable."""
    parts: list[str] = []

    pb_type = pb.get("playbookType") or "?"
    parts.append(
        f"<div class='meta'><strong>Type:</strong> "
        f"<code>{html.escape(str(pb_type))}</code></div>"
    )

    goal = pb.get("goal") or ""
    if goal:
        parts.append(
            _nested_details(
                f"<span class='pill pill-meta'>goal</span> "
                f"<span class='dim'>{len(goal)} chars</span>",
                _pre(goal),
            )
        )

    inputs = pb.get("inputParameterDefinitions") or []
    outputs = pb.get("outputParameterDefinitions") or []
    if inputs or outputs:
        body = ""
        if inputs:
            body += (
                "<div class='section-label'>Inputs:</div>"
                + _render_param_defs_table(inputs)
            )
        if outputs:
            body += (
                "<div class='section-label'>Outputs:</div>"
                + _render_param_defs_table(outputs)
            )
        parts.append(
            _nested_details(
                f"<span class='pill pill-meta'>params</span> "
                f"<span class='dim'>{len(inputs)} in · "
                f"{len(outputs)} out</span>",
                body,
            )
        )

    steps = (pb.get("instruction") or {}).get("steps") or []
    if steps:
        parts.append(
            _nested_details(
                f"<span class='pill pill-meta'>steps</span> "
                f"<span class='dim'>{len(steps)} total</span>",
                _render_playbook_steps(steps),
            )
        )

    examples = pb.get("examples") or []
    if examples:
        parts.append(
            _nested_details(
                f"<span class='pill pill-meta'>examples</span> "
                f"<span class='dim'>{len(examples)} total</span>",
                _render_playbook_examples(examples),
            )
        )

    code_block = pb.get("codeBlock") or {}
    code_text = code_block.get("code") or ""
    if code_text:
        parts.append(
            _nested_details(
                "<span class='pill pill-meta'>code block</span>",
                _pre(code_text),
            )
        )

    return "".join(parts)


def _render_fulfillment_messages(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return ""
    lines: list[str] = []
    for m in messages:
        text = (m.get("text") or {}).get("text") or []
        for t in text:
            lines.append(
                f"<div class='turn agent'>{html.escape(str(t)[:500])}</div>"
            )
    return "".join(lines) or _pre(
        json.dumps(messages, indent=2, default=str)[:1500]
    )


def _render_flow_page(page_data: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(
        f"<div class='meta'><strong>ID:</strong> "
        f"<code>{html.escape(_short(page_data.get('name', '')))}</code></div>"
    )
    ef = page_data.get("entryFulfillment") or {}
    if ef.get("messages"):
        parts.append("<div class='section-label'>Entry messages:</div>")
        parts.append(_render_fulfillment_messages(ef["messages"]))
    if ef.get("webhook"):
        parts.append(
            f"<div class='meta'><strong>Entry webhook:</strong> "
            f"<code>{html.escape(str(ef['webhook']))}</code></div>"
        )
    form = page_data.get("form") or {}
    if form.get("parameters"):
        parts.append(
            f"<div class='section-label'>Form parameters "
            f"({len(form['parameters'])}):</div>"
        )
        parts.append(_pre(json.dumps(form["parameters"], indent=2)[:1500]))
    routes = page_data.get("transitionRoutes") or []
    if routes:
        parts.append(
            f"<div class='section-label'>Transition routes "
            f"({len(routes)}):</div>"
        )
        parts.append(_pre(json.dumps(routes, indent=2)[:1500]))
    return "".join(parts) or "<em class='dim'>(empty page)</em>"


def _render_flow_content(flow_wrapper) -> str:
    """Return flow-specific content: description, transition routes,
    event handlers, and per-page nested dropdowns."""
    parts: list[str] = []
    flow = flow_wrapper.flow_data

    desc = flow.get("description") or ""
    if desc:
        parts.append(
            f"<div class='meta'><strong>Description:</strong> "
            f"{html.escape(desc)}</div>"
        )

    routes = flow.get("transitionRoutes") or []
    if routes:
        parts.append(
            _nested_details(
                f"<span class='pill pill-meta'>routes</span> "
                f"<span class='dim'>{len(routes)} total</span>",
                _pre(json.dumps(routes, indent=2)[:2500]),
            )
        )

    handlers = flow.get("eventHandlers") or []
    if handlers:
        parts.append(
            _nested_details(
                f"<span class='pill pill-meta'>event handlers</span> "
                f"<span class='dim'>{len(handlers)} total</span>",
                _pre(json.dumps(handlers, indent=2)[:2500]),
            )
        )

    pages = flow_wrapper.pages or []
    if pages:
        page_blocks: list[str] = []
        for p in pages:
            pd = p.page_data
            page_blocks.append(
                _nested_details(
                    f"<span class='pill pill-page'>page</span> "
                    f"<strong>{html.escape(pd.get('displayName', '?'))}</strong>",
                    _render_flow_page(pd),
                )
            )
        parts.append(
            _nested_details(
                f"<span class='pill pill-meta'>pages</span> "
                f"<span class='dim'>{len(pages)} total</span>",
                "".join(page_blocks),
            )
        )

    return "".join(parts)


def _render_resource_details_collapsible(
    rows: list[dict[str, Any]],
    agent_data: DFCXAgentIR,
) -> str:
    """Per-resource ``<details>`` panel. Each row expands to show its
    tools and webhooks as nested ``<details>`` blocks, each of which
    expands further to reveal the tool's code / schema / webhook config.
    """
    tool_idx = _index_tools_by_resource(agent_data)
    webhook_idx = _index_webhooks_by_resource(agent_data)

    out: list[str] = []
    for r in rows:
        # Resolve tool refs → DFCX tool dicts
        tool_blocks: list[str] = []
        for ref in r["tools"]:
            tool_dict = tool_idx.get(ref) or tool_idx.get(_short(ref))
            label = (
                tool_dict.get("displayName", _short(ref))
                if tool_dict
                else _short(ref)
            )
            tool_type = (
                tool_dict.get("toolType", "?") if tool_dict else "unresolved"
            )
            tool_blocks.append(
                "<details class='nested'>"
                f"<summary><span class='pill pill-tool'>tool</span> "
                f"<strong>{html.escape(label)}</strong> "
                f"<span class='dim'>· {html.escape(str(tool_type))}</span>"
                "</summary>"
                f"<div class='nested-body'>{_render_tool_inner(tool_dict, ref)}</div>"
                "</details>"
            )

        # Resolve webhook refs by scanning the playbook/flow body
        body_text = ""
        if r["type"] == "Playbook":
            body_text = json.dumps(r.get("_pb_raw") or {})
        elif r["type"] == "Flow":
            wrapper = r.get("_flow_wrapper")
            if wrapper:
                body_text = json.dumps(wrapper.flow_data)
                for page in wrapper.pages:
                    body_text += json.dumps(page.page_data)
        webhook_refs = _scan_for_webhook_refs(body_text)
        webhook_blocks: list[str] = []
        for ref in sorted(webhook_refs):
            short = _short(ref)
            webhook_dict = webhook_idx.get(short)
            label = (
                webhook_dict.get("displayName", short)
                if webhook_dict
                else short
            )
            webhook_blocks.append(
                "<details class='nested'>"
                f"<summary><span class='pill pill-webhook'>webhook</span> "
                f"<strong>{html.escape(label)}</strong>"
                "</summary>"
                f"<div class='nested-body'>{_render_webhook_inner(webhook_dict, ref)}</div>"
                "</details>"
            )

        outgoing_html = (
            " ".join(
                f"<span class='pill'>{html.escape(t)}</span>"
                for t in r["outgoing"]
            )
            or "<em class='dim'>none</em>"
        )
        incoming_html = (
            " ".join(
                f"<span class='pill'>{html.escape(t)}</span>"
                for t in r["incoming"]
            )
            or "<em class='dim'>none</em>"
        )

        tools_section = (
            f"<div class='section-label'>Tools ({len(tool_blocks)}):</div>"
            + "".join(tool_blocks)
            if tool_blocks
            else "<div class='section-label dim'>Tools: none referenced</div>"
        )
        webhooks_section = (
            f"<div class='section-label'>Webhooks ({len(webhook_blocks)}):</div>"
            + "".join(webhook_blocks)
            if webhook_blocks
            else ""
        )

        # Type-specific content (playbook goal/steps/examples,
        # flow description/pages/routes).
        content_section = ""
        if r["type"] == "Playbook":
            pb = r.get("_pb_raw") or {}
            content_section = _render_playbook_content(pb)
            unit_label = "steps"
        else:
            wrapper = r.get("_flow_wrapper")
            if wrapper is not None:
                content_section = _render_flow_content(wrapper)
            unit_label = "pages"

        type_pill = "pill-flow" if r["type"] == "Flow" else "pill-playbook"
        out.append(
            "<details class='resource'>"
            "<summary>"
            f"<span class='pill {type_pill}'>{html.escape(r['type'])}</span> "
            f"<strong>{html.escape(r['name'])}</strong> "
            f"<span class='dim'>· {r['step_count']} {unit_label} · "
            f"{len(r['tools'])} tools · "
            f"{len(r['outgoing'])} out · {len(r['incoming'])} in</span>"
            "</summary>"
            "<div class='resource-body'>"
            f"<div class='meta'><strong>ID:</strong> "
            f"<code>{html.escape(r['id'])}</code></div>"
            f"{content_section}"
            f"{tools_section}"
            f"{webhooks_section}"
            f"<div class='section-label'>Outgoing refs:</div><div>{outgoing_html}</div>"
            f"<div class='section-label'>Incoming refs:</div><div>{incoming_html}</div>"
            "</div>"
            "</details>"
        )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------


_BASE_CSS = """
  :root {
    --bg:#f8fafc; --card:#ffffff; --border:#e2e8f0; --muted:#64748b;
    --accent:#2563eb; --pill-bg:#eff6ff; --pill-text:#1e40af;
    --code-bg:#0f172a; --code-text:#e2e8f0;
  }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
         margin: 0; padding: 24px; background: var(--bg); color: #0f172a; }
  h1 { margin: 0 0 4px 0; font-size: 24px; }
  h2 { margin: 24px 0 8px 0; font-size: 18px; color: var(--accent); }
  .subtitle { color: var(--muted); margin-bottom: 16px; font-size: 14px; }
  .toc { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
         padding: 12px 16px; margin-bottom: 16px; font-size: 14px; }
  .toc a { color: var(--accent); text-decoration: none; margin-right: 12px; }
  .toc a:hover { text-decoration: underline; }
  details.top { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
                padding: 14px 18px; margin: 12px 0; }
  details.top > summary { font-size: 16px; font-weight: 600; cursor: pointer;
                          color: var(--accent); list-style: none; }
  details.top > summary::before { content: '▶ '; color: var(--muted); }
  details.top[open] > summary::before { content: '▼ '; }
  details.top > .body { margin-top: 12px; }
  details.resource { margin: 4px 0; border: 1px solid var(--border); border-radius: 6px;
                     padding: 6px 12px; background: #fdfdfd; }
  details.resource > summary { cursor: pointer; padding: 4px 0; }
  details.resource > summary:hover { color: var(--accent); }
  .resource-body { padding: 8px 4px 8px 8px; border-left: 2px solid var(--border);
                   margin-top: 6px; }
  details.nested { margin: 4px 0 4px 12px; border-left: 2px solid #cbd5e1;
                   padding-left: 10px; }
  details.nested > summary { cursor: pointer; padding: 2px 0; font-size: 13px; }
  details.nested > summary:hover { color: var(--accent); }
  .nested-body { padding: 6px 4px 8px 4px; font-size: 13px; }
  .meta { margin: 4px 0; font-size: 13px; }
  .section-label { margin: 8px 0 4px 0; font-weight: 600; font-size: 12px;
                   color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
  .code-label { margin: 6px 0 2px 0; font-size: 11px; color: var(--muted);
                text-transform: uppercase; letter-spacing: 0.04em; }
  pre.code { background: var(--code-bg); color: var(--code-text); padding: 10px 12px;
             border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
             font-size: 12px; line-height: 1.45; overflow-x: auto; margin: 4px 0; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
  .stat { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
          padding: 14px 16px; }
  .stat .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
                 color: var(--muted); }
  .stat .value { font-size: 24px; font-weight: 600; margin-top: 4px; }
  .panel { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
           padding: 16px; margin-top: 12px; overflow: auto; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px;
          background: var(--pill-bg); color: var(--pill-text); font-size: 11px;
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin: 1px; }
  .pill-tool { background: #dcfce7; color: #14532d; }
  .pill-webhook { background: #fef3c7; color: #7c2d12; }
  .pill-playbook { background: #dbeafe; color: #1e3a8a; }
  .pill-flow { background: #fde68a; color: #78350f; }
  .pill-meta { background: #ede9fe; color: #5b21b6; }
  .pill-page { background: #e0f2fe; color: #075985; }
  .turn { margin: 4px 0; padding: 4px 8px; border-left: 3px solid var(--border);
          background: #f8fafc; border-radius: 3px; font-size: 13px; }
  .turn.user { border-left-color: #2563eb; }
  .turn.agent { border-left-color: #16a34a; }
  .turn.tool { border-left-color: #d97706; }
  ol.steps { padding-left: 22px; }
  ol.steps li { margin: 4px 0; }
  ol.steps li pre.code { margin: 2px 0; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border);
           vertical-align: top; }
  th { background: #f1f5f9; font-weight: 600; font-size: 12px; }
  .est-banner { background: #fef3c7; border-left: 4px solid #d97706; color: #7c2d12;
                padding: 12px 16px; border-radius: 4px; margin-bottom: 16px; font-size: 14px; }
  .mermaid { background: var(--card); border: 1px solid var(--border); border-radius: 8px;
             padding: 16px; }
  .svg-zoom { border: 1px solid var(--border); border-radius: 8px;
              max-height: 700px; overflow: auto; background: white; padding: 12px; }
  .dim { color: var(--muted); }
  .hint { color: var(--muted); font-size: 12px; font-style: italic; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
  .status-ok { color: #16a34a; } .status-fail { color: #dc2626; }
  .status-skipped { color: #d97706; }
"""


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DFCX Source Tree Preview — {agent_name}</title>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  mermaid.initialize({{ startOnLoad: true, theme: 'default', flowchart: {{ curve: 'basis' }} }});
</script>
<style>{base_css}</style>
</head>
<body>

<h1>{agent_name}</h1>
<div class="subtitle">DFCX source tree preview · default language <code>{default_language}</code></div>

<div class="est-banner">
  <strong>Estimated migration time:</strong> ~{estimated_minutes} minutes for a 1:1
  migration of every playbook + flow. Optimization adds ~30-50% on top for the
  consolidation + Stage 1/2 passes.
</div>

<div class="toc"><strong>Jump to:</strong>
  <a href="#stats">Resource counts</a>
  <a href="#topology">Topology</a>
  <a href="#tools-per-agent">Tools per agent</a>
  <a href="#top-connected">Most-connected</a>
  <a href="#per-resource">Per-resource detail</a>
  <a href="#raw-stats">Raw stats</a>
</div>

<details class="top" id="stats" open>
<summary>Resource counts</summary>
<div class="body"><div class="grid">
  <div class="stat"><div class="label">Playbooks</div><div class="value">{playbook_count}</div></div>
  <div class="stat"><div class="label">Flows</div><div class="value">{flow_count}</div></div>
  <div class="stat"><div class="label">Pages</div><div class="value">{page_count}</div></div>
  <div class="stat"><div class="label">Tools</div><div class="value">{tool_count}</div></div>
  <div class="stat"><div class="label">Webhooks</div><div class="value">{webhook_count}</div></div>
  <div class="stat"><div class="label">Intents</div><div class="value">{intent_count}</div></div>
  <div class="stat"><div class="label">Entities</div><div class="value">{entity_count}</div></div>
  <div class="stat"><div class="label">Code blocks</div><div class="value">{code_block_count}</div></div>
  <div class="stat"><div class="label">Routing edges</div><div class="value">{routing_edge_count}</div></div>
</div></div>
</details>

<details class="top" id="topology" open>
<summary>Topology graph</summary>
<div class="body">{topology_block}</div>
</details>

<details class="top" id="tools-per-agent">
<summary>Tools per agent (top {tools_top_n} by tool count)</summary>
<div class="body"><div class="mermaid">
{mermaid_tools}
</div></div>
</details>

<details class="top" id="top-connected">
<summary>Top {top_connected_n} most-connected resources</summary>
<div class="body"><table>
<tr><th>Resource</th><th>Type</th><th>Degree (in + out)</th></tr>
{top_connected_rows}
</table></div>
</details>

<details class="top" id="per-resource" open>
<summary>Per-resource detail · click any row to expand tools / webhooks</summary>
<div class="body">
{resource_details}
</div>
</details>

<details class="top" id="raw-stats">
<summary>Raw stats (JSON)</summary>
<div class="body"><pre class="code">{stats_json}</pre></div>
</details>

</body>
</html>
"""


def _render_top_connected_rows(top: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"<tr><td>{html.escape(r['name'])}</td>"
        f"<td><span class='pill'>{html.escape(r['type'])}</span></td>"
        f"<td>{r['degree']}</td></tr>"
        for r in top
    )


def generate_html_report(
    agent_data: DFCXAgentIR,
    analyzer: DependencyAnalyzer,
    output_path: str,
    tools_top_n: int = 25,
) -> str:
    """Write a self-contained HTML preview to ``output_path``.

    Top-level sections are wrapped in ``<details>`` so the page renders
    a tidy outline that the user can expand as needed. Per-resource
    rows nest tool/webhook dropdowns showing python code, OpenAPI
    schemas, and webhook URIs/methods.

    Topology defaults to graphviz (legible at scale); Mermaid is used
    only if graphviz isn't available.

    Returns the output path.
    """
    stats = collect_stats(agent_data, analyzer)
    rows = collect_resource_rows(agent_data, analyzer)
    topology_block = _topology_html_block(agent_data, analyzer)
    mermaid_tools = build_mermaid_tools_per_agent(
        agent_data, max_agents=tools_top_n
    )

    rendered = _HTML_TEMPLATE.format(
        base_css=_BASE_CSS,
        agent_name=html.escape(stats["agent_name"] or "DFCX Agent"),
        default_language=html.escape(stats["default_language"] or "?"),
        estimated_minutes=stats["estimated_minutes"],
        playbook_count=stats["playbook_count"],
        flow_count=stats["flow_count"],
        page_count=stats["page_count"],
        tool_count=stats["tool_count"],
        webhook_count=stats["webhook_count"],
        intent_count=stats["intent_count"],
        entity_count=stats["entity_count"],
        code_block_count=stats["code_block_count"],
        routing_edge_count=stats["routing_edge_count"],
        topology_block=topology_block,
        mermaid_tools=mermaid_tools,
        top_connected_n=len(stats["top_connected"]),
        top_connected_rows=_render_top_connected_rows(stats["top_connected"]),
        resource_details=_render_resource_details_collapsible(rows, agent_data),
        tools_top_n=tools_top_n,
        stats_json=html.escape(json.dumps(stats, indent=2, default=str)),
    )
    with open(output_path, "w") as f:
        f.write(rendered)
    return output_path


def write_mermaid_files(
    agent_data: DFCXAgentIR,
    analyzer: DependencyAnalyzer,
    target_name: str,
) -> tuple[str, str]:
    """Write the raw Mermaid sources to disk so users can paste them
    into mermaid.live or include in other docs."""
    topo_path = f"{target_name}_topology.mmd"
    tools_path = f"{target_name}_tools.mmd"
    with open(topo_path, "w") as f:
        f.write(build_mermaid_topology(agent_data, analyzer))
    with open(tools_path, "w") as f:
        f.write(build_mermaid_tools_per_agent(agent_data))
    return topo_path, tools_path


# ---------------------------------------------------------------------------
# Rich-tree → HTML helpers (per-resource tree visualizers)
# ---------------------------------------------------------------------------


def render_playbook_trees_html(agent_data: DFCXAgentIR) -> str:
    chunks: list[str] = []
    for playbook_wrapper in agent_data.playbooks:
        playbook = (
            playbook_wrapper.get("playbook", playbook_wrapper)
            if isinstance(playbook_wrapper, dict)
            else playbook_wrapper
        )
        try:
            tree = PlaybookTreeVisualizer(playbook).build_tree()
            chunks.append(rich_to_html(Panel(tree, title="Playbook")))
        except Exception as exc:  # noqa: BLE001
            chunks.append(
                f"<pre>Failed to render playbook: {html.escape(str(exc))}</pre>"
            )
    return "\n".join(chunks) or "<em>No playbooks.</em>"


def render_flow_trees_html(agent_data: DFCXAgentIR) -> str:
    chunks: list[str] = []
    try:
        resolver = FlowDependencyResolver(agent_data)
    except Exception as exc:  # noqa: BLE001
        return f"<pre>Failed to build resolver: {html.escape(str(exc))}</pre>"
    for flow_wrapper in agent_data.flows:
        try:
            context = resolver.resolve(flow_wrapper)
            tree = FlowTreeVisualizer(context).build_tree()
            chunks.append(rich_to_html(Panel(tree, title="Flow")))
        except Exception as exc:  # noqa: BLE001
            chunks.append(
                f"<pre>Failed to render flow: {html.escape(str(exc))}</pre>"
            )
    return "\n".join(chunks) or "<em>No flows.</em>"


# ---------------------------------------------------------------------------
# StageReport — multi-stage HTML accumulator
# ---------------------------------------------------------------------------


_STAGE_HTML_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  mermaid.initialize({{ startOnLoad: true, theme: 'default', flowchart: {{ curve: 'basis' }} }});
</script>
<style>{base_css}</style>
</head>
<body>

<h1>{title}</h1>
<div class="subtitle">{subtitle}</div>

<div class="toc"><strong>Stages:</strong> {toc}</div>
"""


_STAGE_HTML_TAIL = "\n</body>\n</html>\n"


class StageReport:
    """Accumulates HTML snapshots across the migration / optimization
    pipeline and writes a single multi-section HTML report at the end.

    Each ``add_*`` call appends a ``<details open>`` block; the table of
    contents links to all sections by anchor."""

    def __init__(self, title: str, subtitle: str = ""):
        self.title = title
        self.subtitle = subtitle
        self._stages: list[tuple[str, str, str]] = []

    def add_section(
        self, label: str, body_html: str, anchor: str | None = None
    ) -> None:
        anchor = anchor or _SAFE_ID_RE.sub("_", label).lower()[:60]
        self._stages.append((anchor, label, body_html))

    def add_source_overview(
        self, agent_data: DFCXAgentIR, analyzer: DependencyAnalyzer
    ) -> None:
        stats = collect_stats(agent_data, analyzer)
        cards = "\n".join(
            f'<div class="stat"><div class="label">{k}</div>'
            f'<div class="value">{v}</div></div>'
            for k, v in [
                ("Playbooks", stats["playbook_count"]),
                ("Flows", stats["flow_count"]),
                ("Pages", stats["page_count"]),
                ("Tools", stats["tool_count"]),
                ("Webhooks", stats["webhook_count"]),
                ("Intents", stats["intent_count"]),
                ("Entities", stats["entity_count"]),
                ("Code blocks", stats["code_block_count"]),
                ("Routing edges", stats["routing_edge_count"]),
                ("Est. mins", stats["estimated_minutes"]),
            ]
        )
        self.add_section(
            "Source overview",
            f'<div class="grid">{cards}</div>',
            anchor="source-overview",
        )

    def add_topology_svg(self, agent_data: DFCXAgentIR) -> None:
        analyzer = DependencyAnalyzer(agent_data)
        body = _topology_html_block(agent_data, analyzer)
        self.add_section("Topology graph", body, anchor="topology")

    def add_playbook_and_flow_trees(self, agent_data: DFCXAgentIR) -> None:
        self.add_section(
            "Playbook trees",
            render_playbook_trees_html(agent_data),
            anchor="playbooks",
        )
        self.add_section(
            "Flow trees",
            render_flow_trees_html(agent_data),
            anchor="flows",
        )

    def add_ir_snapshot(self, label: str, ir_tree: Tree) -> None:
        """Take a pre-built Rich Tree (the caller renders it from the IR
        however it likes) and capture it as HTML. Decouples StageReport
        from any specific IR tree builder."""
        self.add_section(
            label,
            rich_to_html(ir_tree),
            anchor=_SAFE_ID_RE.sub("_", label).lower()[:60],
        )

    def add_grouping_table(self, groupings: dict) -> None:
        rows = []
        for name, payload in groupings.items():
            members = ", ".join(payload.get("agents", []) or [])
            journey = (payload.get("journey") or "").replace("|", "\\|")
            is_root = "yes" if payload.get("is_root") else ""
            rows.append(
                f"<tr><td><code>{html.escape(name)}</code></td>"
                f"<td>{html.escape(members)}</td>"
                f"<td>{html.escape(journey)}</td>"
                f"<td>{is_root}</td></tr>"
            )
        body = (
            "<table><tr><th>Group</th><th>Members</th><th>Journey</th>"
            "<th>Root</th></tr>" + "\n".join(rows) + "</table>"
        )
        self.add_section("Grouping proposal", body, anchor="grouping")

    def add_optimizer_logs(self, label: str, logs: list[dict] | None) -> None:
        if not logs:
            return
        rows = "\n".join(
            f"<tr><td><code>{html.escape(str(e.get('stage', '?')))}</code></td>"
            f"<td><code>{html.escape(str(e.get('action', '?')))}</code></td>"
            f"<td>{html.escape(str(e.get('details', '')))}</td></tr>"
            for e in logs
        )
        body = (
            "<table><tr><th>Stage</th><th>Action</th><th>Details</th></tr>"
            + rows
            + "</table>"
        )
        self.add_section(label, body, anchor=label.lower().replace(" ", "-"))

    def add_phase_timeline(self, tracker_records: list[dict]) -> None:
        if not tracker_records:
            return
        rows = []
        for rec in tracker_records:
            status = rec.get("status", "?")
            cls = {
                "ok": "status-ok",
                "fail": "status-fail",
                "skipped": "status-skipped",
            }.get(status, "")
            duration = (
                f"{rec['duration_s']:.1f}s"
                if rec.get("duration_s") is not None
                else "—"
            )
            rows.append(
                f"<tr><td><code>{html.escape(rec.get('label', '?'))}</code></td>"
                f"<td>{html.escape(rec.get('description', ''))}</td>"
                f"<td class='{cls}'>{html.escape(status)}</td>"
                f"<td>{duration}</td>"
                f"<td>{html.escape(rec.get('note', ''))}</td></tr>"
            )
        body = (
            "<table><tr><th>Phase</th><th>Description</th><th>Status</th>"
            "<th>Duration</th><th>Note</th></tr>" + "\n".join(rows) + "</table>"
        )
        self.add_section("Pipeline timeline", body, anchor="timeline")

    def add_versions(self, versions: list[tuple[str, str]]) -> None:
        if not versions:
            return
        rows = "\n".join(
            f"<tr><td><code>{html.escape(d)}</code></td>"
            f"<td>{html.escape(desc)}</td></tr>"
            for d, desc in versions
        )
        body = (
            "<table><tr><th>Version</th><th>Description</th></tr>"
            + rows
            + "</table>"
        )
        self.add_section("CXAS Version checkpoints", body, anchor="versions")

    def add_app_url(self, app_url: str) -> None:
        if not app_url:
            return
        self.add_section(
            "App",
            f'<p><a href="{html.escape(app_url)}" target="_blank">'
            f"{html.escape(app_url)}</a></p>",
            anchor="app",
        )

    def write(self, path: str) -> str:
        toc = " · ".join(
            f'<a href="#{anchor}">{html.escape(label)}</a>'
            for anchor, label, _ in self._stages
        )
        head = _STAGE_HTML_HEAD.format(
            base_css=_BASE_CSS,
            title=html.escape(self.title),
            subtitle=html.escape(self.subtitle),
            toc=toc,
        )
        body = "\n".join(
            f'<details class="top" id="{anchor}" open>'
            f"<summary>{html.escape(label)}</summary>"
            f'<div class="body">{body_html}</div></details>'
            for anchor, label, body_html in self._stages
        )
        with open(path, "w") as f:
            f.write(head + body + _STAGE_HTML_TAIL)
        return path


# ---------------------------------------------------------------------------
# Backwards-compat re-exports
# ---------------------------------------------------------------------------


# Older callers expected this private helper to exist; keep an alias so
# the skill's existing re-export shim doesn't break.
_rich_to_html = rich_to_html
