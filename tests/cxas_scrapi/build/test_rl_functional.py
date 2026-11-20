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

"""Failing reproduction tests for Round 1 functional check findings."""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from cxas_scrapi.build import (
    backport_live_to_source,
    build_all,
    build_app,
    check_drift,
)
from cxas_scrapi.cli import main as cli_main

GA_AGENT_MODEL = "gemini-3.5-flash"
GA_LITE_MODEL = "gemini-3.5-flash-lite"


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_cymbal_source(root: Path) -> tuple[Path, Path]:
    core = root / "core"
    _write(
        core / "app.json",
        {
            "name": "@@brand.brand_id@@-assistant",
            "displayName": "@@brand.display_name@@ Assistant",
            "rootAgent": "root_agent",
            "modelSettings": {"model": GA_AGENT_MODEL},
            "guardrails": ["scope_policy"],
        },
    )
    _write(
        core / "global_instruction.txt",
        "You represent @@brand.display_name@@ across every conversation.\n"
        "Support hours are 08:00-20:00 local time. Emergency freeze is 24/7.\n"
        "Verify with @@brand.lexicon.auth_method@@ on @@brand.urls.portal@@.\n",
    )
    _write(
        core / "agents/root_agent/root_agent.json",
        {
            "displayName": "root_agent",
            "description": "Primary router for @@brand.display_name@@.",
            "instruction": "agents/root_agent/instruction.txt",
            "modelSettings": {"model": GA_AGENT_MODEL},
            "childAgents": ["cards_agent"],
            "tools": ["end_session"],
        },
    )
    _write(
        core / "agents/root_agent/instruction.txt",
        "Route card servicing requests to {@AGENT: cards_agent}.\n",
    )
    _write(
        core / "agents/cards_agent/cards_agent.json",
        {
            "displayName": "cards_agent",
            "description": "Card specialist for @@brand.display_name@@.",
            "instruction": "agents/cards_agent/instruction.txt",
            "modelSettings": {"model": GA_AGENT_MODEL, "temperature": 0.2},
            "tools": ["update_card_limit"],
        },
    )
    _write(
        core / "agents/cards_agent/instruction.txt",
        "Assist @@brand.display_name@@ cardholders with card controls.\n"
        "Confirm identity with @@brand.lexicon.auth_method@@ before calling "
        "{@TOOL: update_card_limit}.\n",
    )
    _write(
        core / "tools/update_card_limit/update_card_limit.json",
        {
            "displayName": "update_card_limit",
            "openApiTool": {
                "name": "update_card_limit",
                "description": "Update @@brand.display_name@@ card limit.",
                "url": "@@routing.cards_api@@/v1/cards/limit",
                "openApiSchema": (
                    "tools/update_card_limit/open_api_tool/open_api_schema.yaml"
                ),
            },
        },
    )
    _write(
        core / "tools/update_card_limit/open_api_tool/open_api_schema.yaml",
        "openapi: 3.0.0\n"
        "info:\n"
        "  title: Cymbal Cards API\n"
        "  version: '1.0'\n"
        "servers:\n"
        "  - url: @@routing.cards_api@@\n"
        "paths:\n"
        "  /v1/cards/limit:\n"
        "    post:\n"
        "      operationId: updateCardLimit\n"
        "      responses:\n"
        "        '200':\n"
        "          description: Card limit updated\n"
        "  /v2/cards/limit:\n"
        "    post:\n"
        "      operationId: updateCardLimitV2\n"
        "      responses:\n"
        "        '200':\n"
        "          description: Card limit updated v2\n",
    )
    _write(
        core / "guardrails/scope_policy/scope_policy.json",
        {
            "displayName": "scope_policy",
            "description": "Banking scope guardrail.",
            "enabled": True,
            "llmPolicy": {
                "prompt": "Allow only @@brand.display_name@@ banking topics.",
                "modelSettings": {"model": GA_LITE_MODEL},
            },
        },
    )

    man = root / "manifests"
    for b_id, disp in (
        ("cymbal_direct", "Cymbal Direct"),
        ("cymbal_retail", "Cymbal Retail"),
    ):
        slug = b_id.replace("_", "-")
        _write(
            man / f"brands/{b_id}/identity.yaml",
            f"brand_id: {b_id}\n"
            f"display_name: {disp}\n"
            "lexicon:\n"
            "  auth_method: Cymbal Pass\n"
            "urls:\n"
            f"  portal: https://portal.{slug}.example\n"
            "tts_voices:\n"
            "  en-US:\n"
            "    voice: en-US-Chirp3-HD-Aoede\n"
            "    speaking_rate: 1.0\n",
        )
        _write(
            man / f"brands/{b_id}/channel_policy.yaml",
            f"brand_id: {b_id}\n"
            "api_routing:\n"
            f"  cards_api: https://cards.{slug}.example\n"
            "channels:\n"
            "  DIGITAL_APP:\n"
            "    supported_modalities: ['TEXT']\n"
            "  VOICE_TELEPHONY:\n"
            "    supported_modalities: ['AUDIO']\n",
        )
    _write(
        man / "envs/prod.yaml",
        "environment: prod\n"
        "locked: true\n"
        "models:\n"
        f"  agent: {GA_AGENT_MODEL}\n"
        f"  guardrail: {GA_LITE_MODEL}\n"
        f"  data_store: {GA_LITE_MODEL}\n",
    )
    return core, man


def _ces_export(bundle_dir: Path, live_dir: Path) -> Path:
    if live_dir.exists():
        shutil.rmtree(live_dir)
    shutil.copytree(bundle_dir, live_dir)
    shutil.rmtree(live_dir / "deployments", ignore_errors=True)

    app_data = _read_json(live_dir / "app.json")
    app_data.pop("locked", None)
    _write(live_dir / "app.json", app_data)

    env_tools: dict[str, Any] = {}
    tools_root = live_dir / "tools"
    if tools_root.is_dir():
        for t_dir in sorted(tools_root.iterdir()):
            tj_path = t_dir / f"{t_dir.name}.json"
            if not tj_path.is_file():
                continue
            tj = _read_json(tj_path)
            oa = tj.get("openApiTool")
            if isinstance(oa, dict) and isinstance(oa.get("url"), str):
                concrete_url = oa.pop("url")
                _write(tj_path, tj)
                d_name = str(tj.get("displayName") or t_dir.name)
                env_tools[d_name] = {"openApiTool": {"url": concrete_url}}
                oa_yaml = t_dir / "open_api_tool/open_api_schema.yaml"
                if oa_yaml.is_file():
                    rewritten = [
                        "  - url: $env_var"
                        if ln.strip().startswith("- url:")
                        else ln
                        for ln in oa_yaml.read_text("utf-8").splitlines()
                    ]
                    oa_yaml.write_text(
                        "\n".join(rewritten) + "\n", encoding="utf-8"
                    )
    if env_tools:
        _write(live_dir / "environment.json", {"tools": env_tools})
    return live_dir


def _init_git(repo_dir: Path) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Cymbal Bot",
        "GIT_AUTHOR_EMAIL": "bot@cymbal.example",
        "GIT_COMMITTER_NAME": "Cymbal Bot",
        "GIT_COMMITTER_EMAIL": "bot@cymbal.example",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(
        ["git", "-C", str(repo_dir), "init", "-b", "main"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "add", "."],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", "Init Cymbal Rung 1"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


# RL-PENDING-1
def test_rl_pending_1_line_replacement_unique_substring_preserves_line_context(
    tmp_path: Path,
) -> None:
    """RL-PENDING-1: Single-line unique-substring match keeps surrounding text."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_direct\n"
        "global_instruction:\n"
        "  replace_lines:\n"
        "    - match: '08:00-20:00 local time'\n"
        "      with: '06:00-22:00 CET'\n",
    )
    out_dir = tmp_path / "out"
    build_app(core, man, "cymbal_direct", "DIGITAL_APP", "prod", out_dir)
    compiled_gi = (out_dir / "global_instruction.txt").read_text(
        encoding="utf-8"
    )
    assert (
        "Support hours are 06:00-22:00 CET. Emergency freeze is 24/7."
        in compiled_gi
    )


# RL-PENDING-2
def test_rl_pending_2_core_backport_openapi_url_template_and_env_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """RL-PENDING-2: --target core handles @@...@@ server URL & dual URL files."""
    # Case A: Unquoted `- url: @@routing.cards_api@@` in open_api_schema.yaml
    core_a, man_a = _make_cymbal_source(tmp_path / "repo_a")
    dist_a = tmp_path / "dist_a"
    build_all(core_a, man_a, dist_a, "prod", write_locks=True)
    live_a = _ces_export(
        dist_a / "cymbal_direct/digital_app", tmp_path / "live_a"
    )
    env_a = _read_json(live_a / "environment.json")
    env_a["tools"]["update_card_limit"]["openApiTool"]["url"] = (
        "https://cards.cymbal-direct.example/v2/cards/limit"
    )
    _write(live_a / "environment.json", env_a)

    argv = [
        "cxas",
        "backport",
        "--core-dir",
        str(core_a),
        "--manifests-dir",
        str(man_a),
        "--live",
        str(live_a),
        "--dist",
        str(dist_a / "cymbal_direct/digital_app"),
        "--target",
        "core",
        "--json",
    ]
    with mock.patch.object(sys, "argv", argv):
        cli_main.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist_a / "cymbal_direct/digital_app",
            live_a,
            lockfile_path=dist_a / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )

    # Case B: Both core/environment.json and core/tools/<id>/<id>.json have url
    core_b, man_b = _make_cymbal_source(tmp_path / "repo_b")
    _write(
        core_b / "environment.json",
        {
            "tools": {
                "update_card_limit": {
                    "openApiTool": {
                        "url": "@@routing.cards_api@@/v1/cards/limit"
                    }
                }
            }
        },
    )
    dist_b = tmp_path / "dist_b"
    build_all(core_b, man_b, dist_b, "prod", write_locks=True)
    live_b = _ces_export(
        dist_b / "cymbal_direct/digital_app", tmp_path / "live_b"
    )
    env_b = _read_json(live_b / "environment.json")
    env_b["tools"]["update_card_limit"]["openApiTool"]["url"] = (
        "https://cards.cymbal-direct.example/v2/cards/limit"
    )
    _write(live_b / "environment.json", env_b)

    backport_live_to_source(
        core_b,
        man_b,
        live_b,
        dist_dir=dist_b / "cymbal_direct/digital_app",
        target="core",
    )
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist_b / "cymbal_direct/digital_app",
            live_b,
            lockfile_path=dist_b / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )


# RL-PENDING-3
def test_rl_pending_3_backport_omitted_model_settings_and_cleared_description(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """RL-PENDING-3: No KeyError on omitted model/temperature; cleared desc works."""
    for omitted_key in ("temperature", "model"):
        sub = tmp_path / f"omit_{omitted_key}"
        core, man = _make_cymbal_source(sub / "repo")
        dist = sub / "dist"
        build_all(core, man, dist, "prod", write_locks=True)
        live = _ces_export(dist / "cymbal_direct/digital_app", sub / "live")
        cards_j = _read_json(live / "agents/cards_agent/cards_agent.json")
        cards_j["modelSettings"].pop(omitted_key, None)
        _write(live / "agents/cards_agent/cards_agent.json", cards_j)

        argv = [
            "cxas",
            "backport",
            "--core-dir",
            str(core),
            "--manifests-dir",
            str(man),
            "--live",
            str(live),
            "--dist",
            str(dist / "cymbal_direct/digital_app"),
            "--json",
        ]
        exit_code: int | str | None = 0
        with mock.patch.object(sys, "argv", argv):
            try:
                cli_main.main()
            except SystemExit as exc:
                exit_code = exc.code
        assert exit_code in (0, 1)
        out_json = json.loads(capsys.readouterr().out)
        assert isinstance(out_json.get("passed"), bool)

    # Clearing optional `description` in live succeeds cleanly with --target core
    core_d, man_d = _make_cymbal_source(tmp_path / "clear_desc/repo")
    dist_d = tmp_path / "clear_desc/dist"
    build_all(core_d, man_d, dist_d, "prod", write_locks=True)
    live_d = _ces_export(
        dist_d / "cymbal_direct/digital_app", tmp_path / "clear_desc/live"
    )
    cards_d = _read_json(live_d / "agents/cards_agent/cards_agent.json")
    cards_d.pop("description", None)
    _write(live_d / "agents/cards_agent/cards_agent.json", cards_d)
    gr_d = _read_json(live_d / "guardrails/scope_policy/scope_policy.json")
    gr_d.pop("description", None)
    _write(live_d / "guardrails/scope_policy/scope_policy.json", gr_d)

    backport_live_to_source(
        core_d,
        man_d,
        live_d,
        dist_dir=dist_d / "cymbal_direct/digital_app",
        target="core",
    )
    assert (
        check_drift(
            "cymbal_direct/digital_app",
            dist_d / "cymbal_direct/digital_app",
            live_d,
            lockfile_path=dist_d / "cymbal_direct/digital_app.lock.json",
        ).is_clean
        is True
    )


# RL-PENDING-4
def test_rl_pending_4_documented_build_md_backport_commands_succeed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """RL-PENDING-4: All `cxas backport` examples in docs/guides/build.md work."""
    repo_root = Path(__file__).resolve().parents[3]
    guide_text = (repo_root / "docs/guides/build.md").read_text(
        encoding="utf-8"
    )
    bash_blocks = re.findall(r"```bash\n(.*?)```", guide_text, flags=re.DOTALL)
    bp_blocks = [b for b in bash_blocks if "cxas backport" in b]
    assert bp_blocks, (
        "Expected cxas backport bash block in docs/guides/build.md"
    )

    commands: list[list[str]] = []
    current_lines: list[str] = []
    for raw_line in bp_blocks[0].splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            current_lines.append(stripped[:-1].strip())
        else:
            current_lines.append(stripped)
            commands.append(shlex.split(" ".join(current_lines)))
            current_lines = []
    assert len(commands) >= 3

    ws = tmp_path / "ws"
    core, man = _make_cymbal_source(ws)
    _init_git(ws)
    dist = ws / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = _ces_export(
        dist / "cymbal_direct/digital_app",
        ws / "exports/cymbal_direct_digital_live",
    )
    (live / "agents/cards_agent/instruction.txt").write_text(
        "Assist Cymbal Direct cardholders on "
        "https://portal.cymbal-direct.example.\n"
        "Confirm identity with Cymbal Pass before calling "
        "{@TOOL: update_card_limit}.\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(ws)
    for argv in commands:
        with mock.patch.object(sys, "argv", argv):
            cli_main.main()
        capsys.readouterr()


# RL-PENDING-5
def test_rl_pending_5_brand_and_core_promotion_include_prior_channel_overrides(
    tmp_path: Path,
) -> None:
    """RL-PENDING-5: --scope brand and --target core promote prior channel edits."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = _ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_export"
    )

    # Step 1: Channel-scoped edit on root_agent
    (live / "agents/root_agent/instruction.txt").write_text(
        "Route priority Cymbal Direct card requests to {@AGENT: cards_agent}.\n",
        encoding="utf-8",
    )
    backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_direct/digital_app",
        target="brand",
        scope="channel",
    )

    # Step 2: New edit on cards_agent + promote live to brand-wide scope
    (live / "agents/cards_agent/instruction.txt").write_text(
        "Assist Cymbal Direct cardholders promptly.\n"
        "Confirm identity with Cymbal Pass before calling "
        "{@TOOL: update_card_limit}.\n",
        encoding="utf-8",
    )
    backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        target="brand",
        scope="brand",
    )
    voice_root_in = (
        dist / "cymbal_direct/voice_telephony/agents/root_agent/instruction.txt"
    ).read_text(encoding="utf-8")
    assert "Route priority Cymbal Direct card requests" in voice_root_in

    # Step 3: Another live edit on global_instruction + promote all to core/
    gi_p = live / "global_instruction.txt"
    gi_p.write_text(
        gi_p.read_text(encoding="utf-8")
        + "- Keep Cymbal Direct explanations concise.\n",
        encoding="utf-8",
    )
    backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist,
        brand_id="cymbal_direct",
        channel="DIGITAL_APP",
        environment="prod",
        target="core",
    )
    retail_root_in = (
        dist / "cymbal_retail/digital_app/agents/root_agent/instruction.txt"
    ).read_text(encoding="utf-8")
    assert "Route priority Cymbal Retail card requests" in retail_root_in


# RL-PENDING-6
def test_rl_pending_6_backport_supports_optional_instruction_and_global_files(
    tmp_path: Path,
) -> None:
    """RL-PENDING-6: Missing optional instruction.txt / global_instruction.txt."""
    # Part A: Agent without instruction.txt edited in live JSON
    core_a, man_a = _make_cymbal_source(tmp_path / "no_instr/repo")
    (core_a / "agents/cards_agent/instruction.txt").unlink()
    cj_a = _read_json(core_a / "agents/cards_agent/cards_agent.json")
    cj_a.pop("instruction", None)
    cj_a["tools"] = []
    _write(core_a / "agents/cards_agent/cards_agent.json", cj_a)

    dist_a = tmp_path / "no_instr/dist"
    build_all(core_a, man_a, dist_a, "prod", write_locks=True)
    live_a = _ces_export(
        dist_a / "cymbal_direct/digital_app", tmp_path / "no_instr/live"
    )
    live_cj = _read_json(live_a / "agents/cards_agent/cards_agent.json")
    live_cj["description"] = "Updated Cymbal Direct card specialist."
    _write(live_a / "agents/cards_agent/cards_agent.json", live_cj)

    res_a = backport_live_to_source(
        core_a,
        man_a,
        live_a,
        dist_dir=dist_a / "cymbal_direct/digital_app",
        target="brand",
        scope="channel",
        run_lint=False,
    )
    assert any(c.resource_id == "agents/cards_agent" for c in res_a.changes)

    # Part B: App without core/global_instruction.txt gets global_instruction.txt
    core_b, man_b = _make_cymbal_source(tmp_path / "no_gi/repo")
    (core_b / "global_instruction.txt").unlink()
    dist_b = tmp_path / "no_gi/dist"
    build_all(core_b, man_b, dist_b, "prod", write_locks=True)
    live_b = _ces_export(
        dist_b / "cymbal_direct/digital_app", tmp_path / "no_gi/live"
    )
    (live_b / "global_instruction.txt").write_text(
        "# Brand: Cymbal Direct\n"
        "Lexicon:\n"
        "  - auth_method: Cymbal Pass\n"
        "URLs:\n"
        "  - portal: https://portal.cymbal-direct.example\n\n"
        "Global policy for Cymbal Direct.\n",
        encoding="utf-8",
    )
    res_b = backport_live_to_source(
        core_b,
        man_b,
        live_b,
        dist_dir=dist_b / "cymbal_direct/digital_app",
        target="brand",
        scope="channel",
        run_lint=False,
    )
    assert any(c.resource_id == "app" for c in res_b.changes)


# RL-PENDING-7
def test_rl_pending_7_cli_build_handles_manifest_error_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """RL-PENDING-7: `cxas build` catches ManifestError from overrides.yaml."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    _write(
        man / "brands/cymbal_direct/overrides.yaml",
        "brand_id: cymbal_retail\n",
    )
    argv = [
        "cxas",
        "build",
        "--core-dir",
        str(core),
        "--manifests-dir",
        str(man),
        "--env",
        "prod",
        "--out",
        str(tmp_path / "dist"),
        "--all",
    ]
    with (
        mock.patch.object(sys, "argv", argv),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli_main.main()
    assert exc_info.value.code == 1
    assert "cymbal_retail" in capsys.readouterr().err


# RL-PENDING-8
def test_rl_pending_8_pr_body_does_not_claim_lint_pass_when_no_lint_used(
    tmp_path: Path,
) -> None:
    """RL-PENDING-8: --no-lint PR body must not falsely attest cxas lint PASS."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    live = _ces_export(
        dist / "cymbal_direct/digital_app", tmp_path / "live_nolin"
    )
    # Sister-brand leak (`Cymbal Retail`) would fail P001 if lint ran.
    (live / "agents/cards_agent/instruction.txt").write_text(
        "Assist Cymbal Retail cardholders.\nUse {@TOOL: update_card_limit}.\n",
        encoding="utf-8",
    )
    res = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=dist / "cymbal_direct/digital_app",
        run_lint=False,
    )
    lint_lines = [
        ln
        for ln in res.pr_body_markdown.splitlines()
        if "cxas lint" in ln.lower()
    ]
    assert lint_lines
    assert all("PASS" not in ln for ln in lint_lines)


# RL-PENDING-9
def test_rl_pending_9_backport_lifts_accepted_live_hotfix_instead_of_dropping_it(
    tmp_path: Path,
) -> None:
    """RL-PENDING-9: `cxas backport` must lift `accepted_from_live` hotfixes."""
    core, man = _make_cymbal_source(tmp_path / "repo")
    dist = tmp_path / "dist"
    build_all(core, man, dist, "prod", write_locks=True)
    bundle = dist / "cymbal_direct/digital_app"
    lock = dist / "cymbal_direct/digital_app.lock.json"

    live = _ces_export(bundle, tmp_path / "live")
    hotfix_line = (
        "Emergency Cymbal Direct rule: confirm Cymbal Pass before card changes."
    )
    (live / "agents/cards_agent/instruction.txt").write_text(
        f"{hotfix_line}\nUse {{@TOOL: update_card_limit}}.\n",
        encoding="utf-8",
    )

    # Operator adopts emergency Console hotfix via `cxas drift --accept-live`
    check_drift(
        "cymbal_direct/digital_app",
        bundle,
        live,
        lockfile_path=lock,
        accept_live=("agents/cards_agent",),
    )

    # Operator now runs `cxas backport` to lift that hotfix into overrides.yaml
    res = backport_live_to_source(
        core,
        man,
        live,
        dist_dir=bundle,
        lockfile_path=lock,
    )
    assert any(c.resource_id == "agents/cards_agent" for c in res.changes)
    assert (man / "brands/cymbal_direct/overrides.yaml").is_file()
    assert hotfix_line in (
        bundle / "agents/cards_agent/instruction.txt"
    ).read_text(encoding="utf-8")
