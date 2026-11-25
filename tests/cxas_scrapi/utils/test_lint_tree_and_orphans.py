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

"""Unit tests for S014-S017, YAML/displayName S007 hardening, and T008 reachability."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import yaml

from cxas_scrapi.utils.lint_rules.structure import (
    AgentTreeCycle,
    OrphanedAgent,
    SubAgentSingleParent,
    UnusedGuardrail,
    UnusedToolset,
)
from cxas_scrapi.utils.lint_rules.tools import ToolDisplayNameUnreferenced
from cxas_scrapi.utils.linter import (
    _MAX_AGENT_CYCLES,
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    Severity,
    _find_cycles,
    build_context,
    build_registry,
    resolve_agent_graph,
    run_rules,
)


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _ctx(app_root: Path, project_root: Path | None = None) -> LintContext:
    proj = project_root or app_root
    return LintContext(
        project_root=proj,
        app_dir=app_root,
        evals_dir=proj / "evals",
        app_root=app_root,
    )


def _make_json_agent(
    app_root: Path,
    dir_name: str,
    *,
    display_name: str | None = None,
    name: str | None = None,
    child_agents: list[str] | None = None,
    tools: list[str] | None = None,
    toolsets: list[Any] | None = None,
    guardrails: list[str] | None = None,
) -> Path:
    data: dict[str, Any] = {
        "displayName": display_name or dir_name,
        "instruction": f"agents/{dir_name}/instruction.txt",
    }
    if name is not None:
        data["name"] = name
    if child_agents is not None:
        data["childAgents"] = child_agents
    if tools is not None:
        data["tools"] = tools
    if toolsets is not None:
        data["toolsets"] = toolsets
    if guardrails is not None:
        data["guardrails"] = guardrails
    agent_dir = app_root / "agents" / dir_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "instruction.txt").write_text(
        "<role>Agent.</role>\n", encoding="utf-8"
    )
    return _write_json(agent_dir / f"{dir_name}.json", data)


def _make_yaml_agent(
    app_root: Path,
    dir_name: str,
    *,
    ext: str = ".yaml",
    display_name: str | None = None,
    name: str | None = None,
    child_agents: list[str] | None = None,
    tools: list[str] | None = None,
    toolsets: list[Any] | None = None,
    guardrails: list[str] | None = None,
) -> Path:
    data: dict[str, Any] = {
        "displayName": display_name or dir_name,
        "instruction": f"agents/{dir_name}/instruction.txt",
    }
    if name is not None:
        data["name"] = name
    if child_agents is not None:
        data["childAgents"] = child_agents
    if tools is not None:
        data["tools"] = tools
    if toolsets is not None:
        data["toolsets"] = toolsets
    if guardrails is not None:
        data["guardrails"] = guardrails
    agent_dir = app_root / "agents" / dir_name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "instruction.txt").write_text(
        "<role>Agent.</role>\n", encoding="utf-8"
    )
    return _write_yaml(agent_dir / f"{dir_name}{ext}", data)


def _make_py_tool(
    app_root: Path,
    tool_name: str,
    *,
    display_name: str | None = None,
    ext: str = ".json",
    py_code: str | None = None,
) -> Path:
    tool_dir = app_root / "tools" / tool_name
    py_dir = tool_dir / "python_function"
    py_dir.mkdir(parents=True, exist_ok=True)
    py_path = py_dir / "python_code.py"
    py_path.write_text(
        py_code or f"def {tool_name}() -> dict:\n    return {{}}\n",
        encoding="utf-8",
    )
    cfg = {
        "name": tool_name,
        "displayName": display_name or tool_name,
        "pythonFunction": {
            "pythonCode": f"tools/{tool_name}/python_function/python_code.py"
        },
    }
    if ext == ".json":
        _write_json(tool_dir / f"{tool_name}.json", cfg)
    else:
        _write_yaml(tool_dir / f"{tool_name}{ext}", cfg)
    return py_path


# ── S014: agent-tree-cycle ──────────────────────────────────────────────


def test_s014_self_loop_flagged_as_error(tmp_path: Path) -> None:
    app_json = _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _make_json_agent(tmp_path, "root", child_agents=["root"])

    results = AgentTreeCycle().check(app_json, "", _ctx(tmp_path))
    assert len(results) == 1
    assert results[0].rule_id == "S014"
    assert results[0].severity == Severity.ERROR
    assert "root -> root" in results[0].message


def test_s014_two_node_and_three_node_cycles_deduped(tmp_path: Path) -> None:
    app_2 = tmp_path / "two_node"
    cfg_2 = _write_json(
        app_2 / "app.json", {"displayName": "app", "rootAgent": "root_agent"}
    )
    _make_json_agent(app_2, "root_agent", child_agents=["billing"])
    _make_json_agent(app_2, "billing", child_agents=["root_agent"])

    res_2 = AgentTreeCycle().check(cfg_2, "", _ctx(app_2))
    assert len(res_2) == 1
    assert "root_agent -> billing -> root_agent" in res_2[0].message

    app_3 = tmp_path / "three_node"
    cfg_3 = _write_json(
        app_3 / "app.json", {"displayName": "app", "rootAgent": "root_agent"}
    )
    _make_json_agent(app_3, "root_agent", child_agents=["step_a"])
    _make_json_agent(app_3, "step_a", child_agents=["step_b"])
    _make_json_agent(app_3, "step_b", child_agents=["root_agent"])

    res_3 = AgentTreeCycle().check(cfg_3, "", _ctx(app_3))
    assert len(res_3) == 1
    assert "root_agent -> step_a -> step_b -> root_agent" in res_3[0].message


def test_s014_disconnected_cycle_detected(tmp_path: Path) -> None:
    app_json = _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _make_json_agent(tmp_path, "root", child_agents=["leaf"])
    _make_json_agent(tmp_path, "leaf")
    _make_json_agent(tmp_path, "island_a", child_agents=["island_b"])
    _make_json_agent(tmp_path, "island_b", child_agents=["island_a"])

    results = AgentTreeCycle().check(app_json, "", _ctx(tmp_path))
    assert len(results) == 1
    assert "island_a -> island_b -> island_a" in results[0].message


def test_s014_cycle_via_display_name_detected(tmp_path: Path) -> None:
    app_json = _write_json(
        tmp_path / "app.json",
        {"displayName": "app", "rootAgent": "Main Concierge"},
    )
    _make_json_agent(
        tmp_path,
        "root_agent",
        display_name="Main Concierge",
        child_agents=["Billing Desk"],
    )
    _make_json_agent(
        tmp_path,
        "billing_agent",
        display_name="Billing Desk",
        child_agents=["Main Concierge"],
    )

    results = AgentTreeCycle().check(app_json, "", _ctx(tmp_path))
    assert len(results) == 1
    assert "root_agent -> billing_agent -> root_agent" in results[0].message


def test_s014_clean_tree_zero_errors(tmp_path: Path) -> None:
    app_json = _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _make_json_agent(tmp_path, "root", child_agents=["billing", "support"])
    _make_json_agent(tmp_path, "billing", child_agents=["refunds"])
    _make_json_agent(tmp_path, "refunds")
    _make_json_agent(tmp_path, "support")

    assert AgentTreeCycle().check(app_json, "", _ctx(tmp_path)) == []


# ── S015: orphaned-agent ────────────────────────────────────────────────


def test_s015_orphaned_agent_and_orphaned_subtree(tmp_path: Path) -> None:
    app_json = _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _make_json_agent(tmp_path, "root", child_agents=["active"])
    _make_json_agent(tmp_path, "active")
    _make_json_agent(
        tmp_path,
        "orphan_a",
        display_name="Legacy Intake",
        child_agents=["orphan_b"],
    )
    _make_json_agent(tmp_path, "orphan_b")

    results = OrphanedAgent().check(app_json, "", _ctx(tmp_path))
    assert len(results) == 2
    assert all(r.rule_id == "S015" for r in results)
    assert all(r.severity == Severity.WARNING for r in results)
    by_file = {r.file: r.message for r in results}
    assert "agents/orphan_a/orphan_a.json" in by_file
    assert "Legacy Intake" in by_file["agents/orphan_a/orphan_a.json"]
    assert "root" in by_file["agents/orphan_a/orphan_a.json"]
    assert "agents/orphan_b/orphan_b.json" in by_file


def test_s015_silent_when_root_agent_missing_or_invalid(tmp_path: Path) -> None:
    for case_name, app_data in [
        ("missing", {"displayName": "app"}),
        ("snake_case", {"displayName": "app", "root_agent": "root"}),
        ("non_string", {"displayName": "app", "rootAgent": ["root"]}),
        ("unresolvable", {"displayName": "app", "rootAgent": "does_not_exist"}),
    ]:
        app_root = tmp_path / case_name
        app_json = _write_json(app_root / "app.json", app_data)
        _make_json_agent(app_root, "root")
        _make_json_agent(app_root, "orphan")
        assert OrphanedAgent().check(app_json, "", _ctx(app_root)) == []


def test_s015_resolves_display_name_and_yaml_app(tmp_path: Path) -> None:
    yaml_app = tmp_path / "yaml_app"
    app_yaml = _write_yaml(
        yaml_app / "app.yaml",
        {"displayName": "YAML App", "rootAgent": "Front Desk"},
    )
    _make_yaml_agent(
        yaml_app,
        "front_desk",
        display_name="Front Desk",
        child_agents=[
            "Billing Helper",
            "projects/p/locations/us/apps/a/agents/tech-id",
        ],
    )
    _make_yaml_agent(yaml_app, "billing_helper", display_name="Billing Helper")
    _make_yaml_agent(
        yaml_app,
        "tech_agent",
        name="projects/p/locations/us/apps/a/agents/tech-id",
    )

    assert OrphanedAgent().check(app_yaml, "", _ctx(yaml_app)) == []

    # Also verify Discovery discovers app.yml and .yml agent configs cleanly
    yml_app = tmp_path / "yml_app"
    app_yml = _write_yaml(
        yml_app / "app.yml",
        {"displayName": "YML App", "rootAgent": "root_agent"},
    )
    _make_yaml_agent(
        yml_app, "root_agent", ext=".yml", child_agents=["child_agent"]
    )
    _make_yaml_agent(yml_app, "child_agent", ext=".yml")

    disc = Discovery(yml_app, yml_app / "evals")
    assert disc.discover_app_config() == app_yml
    assert set(disc.discover_agent_configs().keys()) == {
        "root_agent",
        "child_agent",
    }
    report = LintReport()
    run_rules(
        build_registry(),
        LintConfig(),
        build_context(yml_app, LintConfig(), disc),
        disc,
        report,
        specific_rules={"S014", "S015"},
    )
    assert report.results == []


# ── S016: unused-toolset ────────────────────────────────────────────────


def test_s016_unused_dict_str_orphan_yaml_and_ces_path(tmp_path: Path) -> None:
    app_yaml = _write_yaml(
        tmp_path / "app.yaml", {"displayName": "app", "rootAgent": "root"}
    )
    _make_yaml_agent(
        tmp_path,
        "root",
        child_agents=["child"],
        toolsets=[
            {"toolset": "Dict Toolset", "toolIds": ["op1"]},
            "str_toolset",
        ],
    )
    _make_yaml_agent(
        tmp_path,
        "child",
        toolsets=["projects/p/locations/us/apps/a/toolsets/ces_ts_id"],
    )
    _make_yaml_agent(
        tmp_path,
        "orphan_agent",
        toolsets=[{"toolset": "orphan_only_ts"}],
    )

    _write_yaml(
        tmp_path / "toolsets" / "dict_ts" / "dict_ts.yaml",
        {"displayName": "Dict Toolset"},
    )
    _write_json(
        tmp_path / "toolsets" / "str_toolset" / "str_toolset.json",
        {"displayName": "str_toolset"},
    )
    _write_yaml(
        tmp_path / "toolsets" / "ces_path_ts.yaml",
        {"name": "projects/p/locations/us/apps/a/toolsets/ces_ts_id"},
    )
    _write_yaml(
        tmp_path / "toolsets" / "orphan_only_ts" / "orphan_only_ts.yaml",
        {"displayName": "orphan_only_ts"},
    )
    _write_json(
        tmp_path / "toolsets" / "dead_ts" / "dead_ts.json",
        {"displayName": "dead_ts"},
    )

    results = UnusedToolset().check(app_yaml, "", _ctx(tmp_path))
    assert len(results) == 2
    assert all(
        r.rule_id == "S016" and r.severity == Severity.WARNING for r in results
    )
    by_file = {r.file: r.message for r in results}
    assert (
        "not attached to any agent" in by_file["toolsets/dead_ts/dead_ts.json"]
    )
    assert (
        "only attached to unreachable agent(s): orphan_agent"
        in by_file["toolsets/orphan_only_ts/orphan_only_ts.yaml"]
    )


# ── S017: unused-guardrail ──────────────────────────────────────────────


def test_s017_unused_app_agent_orphan_ces_path_and_yaml(tmp_path: Path) -> None:
    app_yaml = _write_yaml(
        tmp_path / "app.yaml",
        {
            "displayName": "app",
            "rootAgent": "root",
            "guardrails": [
                "App Safety",
                "projects/p/locations/us/apps/a/guardrails/ces_gr_id",
            ],
        },
    )
    _make_yaml_agent(tmp_path, "root", guardrails=["agent_gr"])
    _make_yaml_agent(tmp_path, "orphan_agent", guardrails=["orphan_gr"])

    _write_yaml(
        tmp_path / "guardrails" / "app_safety" / "app_safety.yaml",
        {"displayName": "App Safety"},
    )
    _write_json(
        tmp_path / "guardrails" / "ces_gr.json",
        {"name": "projects/p/locations/us/apps/a/guardrails/ces_gr_id"},
    )
    _write_yaml(
        tmp_path / "guardrails" / "agent_gr" / "agent_gr.yaml",
        {"displayName": "agent_gr"},
    )
    _write_yaml(
        tmp_path / "guardrails" / "orphan_gr" / "orphan_gr.yaml",
        {"displayName": "orphan_gr"},
    )
    _write_json(
        tmp_path / "guardrails" / "dead_gr" / "dead_gr.json",
        {"displayName": "dead_gr"},
    )

    results = UnusedGuardrail().check(app_yaml, "", _ctx(tmp_path))
    assert len(results) == 2
    assert all(
        r.rule_id == "S017" and r.severity == Severity.WARNING for r in results
    )
    by_file = {r.file: r.message for r in results}
    assert (
        "not attached to the app or any agent"
        in by_file["guardrails/dead_gr/dead_gr.json"]
    )
    assert (
        "only attached to unreachable agent(s): orphan_agent"
        in by_file["guardrails/orphan_gr/orphan_gr.yaml"]
    )


# ── T008: YAML agents, unreachable-only tools, and Sample_Support_Agent ─


def test_t008_unreachable_only_tool_and_yaml_agent_support(
    tmp_path: Path,
) -> None:
    _write_yaml(
        tmp_path / "app.yaml",
        {
            "displayName": "app",
            "rootAgent": "root_agent",
            "guardrails": ["attached_gr"],
        },
    )
    _make_yaml_agent(tmp_path, "root_agent", tools=["active_tool"])
    _make_yaml_agent(tmp_path, "orphan_agent", tools=["orphan_tool"])

    # Callback on orphan_agent referencing orphan_cb_tool should NOT rescue it
    orphan_cb = (
        tmp_path
        / "agents"
        / "orphan_agent"
        / "before_model_callbacks"
        / "cb_01"
        / "python_code.py"
    )
    orphan_cb.parent.mkdir(parents=True)
    orphan_cb.write_text(
        "def before_model_callback(cb, req):\n    tools.orphan_cb_tool({})\n",
        encoding="utf-8",
    )

    # Attached guardrail calling gr_tool (should keep gr_tool clean)
    _write_yaml(
        tmp_path / "guardrails" / "attached_gr" / "attached_gr.yaml",
        {"action": {"toolCall": {"tool": "gr_tool"}}},
    )
    # Unattached guardrail calling dead_gr_tool (should NOT rescue dead_gr_tool)
    _write_yaml(
        tmp_path / "guardrails" / "dead_gr" / "dead_gr.yaml",
        {"action": {"toolCall": {"tool": "dead_gr_tool"}}},
    )

    active_py = _make_py_tool(tmp_path, "active_tool", ext=".yaml")
    orphan_py = _make_py_tool(tmp_path, "orphan_tool")
    orphan_cb_py = _make_py_tool(tmp_path, "orphan_cb_tool")
    gr_py = _make_py_tool(tmp_path, "gr_tool")
    dead_gr_py = _make_py_tool(tmp_path, "dead_gr_tool")

    ctx = _ctx(tmp_path)
    rule = ToolDisplayNameUnreferenced()

    assert rule.check(active_py, active_py.read_text(), ctx) == []
    assert rule.check(gr_py, gr_py.read_text(), ctx) == []

    orphan_res = rule.check(orphan_py, orphan_py.read_text(), ctx)
    assert len(orphan_res) == 1
    assert orphan_res[0].rule_id == "T008"
    assert (
        "only attached to unreachable agent(s): orphan_agent"
        in orphan_res[0].message
    )

    orphan_cb_res = rule.check(orphan_cb_py, orphan_cb_py.read_text(), ctx)
    assert len(orphan_cb_res) == 1
    assert orphan_cb_res[0].rule_id == "T008"

    dead_gr_res = rule.check(dead_gr_py, dead_gr_py.read_text(), ctx)
    assert len(dead_gr_res) == 1
    assert dead_gr_res[0].rule_id == "T008"


def test_sample_support_agent_zero_errors_and_zero_warnings() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    template_root = (
        repo_root
        / ".agents"
        / "skills"
        / "cxas-agent-foundry"
        / "assets"
        / "project-template"
    )
    cfg = LintConfig()
    discovery = Discovery(template_root / "cxas_app", template_root / "evals")
    ctx = build_context(template_root, cfg, discovery)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, discovery, report)
    assert report.errors == []
    assert report.warnings == []


# ── S007 displayName/YAML hardening & corrupt/symlink resilience ────────


def test_s007_detects_multi_parent_via_display_name_and_yaml(
    tmp_path: Path,
) -> None:
    _write_yaml(
        tmp_path / "app.yaml", {"displayName": "app", "rootAgent": "root"}
    )
    _make_yaml_agent(tmp_path, "root", child_agents=["parent_a", "parent_b"])
    _make_yaml_agent(tmp_path, "parent_a", child_agents=["Shared Billing"])
    _make_yaml_agent(tmp_path, "parent_b", child_agents=["shared_billing"])
    shared_cfg = _make_yaml_agent(
        tmp_path, "shared_billing", display_name="Shared Billing"
    )

    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, LintConfig(), disc)
    assert ctx.agent_to_parents["shared_billing"] == {"parent_a", "parent_b"}

    res = SubAgentSingleParent().check(
        shared_cfg, shared_cfg.read_text("utf-8"), ctx
    )
    assert len(res) == 1
    assert res[0].rule_id == "S007"


def test_graph_and_rules_ignore_symlinks_hidden_and_corrupt_files(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_json(outside / "escape.json", {"displayName": "escape"})

    app_json = _write_json(
        app_root / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _make_json_agent(app_root, "root")
    (app_root / "agents" / ".hidden").mkdir(parents=True)
    (app_root / "agents" / "__pycache__").mkdir(parents=True)
    bad_dir = app_root / "agents" / "corrupt_agent"
    bad_dir.mkdir(parents=True)
    (bad_dir / "corrupt_agent.json").write_bytes(b"\xff\xfe\x00not-utf8")
    os.symlink(outside, app_root / "agents" / "sym_agent")

    (app_root / "toolsets").mkdir()
    os.symlink(outside, app_root / "toolsets" / "sym_ts")
    (app_root / "guardrails").mkdir()
    os.symlink(outside, app_root / "guardrails" / "sym_gr")

    graph = resolve_agent_graph(app_root)
    assert set(graph.agent_dirs.keys()) == {"root", "corrupt_agent"}
    assert graph.root_agent_dir == "root"
    assert graph.cycles == []

    ctx = _ctx(app_root)
    assert AgentTreeCycle().check(app_json, "", ctx) == []
    orphans = OrphanedAgent().check(app_json, "", ctx)
    assert len(orphans) == 1
    assert "corrupt_agent" in orphans[0].message
    assert UnusedToolset().check(app_json, "", ctx) == []
    assert UnusedGuardrail().check(app_json, "", ctx) == []


def test_s014_dense_k6_figure8_snake_case_and_malformed_children(
    tmp_path: Path,
) -> None:
    k6 = {f"n{i}": [f"n{j}" for j in range(6) if j != i] for i in range(6)}
    t0 = time.perf_counter()
    cycles = _find_cycles(k6, root_agent_dir="n0")
    assert 1 <= len(cycles) <= _MAX_AGENT_CYCLES
    assert len({tuple(c) for c in cycles}) == len(cycles)
    assert time.perf_counter() - t0 < 1.0

    # Overlapping figure-8 cycles + snake_case child_agents + duplicate refs
    fig8 = tmp_path / "fig8"
    fig8_app = _write_json(
        fig8 / "app.json", {"displayName": "fig8", "rootAgent": "ROOT AGENT"}
    )
    _write_json(
        fig8 / "agents" / "root_agent" / "root_agent.json",
        {
            "displayName": "Root Agent",
            "child_agents": ["node_a", "NODE A"],
        },
    )
    _make_json_agent(fig8, "node_a", child_agents=["node_b", "node_c"])
    _make_json_agent(fig8, "node_b", child_agents=["root_agent"])
    _make_json_agent(fig8, "node_c", child_agents=["node_a"])
    graph = resolve_agent_graph(fig8)
    assert graph.cycles == [
        ["node_a", "node_c", "node_a"],
        ["root_agent", "node_a", "node_b", "root_agent"],
    ]
    assert len(AgentTreeCycle().check(fig8_app, "", _ctx(fig8))) == 2

    # Malformed childAgents entries must not crash or false-fire
    mal = tmp_path / "mal"
    mal_app = _write_json(
        mal / "app.json", {"displayName": "mal", "rootAgent": "root"}
    )
    _write_json(
        mal / "agents" / "root" / "root.json",
        {
            "displayName": "root",
            "childAgents": [None, 123, "", " ", {"bad": True}, "leaf", "leaf"],
        },
    )
    _write_json(
        mal / "agents" / "leaf" / "leaf.json",
        {"displayName": "leaf", "childAgents": "not-a-list"},
    )
    assert AgentTreeCycle().check(mal_app, "", _ctx(mal)) == []
    assert OrphanedAgent().check(mal_app, "", _ctx(mal)) == []


def test_s015_depth60_configless_dir_and_nested_layout(tmp_path: Path) -> None:
    nested = tmp_path / "cxas_app"
    app_json = _write_json(
        nested / "app.json", {"displayName": "chain", "rootAgent": "a00"}
    )
    for i in range(60):
        _make_json_agent(
            nested,
            f"a{i:02d}",
            child_agents=[f"a{i + 1:02d}"] if i < 59 else [],
        )
    (nested / "agents" / "bare_orphan").mkdir(parents=True)

    ctx = _ctx(nested, project_root=tmp_path)
    assert AgentTreeCycle().check(app_json, "", ctx) == []
    orphans = OrphanedAgent().check(app_json, "", ctx)
    assert len(orphans) == 1
    assert orphans[0].file == "cxas_app/agents/bare_orphan"


def test_s016_s017_broken_root_app_plus_orphan_and_ref_formats(
    tmp_path: Path,
) -> None:
    # Broken rootAgent skips unreachable-only escalation but flags unattached
    broken = tmp_path / "broken"
    b_app = _write_json(
        broken / "app.json",
        {"displayName": "b", "rootAgent": "nonexistent"},
    )
    _make_json_agent(
        broken, "agent_a", toolsets=["ts_attached"], guardrails=["gr_attached"]
    )
    _write_json(broken / "toolsets" / "ts_attached.json", {})
    _write_json(broken / "toolsets" / "ts_dead.json", {})
    _write_json(broken / "guardrails" / "gr_attached.json", {})
    _write_json(broken / "guardrails" / "gr_dead.json", {})
    assert [r.file for r in UnusedToolset().check(b_app, "", _ctx(broken))] == [
        "toolsets/ts_dead.json"
    ]
    assert [
        r.file for r in UnusedGuardrail().check(b_app, "", _ctx(broken))
    ] == ["guardrails/gr_dead.json"]

    # App+orphan shared guardrail, dict/relative/flat .yml refs, malformed items
    fmt = tmp_path / "fmt"
    f_app = _write_yaml(
        fmt / "app.yaml",
        {
            "displayName": "fmt",
            "rootAgent": "root",
            "guardrails": [
                {"guardrail": "shared_gr"},
                "guardrails/rel_gr",
                None,
                123,
                {},
            ],
        },
    )
    _make_yaml_agent(fmt, "root", guardrails=["flat_yml_gr"])
    _make_yaml_agent(fmt, "orphan", guardrails=["shared_gr"])
    _write_yaml(fmt / "guardrails" / "shared_gr.yml", {"displayName": "s"})
    _write_yaml(fmt / "guardrails" / "rel_gr.yml", {"displayName": "r"})
    _write_yaml(fmt / "guardrails" / "flat_yml_gr.yml", {"displayName": "f"})
    assert UnusedGuardrail().check(f_app, "", _ctx(fmt)) == []


def test_t008_reachable_caller_non_utf8_and_ces_name_orphan_isolation(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _make_json_agent(tmp_path, "root", tools=["live_caller"])
    _make_json_agent(
        tmp_path,
        "orphan",
        tools=["projects/p/locations/us/apps/a/tools/custom-id-99"],
    )
    # Reachable tool calls live_helper (should rescue live_helper)
    _make_py_tool(
        tmp_path,
        "live_caller",
        py_code="def live_caller() -> dict:\n    return tools.live_helper()\n",
    )
    live_helper_py = _make_py_tool(tmp_path, "live_helper")
    # Non-UTF-8 .py file in reachable agent must not crash T008
    bad_cb = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks"
        / "bad"
        / "python_code.py"
    )
    bad_cb.parent.mkdir(parents=True)
    bad_cb.write_bytes(b"\xff\xfe\x00not-utf8")

    # Orphan-only tool attached via CES resource name must NOT rescue dead_helper
    orphan_dir = tmp_path / "tools" / "orphan_caller"
    (orphan_dir / "python_function").mkdir(parents=True)
    (orphan_dir / "python_function" / "python_code.py").write_text(
        "def orphan_caller() -> dict:\n    return tools.dead_helper()\n",
        encoding="utf-8",
    )
    _write_json(
        orphan_dir / "orphan_caller.json",
        {
            "name": "projects/p/locations/us/apps/a/tools/custom-id-99",
            "displayName": "Orphan Caller",
            "pythonFunction": {
                "pythonCode": "tools/orphan_caller/python_function/python_code.py"
            },
        },
    )
    dead_py = _make_py_tool(tmp_path, "dead_helper")

    rule = ToolDisplayNameUnreferenced()
    ctx = _ctx(tmp_path)
    assert rule.check(live_helper_py, live_helper_py.read_text(), ctx) == []
    dead_res = rule.check(dead_py, dead_py.read_text(), ctx)
    assert [r.rule_id for r in dead_res] == ["T008"]


def test_s007_same_parent_duplicate_ignored_and_yml_snake_case_caught(
    tmp_path: Path,
) -> None:
    _write_yaml(
        tmp_path / "app.yml", {"displayName": "app", "rootAgent": "root"}
    )
    _write_yaml(
        tmp_path / "agents" / "root" / "root.yml",
        {"displayName": "root", "child_agents": ["solo", "solo", "p1", "p2"]},
    )
    solo_cfg = _make_yaml_agent(tmp_path, "solo", ext=".yml")
    _write_yaml(
        tmp_path / "agents" / "p1" / "p1.yml",
        {"displayName": "p1", "child_agents": ["shared"]},
    )
    _write_yaml(
        tmp_path / "agents" / "p2" / "p2.yml",
        {"displayName": "p2", "child_agents": ["shared"]},
    )
    shared_cfg = _make_yaml_agent(tmp_path, "shared", ext=".yml")

    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, LintConfig(), disc)
    assert (
        SubAgentSingleParent().check(solo_cfg, solo_cfg.read_text(), ctx) == []
    )
    assert [
        r.rule_id
        for r in SubAgentSingleParent().check(
            shared_cfg, shared_cfg.read_text(), ctx
        )
    ] == ["S007"]


def test_yaml_app_with_guided_agent_passes_full_lint_without_false_errors(
    tmp_path: Path,
) -> None:
    _write_yaml(
        tmp_path / "app.yaml",
        {
            "name": "yaml_app",
            "displayName": "YAML App",
            "rootAgent": "root_agent",
        },
    )
    _make_yaml_agent(
        tmp_path,
        "root_agent",
        child_agents=["guided_sub"],
        tools=["end_session"],
    )
    _write_yaml(
        tmp_path / "agents" / "guided_sub" / "guided_sub.yaml",
        {
            "displayName": "Guided Sub",
            "guidedAgent": {"config": "some_config"},
        },
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, LintConfig(), disc)
    report = LintReport()
    run_rules(build_registry(), LintConfig(), ctx, disc, report)
    assert report.errors == []


def test_discovery_prefers_dir_config_over_sidecar_and_skips_symlinks(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    (outside / "before_model_callbacks" / "cb1").mkdir(parents=True)
    (outside / "before_model_callbacks" / "cb1" / "python_code.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    (outside / "secret.json").write_text('{"displayName": "s"}')

    app = tmp_path / "app"
    _write_json(app / "app.json", {"displayName": "app", "rootAgent": "root"})
    _make_json_agent(app, "root", tools=[])

    # Non-Python JSON & YAML tools with alphabetically earlier sidecars
    _write_json(
        app / "tools" / "zeta_tool" / "zeta_tool.json",
        {"displayName": "Zeta Tool", "openApiTool": {}},
    )
    _write_json(app / "tools" / "zeta_tool" / "aaa_schema.json", {"type": "x"})
    _write_yaml(
        app / "tools" / "yaml_tool" / "yaml_tool.yaml",
        {"displayName": "YAML Tool", "openApiTool": {}},
    )
    _write_json(app / "tools" / "yaml_tool" / "schema.json", {"type": "x"})
    _write_json(app / "tools" / ".hidden" / ".hidden.json", {})
    os.symlink(outside, app / "tools" / "sym_tool")
    os.symlink(outside, app / "agents" / "sym_agent")

    disc = Discovery(app, app / "evals")
    tools = disc.discover_tools()
    assert tools == {
        "zeta_tool": app / "tools" / "zeta_tool" / "zeta_tool.json",
        "yaml_tool": app / "tools" / "yaml_tool" / "yaml_tool.yaml",
    }
    assert all(agent != "sym_agent" for agent, *_ in disc.discover_callbacks())
