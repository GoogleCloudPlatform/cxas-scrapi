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

"""Tests for PR 4: polymorphic lint rules (P001-P005), model pins, and lock."""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import yaml

from cxas_scrapi.build.builder import build_app
from cxas_scrapi.build.manifests import ManifestError, load_manifests
from cxas_scrapi.cli import app as cli_app
from cxas_scrapi.cli.main import get_parser
from cxas_scrapi.utils.linter import (
    CES_PLATFORM_SYSTEM_TOOLS,
    collect_lint_report,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_poly_workspace(
    tmp_path: Path,
    *,
    models: dict[str, str] | None = None,
    locked: bool | None = None,
) -> tuple[Path, Path]:
    core_dir = tmp_path / "core"
    (core_dir / "agents" / "root_agent").mkdir(parents=True)
    (core_dir / "agents" / "web_only_agent").mkdir(parents=True)
    (core_dir / "tools" / "active_tool").mkdir(parents=True)
    (core_dir / "tools" / "disabled_tool").mkdir(parents=True)
    (core_dir / "tools" / "kb_search").mkdir(parents=True)
    (core_dir / "guardrails" / "brand_guard").mkdir(parents=True)
    (core_dir / "guardrails" / "sec_guard").mkdir(parents=True)

    (core_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "@@profile.display_name@@",
                "rootAgent": "root_agent",
                "globalInstruction": "global_instruction.txt",
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "global_instruction.txt").write_text(
        "Welcome to @@profile.display_name@@.\n", encoding="utf-8"
    )
    (core_dir / "agents" / "root_agent" / "root_agent.json").write_text(
        json.dumps(
            {
                "displayName": "root_agent",
                "childAgents": ["web_only_agent"],
                "tools": [
                    "active_tool",
                    "disabled_tool",
                    "kb_search",
                    "end_session",
                    "customize_response",
                ],
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "Use {@TOOL: active_tool}, {@TOOL: kb_search}, and "
        "{@TOOL: end_session}.\n"
        "{@startToolEnabled_disabled_tool}\n"
        "Also call {@TOOL: disabled_tool}.\n"
        "{@endToolEnabled_disabled_tool}\n",
        encoding="utf-8",
    )
    (core_dir / "agents" / "web_only_agent" / "web_only_agent.json").write_text(
        json.dumps({"displayName": "web_only_agent"}), encoding="utf-8"
    )
    (core_dir / "agents" / "web_only_agent" / "instruction.txt").write_text(
        "Web helper.\n", encoding="utf-8"
    )
    (core_dir / "tools" / "active_tool" / "active_tool.json").write_text(
        json.dumps({"displayName": "active_tool"}), encoding="utf-8"
    )
    (core_dir / "tools" / "disabled_tool" / "disabled_tool.json").write_text(
        json.dumps({"displayName": "disabled_tool"}), encoding="utf-8"
    )
    (core_dir / "tools" / "kb_search" / "kb_search.json").write_text(
        json.dumps(
            {
                "displayName": "kb_search",
                "dataStoreTool": {
                    "modalityConfigs": [
                        {
                            "modalityType": "TEXT",
                            "rewriterConfig": {"disabled": False},
                            "summarizationConfig": {"disabled": False},
                        },
                        {
                            "modalityType": "AUDIO",
                            "rewriterConfig": {"disabled": True},
                            "summarizationConfig": {"disabled": True},
                        },
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "guardrails" / "brand_guard" / "brand_guard.json").write_text(
        json.dumps(
            {
                "displayName": "brand_guard",
                "llmPolicy": {"prompt": "Guard @@profile.display_name@@."},
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "guardrails" / "sec_guard" / "sec_guard.json").write_text(
        json.dumps(
            {
                "displayName": "sec_guard",
                "llmPromptSecurity": {
                    "customPolicy": {"prompt": "Security check."}
                },
            }
        ),
        encoding="utf-8",
    )

    manifests_dir = tmp_path / "manifests"
    b1 = manifests_dir / "profiles" / "brand_1"
    b1.mkdir(parents=True)
    (b1 / "identity.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "display_name": "Alpha Bank",
                "forbidden_tokens": ["LegacyBrand", "SisterSecret"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (b1 / "channel_policy.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "channels": {
                    "VOICE_TELEPHONY": {
                        "disabled_agents": ["web_only_agent"],
                        "disabled_tools": ["disabled_tool"],
                        "supported_modalities": ["AUDIO"],
                    },
                    "WEB": {"supported_modalities": ["TEXT"]},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    b2 = manifests_dir / "profiles" / "brand_2"
    b2.mkdir(parents=True)
    (b2 / "identity.yaml").write_text(
        yaml.safe_dump(
            {"profile_id": "brand_2", "display_name": "Beta Bank"},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (b2 / "channel_policy.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_2",
                "channels": {"WEB": {"supported_modalities": ["TEXT"]}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    envs_dir = manifests_dir / "envs"
    envs_dir.mkdir(parents=True)
    env_doc: dict[str, object] = {"environment": "prod"}
    if models is not None:
        env_doc["models"] = models
    if locked is not None:
        env_doc["locked"] = locked
    (envs_dir / "prod.yaml").write_text(
        yaml.safe_dump(env_doc, sort_keys=False), encoding="utf-8"
    )
    return core_dir, manifests_dir


def test_build_applies_model_pins_and_lock_policy_clean_lint(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _make_poly_workspace(
        tmp_path,
        models={
            "agent": "gemini-3.5-flash",
            "guardrail": "gemini-3.5-flash-lite",
            "data_store": "gemini-3.5-flash-lite",
        },
        locked=True,
    )
    out_dir = tmp_path / "built_b1_voice"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_dir
    )

    app_data = json.loads((out_dir / "app.json").read_text("utf-8"))
    assert app_data["locked"] is True
    assert app_data["modelSettings"]["model"] == "gemini-3.5-flash"

    agent_data = json.loads(
        (out_dir / "agents" / "root_agent" / "root_agent.json").read_text(
            "utf-8"
        )
    )
    assert agent_data["modelSettings"]["model"] == "gemini-3.5-flash"

    gr_data = json.loads(
        (out_dir / "guardrails" / "brand_guard" / "brand_guard.json").read_text(
            "utf-8"
        )
    )
    assert (
        gr_data["llmPolicy"]["modelSettings"]["model"]
        == "gemini-3.5-flash-lite"
    )
    sec_data = json.loads(
        (out_dir / "guardrails" / "sec_guard" / "sec_guard.json").read_text(
            "utf-8"
        )
    )
    assert (
        sec_data["llmPromptSecurity"]["customPolicy"]["modelSettings"]["model"]
        == "gemini-3.5-flash-lite"
    )

    ds_data = json.loads(
        (out_dir / "tools" / "kb_search" / "kb_search.json").read_text("utf-8")
    )
    text_cfg = ds_data["dataStoreTool"]["modalityConfigs"][0]
    audio_cfg = ds_data["dataStoreTool"]["modalityConfigs"][1]
    assert (
        text_cfg["rewriterConfig"]["modelSettings"]["model"]
        == "gemini-3.5-flash-lite"
    )
    assert (
        text_cfg["summarizationConfig"]["modelSettings"]["model"]
        == "gemini-3.5-flash-lite"
    )
    # Disabled data-store configs are left unpinned.
    assert "modelSettings" not in audio_cfg["rewriterConfig"]

    # Clean lint: zero P001-P005 findings and zero S002/A005 false positives on
    # CES platform system tools (`end_session`, `customize_response`).
    report = collect_lint_report(out_dir, manifests_dir=manifests_dir)
    ids = {r.rule_id for r in report.results}
    for rule_id in ("P001", "P002", "P003", "P004", "P005", "S002", "A005"):
        assert rule_id not in ids, (
            f"Unexpected {rule_id} finding: "
            f"{[r for r in report.results if r.rule_id == rule_id]}"
        )


def test_p001_brand_token_leak_sister_and_forbidden(tmp_path: Path) -> None:
    core_dir, manifests_dir = _make_poly_workspace(tmp_path)
    out_dir = tmp_path / "built"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_dir
    )

    instr = out_dir / "agents" / "root_agent" / "instruction.txt"
    instr.write_text(
        instr.read_text("utf-8")
        + "Transfer to Beta Bank or LegacyBrand portal.\n",
        encoding="utf-8",
    )

    report = collect_lint_report(out_dir, manifests_dir=manifests_dir)
    p001 = [r for r in report.results if r.rule_id == "P001"]
    assert len(p001) == 2
    msgs = " ".join(r.message for r in p001)
    assert "Beta Bank" in msgs
    assert "LegacyBrand" in msgs

    # Without manifests context, P001 is skipped cleanly.
    report_no_ctx = collect_lint_report(out_dir)
    assert not [r for r in report_no_ctx.results if r.rule_id == "P001"]


def test_p002_agent_tool_count_cap(tmp_path: Path) -> None:
    core_dir, manifests_dir = _make_poly_workspace(tmp_path)
    out_dir = tmp_path / "built"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)

    report = collect_lint_report(
        out_dir, rule_options={"P002": {"max_tools": 3}}
    )
    p002 = [r for r in report.results if r.rule_id == "P002"]
    assert len(p002) == 1
    assert "5 tools (cap 3)" in p002[0].message


def test_p003_unresolved_build_tags_and_dangling_tokens(tmp_path: Path) -> None:
    core_dir, manifests_dir = _make_poly_workspace(tmp_path)
    out_dir = tmp_path / "built"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)

    instr = out_dir / "agents" / "root_agent" / "instruction.txt"
    instr.write_text(
        "Unresolved @@brand.unknown@@\n"
        "Unclosed @@env.api:users\n"
        "{@startChannel_WEB}stray\n",
        encoding="utf-8",
    )

    report = collect_lint_report(out_dir)
    p003 = [r for r in report.results if r.rule_id == "P003"]
    assert len(p003) == 3
    joined = " ".join(r.message for r in p003)
    assert "@@brand.unknown@@" in joined
    assert "@@env.api:users" in joined
    assert "{@startChannel_WEB}" in joined


def test_p004_pruned_resources_absent_and_system_tools(tmp_path: Path) -> None:
    core_dir, manifests_dir = _make_poly_workspace(tmp_path)
    out_dir = tmp_path / "built"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_dir
    )

    # Re-introduce disabled resources & references to trigger P004.
    (out_dir / "tools" / "disabled_tool").mkdir(parents=True)
    (out_dir / "tools" / "disabled_tool" / "disabled_tool.json").write_text(
        json.dumps({"displayName": "disabled_tool"}), encoding="utf-8"
    )
    agent_path = out_dir / "agents" / "root_agent" / "root_agent.json"
    agent_doc = json.loads(agent_path.read_text("utf-8"))
    agent_doc["tools"].append("disabled_tool")
    agent_doc["childAgents"].append("web_only_agent")
    agent_path.write_text(json.dumps(agent_doc), encoding="utf-8")
    (out_dir / "environment.json").write_text(
        json.dumps({"tools": {"disabled_tool": {"endpoint": "https://x"}}}),
        encoding="utf-8",
    )
    instr = out_dir / "agents" / "root_agent" / "instruction.txt"
    instr.write_text(
        "Call {@TOOL: disabled_tool} and {@AGENT: web_only_agent} and "
        "{@TOOL: end_session}.\n",
        encoding="utf-8",
    )

    assert {"end_session", "customize_response"} == CES_PLATFORM_SYSTEM_TOOLS
    report = collect_lint_report(out_dir, manifests_dir=manifests_dir)
    p004 = [r for r in report.results if r.rule_id == "P004"]
    assert len(p004) == 6
    assert not any("end_session" in r.message for r in p004)


def test_p005_pinned_ga_models_flags_aliases_and_deprecated(
    tmp_path: Path,
) -> None:
    core_dir, manifests_dir = _make_poly_workspace(
        tmp_path,
        models={
            "agent": "default",
            "guardrail": "gemini-2.5-flash",
            "data_store": "gemini-3.5-flash-latest",
        },
    )
    out_dir = tmp_path / "built_bad_models"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)

    report = collect_lint_report(out_dir)
    p005 = [r for r in report.results if r.rule_id == "P005"]
    # app.json + 2 agents + 2 guardrails + 2 data_store subconfigs = 7
    assert len(p005) == 7
    msgs = " ".join(r.message for r in p005)
    assert "alias" in msgs
    assert "deprecated" in msgs


def test_model_pins_reject_blank_string(tmp_path: Path) -> None:
    _core_dir, manifests_dir = _make_poly_workspace(
        tmp_path, models={"agent": "   "}
    )
    with pytest.raises(ManifestError, match="model pin must not be blank"):
        load_manifests(manifests_dir)


def test_governed_lock_push_lifecycle_unlock_import_version_relock(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "locked_app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "Locked App", "locked": True}),
        encoding="utf-8",
    )

    target_name = "projects/p/locations/us/apps/a1"
    events: list[tuple[str, object]] = []
    imported_locked_values: list[object] = []

    apps_client = MagicMock()
    apps_client.creds = MagicMock()
    mock_live = MagicMock()
    mock_live.locked = True
    apps_client.get_app.return_value = mock_live

    def _update_app(name: str, **kwargs: object) -> MagicMock:
        events.append(("update_app", (name, kwargs)))
        return MagicMock()

    def _import_app(
        *,
        app_name: str,
        app_content: bytes,
        conflict_strategy: str | None = None,
    ) -> MagicMock:
        events.append(("import_app", app_name))
        with zipfile.ZipFile(io.BytesIO(app_content), "r") as zf:
            app_member = next(
                n for n in zf.namelist() if n.endswith("app.json")
            )
            staged = json.loads(zf.read(app_member))
            imported_locked_values.append(staged.get("locked"))
        resp = MagicMock()
        resp.result.return_value.name = app_name
        return resp

    apps_client.update_app.side_effect = _update_app
    apps_client.import_app.side_effect = _import_app

    mock_versions_cls = MagicMock()
    mock_version_obj = MagicMock()
    mock_version_obj.name = f"{target_name}/versions/v1"

    def _create_version(**kwargs: object) -> MagicMock:
        events.append(("create_version", kwargs))
        return mock_version_obj

    mock_versions_cls.return_value.create_version.side_effect = _create_version

    args = argparse.Namespace(
        overwrite=True,
        create_version=True,
        version_name="v1",
        version_description="release",
    )
    orig_versions = cli_app.Versions
    cli_app.Versions = mock_versions_cls
    try:
        res = cli_app._app_push(
            app_dir=str(app_dir),
            apps_client=apps_client,
            target_app_name=target_name,
            identifier=target_name,
            display_name="Locked App",
            args=args,
        )
    finally:
        cli_app.Versions = orig_versions

    assert res == target_name
    assert imported_locked_values == [False]
    # Source app.json on disk stays untouched (`locked: True`).
    assert (
        json.loads((app_dir / "app.json").read_text("utf-8"))["locked"] is True
    )
    assert [ev[0] for ev in events] == [
        "update_app",
        "import_app",
        "create_version",
        "update_app",
    ]
    assert events[0][1] == (target_name, {"locked": False})
    assert events[-1][1] == (target_name, {"locked": True})


def test_governed_lock_push_restores_lock_on_import_failure(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "App"}), encoding="utf-8"
    )

    target_name = "projects/p/locations/us/apps/a2"
    apps_client = MagicMock()
    mock_live = MagicMock()
    mock_live.locked = True
    apps_client.get_app.return_value = mock_live
    apps_client.import_app.side_effect = RuntimeError("import boom")

    with pytest.raises(SystemExit) as exc:
        cli_app._app_push(
            app_dir=str(app_dir),
            apps_client=apps_client,
            target_app_name=target_name,
            identifier=target_name,
            display_name="App",
        )
    assert exc.value.code == 1
    assert apps_client.update_app.call_count == 2
    assert apps_client.update_app.call_args_list[0].kwargs == {"locked": False}
    assert apps_client.update_app.call_args_list[1].kwargs == {"locked": True}


def test_cli_lint_flags_and_no_push_lint_flag() -> None:
    parser = get_parser()
    lint_args = parser.parse_args(
        [
            "lint",
            "--app-dir",
            "dist/brand_1/web",
            "--manifests-dir",
            "manifests",
            "--profile",
            "brand_1",
            "--channel",
            "WEB",
        ]
    )
    assert lint_args.manifests_dir == "manifests"
    assert lint_args.profile == "brand_1"
    assert lint_args.channel == "WEB"

    # PR 4 must NOT add --lint to `cxas push` (#593 collision guard).
    with pytest.raises(SystemExit):
        parser.parse_args(["push", "--app-dir", ".", "--lint"])


def test_clear_staged_app_lock_preserves_yaml12_no_and_unicode(
    tmp_path: Path,
) -> None:
    import yaml  # noqa: PLC0415

    from cxas_scrapi.utils.yaml_utils import Yaml12SafeLoader  # noqa: PLC0415

    staged_dir = tmp_path / "staged"
    staged_dir.mkdir()
    (staged_dir / "app.yaml").write_text(
        "displayName: Zürich Nordic App\n"
        "locked: true\n"
        "metadata:\n"
        "  country: NO\n"
        "languageSettings:\n"
        "  defaultLanguageCode: no\n",
        encoding="utf-8",
    )

    cli_app._clear_staged_app_lock(staged_dir)
    raw = (staged_dir / "app.yaml").read_text(encoding="utf-8")
    parsed = yaml.load(raw, Loader=Yaml12SafeLoader)
    assert parsed["locked"] is False
    assert parsed["metadata"]["country"] == "NO"
    assert parsed["languageSettings"]["defaultLanguageCode"] == "no"
    assert "Zürich Nordic App" in raw
