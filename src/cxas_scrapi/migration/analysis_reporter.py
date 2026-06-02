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

"""MigrationAnalysisBuilder: emits a self-contained, tabbed HTML report
that captures the state of a DFCX→CXAS migration as it runs.

The builder is owned by :class:`MigrationService`. The service calls
``checkpoint(phase_name, what_changed)`` at each natural phase boundary
(end of variable extraction, end of fast deploy, end of Stage 1
consolidation, etc.). Each checkpoint refreshes the snapshot from the
live service state and writes two files atomically next to the IR
bundle:

* ``<target>_migration_analysis.json`` — the raw snapshot
* ``<target>_migration_analysis.html`` — the rendered report

The HTML opens directly in a browser, requires no network beyond the
Mermaid CDN, and renders a 6-tab vanilla-JS app: Overview, CXAS Agents,
Tools & Variables, Evals, DFCX Source, Pipeline.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Template

if TYPE_CHECKING:
    from cxas_scrapi.migration.data_models import (
        DFCXAgentIR,
        IRBundle,
        MigrationIR,
    )

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).parent / "analysis_report_template.html"

_MOCK_MODE_RE = re.compile(r"""get_variable\(\s*['"]mock_mode['"]\s*\)""")


# --- Snapshot dataclass --------------------------------------------------


@dataclass
class PipelineStep:
    name: str
    kind: str  # "skill" | "custom"
    started_at: str
    duration_s: float
    what_changed: str


@dataclass
class MigrationAnalysisSnapshot:
    """All data the HTML report needs. Mutated in place by the builder."""

    app_name: str = ""
    target_name: str = ""
    generated_at: str = ""
    kpis: dict[str, Any] = field(default_factory=dict)
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    toolsets: dict[str, dict[str, Any]] = field(default_factory=dict)
    variables: list[dict[str, Any]] = field(default_factory=list)
    flows: list[dict[str, Any]] = field(default_factory=list)
    pipeline: list[dict[str, Any]] = field(default_factory=list)
    grouping: dict[str, dict[str, Any]] = field(default_factory=dict)
    evals: dict[str, Any] | None = None  # stubbed in this PR
    eval_traces: dict[str, Any] | None = None  # stubbed in this PR
    references: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- Builder -------------------------------------------------------------


class MigrationAnalysisBuilder:
    """Stateful aggregator + renderer for the migration analysis report."""

    def __init__(
        self,
        target_name: str,
        app_name: str,
        output_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.target_name = target_name
        self.app_name = app_name
        self.output_dir = Path(output_dir) if output_dir else Path.cwd()
        self.snapshot = MigrationAnalysisSnapshot(
            app_name=app_name, target_name=target_name
        )
        self._pipeline: list[PipelineStep] = []
        self._template: Template | None = None

    # ----- Public API ---------------------------------------------------

    @property
    def html_path(self) -> Path:
        return self.output_dir / f"{self.target_name}_migration_analysis.html"

    @property
    def json_path(self) -> Path:
        return self.output_dir / f"{self.target_name}_migration_analysis.json"

    def record_phase(
        self,
        name: str,
        what_changed: str,
        *,
        kind: str = "skill",
        duration_s: float = 0.0,
    ) -> None:
        self._pipeline.append(
            PipelineStep(
                name=name,
                kind=kind,
                started_at=datetime.now().isoformat(timespec="seconds"),
                duration_s=duration_s,
                what_changed=what_changed,
            )
        )

    def update_from_service(self, service: Any) -> None:
        """Refresh the snapshot from the live service state.

        Resilient: any per-section failure is logged and skipped so a
        partial report still flushes.
        """
        try:
            ir = getattr(service, "ir", None)
            source = getattr(service, "source_agent_data", None)
            bundle = getattr(service, "_analysis_bundle", None)
            grouping = (
                getattr(bundle, "grouping", None)
                if bundle is not None
                else None
            )

            self.snapshot.generated_at = datetime.now().isoformat(
                timespec="seconds"
            )
            self.snapshot.kpis = self._derive_kpis(ir, source, bundle)
            self.snapshot.tools, self.snapshot.toolsets = self._derive_tools(ir)
            self.snapshot.agents = self._derive_agents(ir, grouping)
            self.snapshot.variables = self._derive_variables(ir)
            self.snapshot.flows = self._derive_flows(source, grouping)
            self.snapshot.grouping = self._derive_grouping(grouping)
            self._wire_callers()
            self.snapshot.references = self._derive_references(ir, bundle)
        except Exception as exc:  # noqa: BLE001
            logger.warning("analysis snapshot refresh failed: %s", exc)

    def flush(self) -> None:
        """Atomically write the JSON + HTML to disk. Never raises."""
        try:
            self.snapshot.pipeline = [asdict(p) for p in self._pipeline]
            self.output_dir.mkdir(parents=True, exist_ok=True)
            data = self.snapshot.to_dict()
            self._atomic_write(
                self.json_path, json.dumps(data, indent=2, default=str)
            )
            html = self._render_html(data)
            self._atomic_write(self.html_path, html)
            logger.info("migration analysis report → %s", self.html_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("analysis report flush failed: %s", exc)

    # ----- Derivation helpers ------------------------------------------

    def _derive_kpis(
        self,
        ir: "MigrationIR | None",
        source: "DFCXAgentIR | None",
        bundle: "IRBundle | None",
    ) -> dict[str, Any]:
        kpis: dict[str, Any] = {}
        if source is not None:
            kpis["dfcx_flows"] = len(getattr(source, "flows", []) or [])
            kpis["dfcx_pages_total"] = sum(
                len(getattr(f, "pages", []) or []) for f in source.flows or []
            )
            kpis["dfcx_intents"] = len(getattr(source, "intents", []) or [])
            kpis["dfcx_entity_types"] = len(
                getattr(source, "entity_types", []) or []
            )
            kpis["dfcx_webhooks"] = len(getattr(source, "webhooks", []) or [])
            kpis["dfcx_testcases"] = len(
                getattr(source, "test_cases", []) or []
            )
            kpis["dfcx_playbooks"] = len(getattr(source, "playbooks", []) or [])
        if ir is not None:
            kpis["cxas_agents"] = len(ir.agents or {})
            kpis["cxas_tools"] = sum(
                1 for t in (ir.tools or {}).values() if t.type != "TOOLSET"
            )
            kpis["cxas_toolsets"] = sum(
                1 for t in (ir.tools or {}).values() if t.type == "TOOLSET"
            )
            kpis["cxas_variables"] = len(ir.parameters or {})
            kpis["app_resource"] = ir.metadata.app_resource_name or ""
        if bundle is not None and ir is not None:
            # Best-effort: surface dedup + lint counts from optimization logs
            # if Stage 1 / 2 has written them. Absent fields render as blank
            # in the report.
            opt = getattr(ir, "optimization_logs", {}) or {}
            stage_1 = (opt.get("stages") or {}).get("stage_1") or {}
            stage_2 = (opt.get("stages") or {}).get("stage_2") or {}
            if isinstance(stage_1, dict):
                kpis["stage_1_variables_before"] = stage_1.get(
                    "parameters_before"
                )
                kpis["stage_1_variables_after"] = stage_1.get(
                    "parameters_after"
                )
            if isinstance(stage_2, dict):
                kpis["stage_2_lint_baseline"] = stage_2.get("lint_baseline")
                kpis["stage_2_lint_final"] = stage_2.get("lint_final")
        return kpis

    def _derive_tools(
        self, ir: "MigrationIR | None"
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        tools: dict[str, dict[str, Any]] = {}
        toolsets: dict[str, dict[str, Any]] = {}
        if ir is None:
            return tools, toolsets
        for tool_id, tool in (ir.tools or {}).items():
            payload = tool.payload or {}
            display = (
                payload.get("displayName")
                or payload.get("display_name")
                or tool_id
            )
            description = payload.get("description", "")
            if tool.type == "TOOLSET":
                openapi = payload.get("open_api_toolset", {}) or {}
                server_url = ""
                spec = openapi.get("text_schema") or openapi.get("schema") or ""
                if isinstance(spec, str):
                    # crude grep for "url:" entry in raw spec
                    match = re.search(r"url:\s*['\"]?([^\s'\"]+)", spec)
                    if match:
                        server_url = match.group(1)
                operations = tool.operation_ids or []
                toolsets[tool_id] = {
                    "name": tool_id,
                    "display_name": display,
                    "description": description,
                    "server_url": server_url,
                    "mocked_server": bool(
                        server_url and server_url.startswith("$")
                    ),
                    "operations": operations,
                    "operation_count": len(operations),
                    "callers": [],
                }
            else:
                py = payload.get("pythonFunction") or {}
                code = py.get("code") or payload.get("code") or ""
                docstring = ""
                signature = ""
                mock_excerpt = ""
                if isinstance(code, str) and code:
                    sig_match = re.search(r"^def\s+[^\n]+", code, re.MULTILINE)
                    if sig_match:
                        signature = sig_match.group(0).rstrip(":")
                    doc_match = re.search(r"\"\"\"(.+?)\"\"\"", code, re.DOTALL)
                    if doc_match:
                        docstring = doc_match.group(1).strip().split("\n")[0]
                    mock_excerpt = self._extract_mock_excerpt(code)
                mocked_fn = bool(code) and bool(_MOCK_MODE_RE.search(code))
                kind = "python_function" if code else (tool.type or "").lower()
                tools[tool_id] = {
                    "name": tool_id,
                    "display_name": display,
                    "description": description or docstring,
                    "docstring": docstring,
                    "signature": signature,
                    "mocked_fn": mocked_fn,
                    "mock_excerpt": mock_excerpt,
                    "callers": [],
                    "kind": kind,
                }
        return tools, toolsets

    @staticmethod
    def _extract_mock_excerpt(code: str) -> str:
        match = _MOCK_MODE_RE.search(code)
        if not match:
            return ""
        start = code.rfind("\n", 0, match.start())
        end = match.end()
        # take up to ~10 lines after the match
        lines_after = code[end:].splitlines()[:9]
        prefix = code[start + 1 : match.end()].splitlines()[0]
        return "\n".join([prefix] + lines_after).strip("\n")

    def _derive_agents(
        self,
        ir: "MigrationIR | None",
        grouping: dict[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        agents: dict[str, dict[str, Any]] = {}
        if ir is None:
            return agents
        # build reverse name->id map for resource-name pills
        tool_resource_to_id = {
            t.name: t_id for t_id, t in (ir.tools or {}).items()
        }
        for name, agent in (ir.agents or {}).items():
            tool_ids = []
            for ref in agent.tools or []:
                tool_ids.append(
                    tool_resource_to_id.get(ref, ref.split("/")[-1])
                )
            toolset_ids = []
            for entry in agent.toolsets or []:
                toolset_resource = (
                    entry.get("toolset") if isinstance(entry, dict) else entry
                )
                if not toolset_resource:
                    continue
                toolset_ids.append(
                    tool_resource_to_id.get(
                        toolset_resource, toolset_resource.split("/")[-1]
                    )
                )
            g_entry = (grouping or {}).get(name, {}) if grouping else {}
            agents[name] = {
                "name": name,
                "type": agent.type,
                "description": agent.description or "",
                "is_root": bool(g_entry.get("is_root")),
                "absorbed_flows": list(g_entry.get("agents") or []),
                "rationale": g_entry.get("rationale", ""),
                "journey": g_entry.get("journey", ""),
                "tools": tool_ids,
                "toolsets": toolset_ids,
                "child_agents": [],  # filled below from routing_edges
                "model": (agent.model_settings or {}).get(
                    "model", ir.metadata.default_model
                ),
                "instruction_excerpt": (agent.instruction or "")[:2048],
                "status": str(agent.status),
            }
        # routing_edges → child_agents
        for edge in ir.routing_edges or []:
            parent = edge.get("parent") or edge.get("from")
            child = edge.get("child") or edge.get("to")
            if parent in agents and child:
                agents[parent]["child_agents"].append(child)
        return agents

    def _derive_variables(
        self, ir: "MigrationIR | None"
    ) -> list[dict[str, Any]]:
        if ir is None:
            return []
        out: list[dict[str, Any]] = []
        for name, param in sorted((ir.parameters or {}).items()):
            schema = (param or {}).get("schema") or {}
            out.append(
                {
                    "name": name,
                    "type": (schema.get("type") or "STRING"),
                    "default": schema.get("default"),
                    "description": (param or {}).get("description", ""),
                    "referenced_by": [],
                }
            )
        return out

    def _derive_flows(
        self,
        source: "DFCXAgentIR | None",
        grouping: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if source is None:
            return []
        # invert grouping: flow_name -> group_name
        flow_to_group: dict[str, str] = {}
        for group_name, entry in (grouping or {}).items():
            for flow_name in entry.get("agents", []):
                flow_to_group[flow_name] = group_name
        out: list[dict[str, Any]] = []
        for flow in source.flows or []:
            data = flow.flow_data or {}
            name = data.get("displayName") or flow.flow_id.split("/")[-1]
            pages = flow.pages or []
            sample_pages = [
                p.page_data.get("displayName", "") for p in pages[:5]
            ]
            out.append(
                {
                    "name": name,
                    "description": data.get("description", ""),
                    "transition_routes": len(data.get("transitionRoutes", [])),
                    "event_handlers": len(data.get("eventHandlers", [])),
                    "nlu": bool(data.get("nluSettings")),
                    "page_count": len(pages),
                    "sample_pages": sample_pages,
                    "absorbed_by_group": flow_to_group.get(name, ""),
                }
            )
        # sort by complexity (most pages first), matches reference
        out.sort(key=lambda f: f["page_count"], reverse=True)
        return out

    def _derive_grouping(
        self, grouping: dict[str, Any] | None
    ) -> dict[str, dict[str, Any]]:
        if not grouping:
            return {}
        return {
            name: {
                "agents": list(entry.get("agents") or []),
                "rationale": entry.get("rationale", ""),
                "journey": entry.get("journey", ""),
                "is_root": bool(entry.get("is_root")),
            }
            for name, entry in grouping.items()
        }

    def _wire_callers(self) -> None:
        """Build the reverse 'used by' index across tools/toolsets/vars."""
        agents = self.snapshot.agents
        tools = self.snapshot.tools
        toolsets = self.snapshot.toolsets
        variables = self.snapshot.variables
        # reset
        for t in tools.values():
            t["callers"] = []
        for t in toolsets.values():
            t["callers"] = []
        for v in variables:
            v["referenced_by"] = []

        for agent_name, agent in agents.items():
            for tid in agent.get("tools", []):
                if tid in tools and agent_name not in tools[tid]["callers"]:
                    tools[tid]["callers"].append(agent_name)
                if (
                    tid in toolsets
                    and agent_name not in toolsets[tid]["callers"]
                ):
                    toolsets[tid]["callers"].append(agent_name)
            for tsid in agent.get("toolsets", []):
                if (
                    tsid in toolsets
                    and agent_name not in toolsets[tsid]["callers"]
                ):
                    toolsets[tsid]["callers"].append(agent_name)
            # variables referenced in instruction text
            instruction = agent.get("instruction_excerpt", "") or ""
            for v in variables:
                token = "{" + v["name"] + "}"
                if (
                    token in instruction
                    and agent_name not in v["referenced_by"]
                ):
                    v["referenced_by"].append(agent_name)

    def _derive_references(
        self,
        ir: "MigrationIR | None",
        bundle: "IRBundle | None",
    ) -> list[dict[str, str]]:
        refs: list[dict[str, str]] = []
        if ir is not None and ir.metadata.app_resource_name:
            app_id = ir.metadata.app_id or ""
            refs.append(
                {
                    "label": "Live CXAS app",
                    "value": ir.metadata.app_resource_name,
                    "href": (
                        f"https://ces.cloud.google.com/{ir.metadata.app_resource_name}"
                        if app_id
                        else ""
                    ),
                }
            )
        target = (
            bundle.config.target_name
            if bundle is not None
            else self.target_name
        )
        refs.append(
            {
                "label": "IR bundle",
                "value": f"{target}_ir.json",
                "href": f"{target}_ir.json",
            }
        )
        refs.append(
            {
                "label": "Migration log (markdown)",
                "value": f"{target}_migration_report.md",
                "href": f"{target}_migration_report.md",
            }
        )
        return refs

    # ----- Rendering ----------------------------------------------------

    def _render_html(self, data: dict[str, Any]) -> str:
        if self._template is None:
            self._template = Template(
                _TEMPLATE_PATH.read_text(encoding="utf-8")
            )
        data_json = json.dumps(data, default=str, separators=(",", ":"))
        return self._template.render(
            app_name=self.app_name,
            target_name=self.target_name,
            generated_at=self.snapshot.generated_at,
            data_json=data_json,
            mermaid_chart=self._build_mermaid_chart(),
        )

    def _build_mermaid_chart(self) -> str:
        """Render a small flowchart describing the consolidated topology.

        Falls back to a single-node placeholder when no agents exist yet.
        """
        agents = self.snapshot.agents
        if not agents:
            return (
                "flowchart LR\n"
                '  placeholder(["Migration in progress — agents not yet'
                ' compiled."])\n'
            )
        lines = [
            "flowchart LR",
            "  classDef root fill:#ede9fe,stroke:#6d28d9,color:#312e81,"
            "font-weight:bold,stroke-width:2px;",
            "  classDef leaf fill:#ede9fe,stroke:#6d28d9,color:#312e81;",
            "  classDef caller fill:#e0f2fe,stroke:#0369a1,color:#0c4a6e;",
            "  classDef exit fill:#fee2e2,stroke:#be123c,color:#7f1d1d;",
            '  caller(["Inbound"]):::caller',
        ]
        root_name = next(
            (n for n, a in agents.items() if a.get("is_root")), None
        )
        if root_name is None:
            root_name = next(iter(agents))
        for name, _agent in agents.items():
            safe_id = re.sub(r"[^A-Za-z0-9]", "_", name)
            label = name + ("<br/><i>root</i>" if name == root_name else "")
            cls = "root" if name == root_name else "leaf"
            lines.append(f'  {safe_id}["{label}"]:::{cls}')
        root_id = re.sub(r"[^A-Za-z0-9]", "_", root_name)
        lines.append(f"  caller --> {root_id}")
        for name, _agent in agents.items():
            if name == root_name:
                continue
            safe_id = re.sub(r"[^A-Za-z0-9]", "_", name)
            lines.append(f"  {root_id} --> {safe_id}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
