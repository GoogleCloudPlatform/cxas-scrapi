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

"""Rung 2: Rung 1 is the only build path; firewall checks live in lint.

Covers: blueprint input removed; polymorphic lint rules P001-P005; env
manifest model pinning + governed lock; push `--lint` gate; drift without
a blueprint.
"""

import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from cxas_scrapi.build import build_app, check_drift, write_lockfile
from cxas_scrapi.cli import main as cli_main
from cxas_scrapi.utils.linter import collect_lint_report

TARGET_APP_NAME = "projects/test-proj/locations/us/apps/cymbal-web"
GA_AGENT_MODEL = "gemini-3.5-flash"
GA_LITE_MODEL = "gemini-3.5-flash-lite"


def _w(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _r(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_rung1_source(root: Path, *, models: bool = True) -> tuple[Path, Path]:
    core = root / "core"
    _w(
        core / "app.json",
        {
            "name": "@@brand.brand_id@@-assistant",
            "displayName": "@@brand.display_name@@ Assistant",
            "rootAgent": "root_agent",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "guardrails": ["scope_policy"],
        },
    )
    _w(
        core / "global_instruction.txt",
        "You represent @@brand.display_name@@ only.\n"
        "Disclosure: @@brand.lexicon.ai_disclosure@@\n",
    )
    _w(
        core / "agents/root_agent/root_agent.json",
        {
            "displayName": "root_agent",
            "instruction": "agents/root_agent/instruction.txt",
            "modelSettings": {"model": "gemini-2.5-flash-001"},
            "childAgents": ["branch_agent", "guide_agent"],
            "tools": ["end_session"],
        },
    )
    _w(
        core / "agents/root_agent/instruction.txt",
        "{@startAgentEnabled_branch_agent}Route branch questions to "
        "{@AGENT: branch_agent}.{@endAgentEnabled_branch_agent}\n"
        "Route everything else to {@AGENT: guide_agent}.\n",
    )
    _w(
        core / "agents/branch_agent/branch_agent.json",
        {
            "displayName": "branch_agent",
            "instruction": "agents/branch_agent/instruction.txt",
            "tools": ["find_branches"],
        },
    )
    _w(
        core / "agents/branch_agent/instruction.txt",
        "Use {@TOOL: find_branches} for @@brand.display_name@@ branches.\n",
    )
    _w(
        core / "agents/guide_agent/guide_agent.json",
        {
            "displayName": "guide_agent",
            "instruction": "agents/guide_agent/instruction.txt",
            "tools": ["kb_search"],
        },
    )
    _w(
        core / "agents/guide_agent/instruction.txt",
        "Answer product questions with {@TOOL: kb_search}.\n",
    )
    _w(
        core / "tools/find_branches/find_branches.json",
        {
            "displayName": "find_branches",
            "openApiTool": {
                "name": "find_branches",
                "description": "Find @@brand.display_name@@ branches.",
                "openApiSchema": "tools/find_branches/open_api_tool/"
                "open_api_schema.yaml",
            },
        },
    )
    _w(
        core / "tools/find_branches/open_api_tool/open_api_schema.yaml",
        "openapi: 3.0.0\ninfo: {title: branches, version: '1'}\n"
        "servers:\n- url: https://bfa.example.internal\npaths: {}\n",
    )
    _w(
        core / "tools/kb_search/kb_search.json",
        {
            "displayName": "kb_search",
            "dataStoreTool": {
                "name": "kb_search",
                "description": "Search the knowledge base.",
                "modalityConfigs": [
                    {
                        "modalityType": "TEXT",
                        "rewriterConfig": {
                            "modelSettings": {"model": "gemini-2.5-flash-001"}
                        },
                        "summarizationConfig": {
                            "modelSettings": {"model": "gemini-2.5-flash-001"}
                        },
                    }
                ],
            },
        },
    )
    _w(
        core / "guardrails/scope_policy/scope_policy.json",
        {
            "displayName": "scope_policy",
            "enabled": True,
            "llmPolicy": {
                "prompt": "Allow only @@brand.display_name@@ banking topics.",
                "modelSettings": {"model": "gemini-2.5-flash-001"},
            },
        },
    )

    man = root / "manifests"
    _w(
        man / "brands/cymbal_retail/identity.yaml",
        "brand_id: cymbal_retail\n"
        "display_name: Cymbal Retail\n"
        "lexicon:\n"
        "  ai_disclosure: Hello from Cymbal Retail's AI assistant.\n"
        "forbidden_tokens:\n"
        "  - DirectTAN\n",
    )
    _w(
        man / "brands/cymbal_retail/channel_policy.yaml",
        "brand_id: cymbal_retail\n"
        "channels:\n"
        "  WEB:\n"
        "    supported_modalities: ['TEXT']\n",
    )
    _w(
        man / "brands/cymbal_direct/identity.yaml",
        "brand_id: cymbal_direct\n"
        "display_name: Cymbal Direct\n"
        "lexicon:\n"
        "  ai_disclosure: Hi from Cymbal Direct's AI assistant.\n",
    )
    _w(
        man / "brands/cymbal_direct/channel_policy.yaml",
        "brand_id: cymbal_direct\n"
        "channels:\n"
        "  WEB:\n"
        "    disabled_agents: [branch_agent]\n"
        "    disabled_tools: [find_branches]\n"
        "    supported_modalities: ['TEXT']\n",
    )
    env = "environment: dev\nlocked: true\n"
    if models:
        env += (
            "models:\n"
            f"  agent: {GA_AGENT_MODEL}\n"
            f"  guardrail: {GA_LITE_MODEL}\n"
            f"  data_store: {GA_LITE_MODEL}\n"
        )
    _w(man / "envs/dev.yaml", env)
    return core, man


def _build(tmp_path: Path, brand: str = "cymbal_retail", **kw: Any) -> Path:
    core, man = _make_rung1_source(tmp_path / "src", **kw)
    out = tmp_path / "dist" / brand / "web"
    build_app(core, man, brand, "WEB", "dev", out)
    return out


def _rule_ids(report: Any, severity: str = "error") -> list[str]:
    items = report.errors if severity == "error" else report.warnings
    return sorted(r.rule_id for r in items)


# -------------------------------------------------------------------------
# 1. Blueprint input format removed
# -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    [
        "cxas_scrapi.build.blueprint_compiler",
        "cxas_scrapi.build.blueprint_schemas",
        "cxas_scrapi.build.firewall",
    ],
)
def test_blueprint_and_firewall_modules_are_gone(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


@pytest.mark.parametrize(
    "argv",
    [
        ["build", "--blueprint", "bp", "--out", "o", "--env", "dev"],
        ["firewall", "--bundle", "b"],
        ["push", "--app-dir", "a", "--firewall"],
        [
            "drift",
            "--app",
            "a",
            "--dist",
            "d",
            "--live",
            "l",
            "--blueprint",
            "b",
        ],
    ],
)
def test_blueprint_and_firewall_cli_flags_are_rejected(
    argv: list[str],
) -> None:
    with (
        mock.patch.object(sys, "argv", ["cxas", *argv]),
        pytest.raises(SystemExit) as exc,
    ):
        cli_main.main()
    assert exc.value.code == 2


# -------------------------------------------------------------------------
# 3/4. Env manifest: governed lock + model pinning on the Rung 1 path
# -------------------------------------------------------------------------


def test_env_manifest_pins_models_and_declares_lock(tmp_path: Path) -> None:
    out = _build(tmp_path)
    app = _r(out / "app.json")
    assert app["locked"] is True
    assert app["modelSettings"]["model"] == GA_AGENT_MODEL
    for agent in ("root_agent", "branch_agent", "guide_agent"):
        a = _r(out / "agents" / agent / f"{agent}.json")
        assert a["modelSettings"]["model"] == GA_AGENT_MODEL
    gr = _r(out / "guardrails/scope_policy/scope_policy.json")
    assert gr["llmPolicy"]["modelSettings"]["model"] == GA_LITE_MODEL
    ds = _r(out / "tools/kb_search/kb_search.json")["dataStoreTool"]
    for cfg in ds["modalityConfigs"]:
        assert cfg["rewriterConfig"]["modelSettings"]["model"] == GA_LITE_MODEL
        assert (
            cfg["summarizationConfig"]["modelSettings"]["model"]
            == GA_LITE_MODEL
        )
    dep = (out / "deployments" / "web.yaml").read_text(encoding="utf-8")
    assert "brandId: cymbal_retail" in dep
    assert "channel: WEB" in dep


# -------------------------------------------------------------------------
# 2. Polymorphic lint rules (P001-P005)
# -------------------------------------------------------------------------


def test_clean_compiled_bundle_has_no_polymorphic_errors(
    tmp_path: Path,
) -> None:
    out = _build(tmp_path)
    report = collect_lint_report(out, manifests_dir=tmp_path / "src/manifests")
    assert [i for i in _rule_ids(report) if i.startswith("P")] == []


def test_p001_flags_sister_brand_and_forbidden_tokens(tmp_path: Path) -> None:
    out = _build(tmp_path)
    gi = out / "global_instruction.txt"
    gi.write_text(
        gi.read_text(encoding="utf-8")
        + "Also try cymbal direct and DirectTAN.\n",
        encoding="utf-8",
    )
    report = collect_lint_report(out, manifests_dir=tmp_path / "src/manifests")
    p001 = [r for r in report.errors if r.rule_id == "P001"]
    msgs = " ".join(r.message for r in p001)
    assert "Cymbal Direct" in msgs
    assert "DirectTAN" in msgs
    assert {r.rule_id for r in report.errors if r.rule_id.startswith("P")} == {
        "P001"
    }


def test_p001_needs_brand_context_and_is_silent_without_it(
    tmp_path: Path,
) -> None:
    out = _build(tmp_path)
    (out / "global_instruction.txt").write_text(
        "Cymbal Direct\n", encoding="utf-8"
    )
    report = collect_lint_report(out)  # no manifests -> rule cannot run
    assert "P001" not in _rule_ids(report)


def test_p002_tool_count_cap_is_configurable(tmp_path: Path) -> None:
    out = _build(tmp_path)
    root = out / "agents/root_agent/root_agent.json"
    data = _r(root)
    data["tools"] = ["end_session", "customize_response"]
    _w(root, data)
    report = collect_lint_report(
        out,
        manifests_dir=tmp_path / "src/manifests",
        rule_options={"P002": {"max_tools": 1}},
    )
    assert "P002" in _rule_ids(report, "warning")


def test_p003_flags_unresolved_build_tags(tmp_path: Path) -> None:
    out = _build(tmp_path)
    instr = out / "agents/guide_agent/instruction.txt"
    instr.write_text(
        "Hi @@brand.legal_entity@@\n{@startChannel_WEB}\nx\n{@endChannel_WEB}\n",
        encoding="utf-8",
    )
    report = collect_lint_report(out, manifests_dir=tmp_path / "src/manifests")
    p003 = [r for r in report.errors if r.rule_id == "P003"]
    assert len(p003) >= 2


def test_p004_pruned_agents_and_tools_must_be_absent(tmp_path: Path) -> None:
    out = _build(tmp_path, brand="cymbal_direct")
    assert not (out / "agents/branch_agent").exists()
    assert not (out / "tools/find_branches").exists()
    clean = collect_lint_report(out, manifests_dir=tmp_path / "src/manifests")
    assert "P004" not in _rule_ids(clean)

    shutil.copytree(
        tmp_path / "src/core/tools/find_branches", out / "tools/find_branches"
    )
    instr = out / "agents/root_agent/instruction.txt"
    instr.write_text("Use {@AGENT: branch_agent}.\n", encoding="utf-8")
    report = collect_lint_report(out, manifests_dir=tmp_path / "src/manifests")
    p004 = [r for r in report.errors if r.rule_id == "P004"]
    joined = " ".join(r.message for r in p004)
    assert "find_branches" in joined
    assert "branch_agent" in joined


def test_p005_rejects_unpinned_aliases_and_25_models(tmp_path: Path) -> None:
    out = _build(tmp_path, models=False)
    report = collect_lint_report(out, manifests_dir=tmp_path / "src/manifests")
    p005 = [r for r in report.errors if r.rule_id == "P005"]
    assert p005, "2.5 models from core must be rejected"
    assert any("gemini-2.5" in r.message for r in p005)

    out_ok = _build(tmp_path / "pinned")
    gr = out_ok / "guardrails/scope_policy/scope_policy.json"
    data = _r(gr)
    data["llmPolicy"]["modelSettings"]["model"] = "latest"
    _w(gr, data)
    report_alias = collect_lint_report(
        out_ok, manifests_dir=tmp_path / "pinned/src/manifests"
    )
    p005_alias = [r for r in report_alias.errors if r.rule_id == "P005"]
    assert len(p005_alias) == 1
    assert "latest" in p005_alias[0].message


def test_system_tools_need_no_tool_dir_in_lint(tmp_path: Path) -> None:
    out = _build(tmp_path)
    report = collect_lint_report(out, manifests_dir=tmp_path / "src/manifests")
    assert not [
        r
        for r in report.errors
        if "end_session" in r.message or "customize_response" in r.message
    ]


# -------------------------------------------------------------------------
# Push: lint gate + governed lock + drift (no blueprint anywhere)
# -------------------------------------------------------------------------


@pytest.fixture
def mock_apps_client() -> Any:
    with mock.patch("cxas_scrapi.cli.app.Apps", autospec=True) as cls:
        inst = cls.return_value
        app = mock.MagicMock()
        app.name = TARGET_APP_NAME
        app.locked = False
        inst.get_app_by_display_name.return_value = app
        inst.get_app.return_value = app
        resp = mock.MagicMock()
        resp.name = TARGET_APP_NAME
        resp.warnings = []
        lro = mock.MagicMock()
        lro.result.return_value = resp
        inst.import_app.return_value = lro
        inst.import_as_new_app.return_value = lro
        yield inst


def _push(argv: list[str]) -> None:
    with mock.patch.object(sys, "argv", ["cxas", "push", *argv]):
        cli_main.main()


def test_push_lint_gate_refuses_brand_leak_before_import(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    out = _build(tmp_path)
    gi = out / "global_instruction.txt"
    gi.write_text(gi.read_text(encoding="utf-8") + "Cymbal Direct\n", "utf-8")
    with pytest.raises(SystemExit) as exc:
        _push(
            [
                "--app-dir",
                str(out),
                "--to",
                TARGET_APP_NAME,
                "--overwrite",
                "--lint",
                "--manifests-dir",
                str(tmp_path / "src/manifests"),
            ]
        )
    assert exc.value.code == 1
    mock_apps_client.import_app.assert_not_called()


def test_push_lint_and_drift_gate_then_lock(
    tmp_path: Path, mock_apps_client: Any
) -> None:
    out = _build(tmp_path)
    lock = write_lockfile(out, tmp_path / "base.lock.json")
    live = tmp_path / "live"
    shutil.copytree(out, live)
    shutil.rmtree(live / "deployments")
    app = _r(live / "app.json")
    app.pop("locked")  # CES never exports `locked`
    app["displayName"] = "renamed-at-push"
    _w(live / "app.json", app)

    _push(
        [
            "--app-dir",
            str(out),
            "--to",
            TARGET_APP_NAME,
            "--overwrite",
            "--lint",
            "--manifests-dir",
            str(tmp_path / "src/manifests"),
            "--check-drift",
            "--live-dir",
            str(live),
            "--lockfile",
            str(lock),
        ]
    )
    mock_apps_client.import_app.assert_called_once()
    mock_apps_client.update_app.assert_called_once_with(
        TARGET_APP_NAME, locked=True
    )


def test_drift_cli_runs_without_blueprint(tmp_path: Path) -> None:
    out = _build(tmp_path)
    lock = write_lockfile(out, tmp_path / "base.lock.json")
    report = check_drift("app", out, out, lockfile_path=lock)
    assert report.is_clean


def test_build_renders_brand_tokens_in_openapi_yaml(tmp_path: Path) -> None:
    core, man = _make_rung1_source(tmp_path / "src")
    schema = core / "tools/find_branches/open_api_tool/open_api_schema.yaml"
    schema.write_text(
        "openapi: 3.0.0\n"
        "info: {title: '@@brand.display_name@@ branches', version: '1'}\n"
        "servers:\n- url: https://bfa.@@brand.brand_id@@.example.internal\n"
        "paths: {}\n",
        encoding="utf-8",
    )
    out = tmp_path / "dist"
    build_app(core, man, "cymbal_retail", "WEB", "dev", out)
    text = (
        out / "tools/find_branches/open_api_tool/open_api_schema.yaml"
    ).read_text(encoding="utf-8")
    assert "Cymbal Retail branches" in text
    assert "https://bfa.cymbal_retail.example.internal" in text
    assert "@@" not in text
