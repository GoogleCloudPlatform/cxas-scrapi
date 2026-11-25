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

"""Adversarial E2E and edge-case QA tests for PR 4 (polymorphic lint & lock).

Contains:
- Passing edge-case & CLI E2E regression tests (``test_qa_pr4_...``).
- Failing reproduction tests for open QA defects (``test_qa_pr4_001_...``
  through ``test_qa_pr4_006_...``), which must turn green once fixed.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from cxas_scrapi.build.builder import build_app
from cxas_scrapi.build.manifests import ManifestError, ModelPins
from cxas_scrapi.cli import app as cli_app
from cxas_scrapi.cli.main import get_parser
from cxas_scrapi.utils.linter import (
    CES_PLATFORM_SYSTEM_TOOLS,
    LintContext,
    build_registry,
    collect_lint_report,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(_REPO_ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "cxas_scrapi.cli.main", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _make_workspace(
    tmp_path: Path,
    *,
    models: dict[str, str] | None = None,
    locked: bool | None = None,
    forbidden_tokens: list[str] | None = None,
    brand_1_display: str = "Alpha Bank",
    brand_2_display: str = "Beta Bank",
) -> tuple[Path, Path]:
    core_dir = tmp_path / "core"
    (core_dir / "agents" / "root_agent").mkdir(parents=True)
    (core_dir / "agents" / "web_only_agent").mkdir(parents=True)
    (core_dir / "tools" / "active_tool" / "python_function").mkdir(parents=True)
    (core_dir / "tools" / "api_tool" / "open_api_tool").mkdir(parents=True)
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
                    "api_tool",
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
        "Use {@TOOL: active_tool}, {@TOOL: kb_search}, "
        "{@TOOL: end_session}, and {@TOOL: customize_response}.\n"
        "{@startToolEnabled_disabled_tool}\n"
        "Also call {@TOOL: disabled_tool}.\n"
        "{@endToolEnabled_disabled_tool}\n"
        "{@startAgentEnabled_web_only_agent}\n"
        "Or transfer to {@AGENT: web_only_agent}.\n"
        "{@endAgentEnabled_web_only_agent}\n",
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
    (
        core_dir
        / "tools"
        / "active_tool"
        / "python_function"
        / "python_code.py"
    ).write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class Result:\n"
        "    brand: str = '@@profile.display_name@@'\n"
        "    support: str = 'support@example.com'\n",
        encoding="utf-8",
    )
    (core_dir / "tools" / "api_tool" / "api_tool.json").write_text(
        json.dumps({"displayName": "api_tool"}), encoding="utf-8"
    )
    (
        core_dir / "tools" / "api_tool" / "open_api_tool" / "schema.yaml"
    ).write_text(
        "openapi: 3.0.0\ninfo:\n  title: Clean API\npaths: {}\n",
        encoding="utf-8",
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
                "display_name": brand_1_display,
                "forbidden_tokens": (
                    forbidden_tokens
                    if forbidden_tokens is not None
                    else ["LegacyBrand", "foo.bar", "C++", "$100", "a(b)", "  "]
                ),
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
            {"profile_id": "brand_2", "display_name": brand_2_display},
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


# ── Passing Edge-Case & E2E Regression Tests ────────────────────────────


def test_qa_pr4_p001_case_insensitive_surfaces_metachars_and_binaries(
    tmp_path: Path,
) -> None:
    """P001 catches leaks across all text surfaces, escapes regex metachars,
    and never crashes on binary or invalid-UTF8 files."""
    core_dir, manifests_dir = _make_workspace(tmp_path)
    out_dir = tmp_path / "built_b1"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_dir
    )

    # Inject binary files and an invalid UTF-8 text file (must NOT crash).
    (out_dir / "assets").mkdir()
    (out_dir / "assets" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff")
    (out_dir / "tools" / "active_tool" / "bad_utf8.txt").write_bytes(
        b"\xff\xfe\x00\x80invalid"
    )

    # Non-matching lookalikes must NOT trigger regex metachar false positives.
    instr = out_dir / "agents" / "root_agent" / "instruction.txt"
    instr.write_text("Mention fooXbar, ab, and $1000 only.\n", encoding="utf-8")
    clean_rep = collect_lint_report(out_dir, manifests_dir=manifests_dir)
    assert not [r for r in clean_rep.results if r.rule_id == "P001"]

    # Inject case-insensitive leaks across every supported file surface.
    (out_dir / "global_instruction.txt").write_text(
        "Welcome to BETA BANK and foo.bar!\n", encoding="utf-8"
    )
    instr.write_text("Use C++, pay $100, formula a(b) = 1.\n", encoding="utf-8")
    (out_dir / "guardrails" / "brand_guard" / "brand_guard.json").write_text(
        json.dumps({"llmPolicy": {"prompt": "Block legacybrand mentions."}}),
        encoding="utf-8",
    )
    (
        out_dir / "tools" / "api_tool" / "open_api_tool" / "schema.yaml"
    ).write_text(
        "openapi: 3.0.0\ninfo:\n  title: beta bank API\n",
        encoding="utf-8",
    )
    (
        out_dir / "tools" / "active_tool" / "python_function" / "python_code.py"
    ).write_text("MSG = 'LegacyBrand'\n", encoding="utf-8")

    rep = collect_lint_report(out_dir, manifests_dir=manifests_dir)
    p001 = [r for r in rep.results if r.rule_id == "P001"]
    files = {r.file for r in p001}
    assert "global_instruction.txt" in files
    assert "agents/root_agent/instruction.txt" in files
    assert "guardrails/brand_guard/brand_guard.json" in files
    assert "tools/api_tool/open_api_tool/schema.yaml" in files
    assert "tools/active_tool/python_function/python_code.py" in files
    msgs = " ".join(r.message for r in p001)
    for tok in ("Beta Bank", "LegacyBrand", "foo.bar", "C++", "$100", "a(b)"):
        assert tok in msgs


def test_qa_pr4_p001_auto_inference_vs_explicit_brand_override(
    tmp_path: Path,
) -> None:
    """Brand/channel auto-infer from deployments/*.yaml and respect explicit
    overrides without false positives on own substring display_name."""
    core_dir, manifests_dir = _make_workspace(
        tmp_path,
        brand_1_display="Alpha Bank Private",
        brand_2_display="Alpha Bank",
        forbidden_tokens=[],
    )
    out_dir = tmp_path / "built_b1"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)

    # Sister display_name 'Alpha Bank' is a substring of 'Alpha Bank Private',
    # so brand_1 must NOT false-positive on its own display_name.
    rep_b1 = collect_lint_report(out_dir, manifests_dir=manifests_dir)
    assert not [r for r in rep_b1.results if r.rule_id == "P001"]

    # Overriding --brand brand_2 flags 'Alpha Bank Private' (brand_1's name)
    # because 'Alpha Bank Private' is NOT a substring of 'Alpha Bank'.
    rep_b2 = collect_lint_report(
        out_dir, manifests_dir=manifests_dir, profile="brand_2"
    )
    p001 = [r for r in rep_b2.results if r.rule_id == "P001"]
    assert p001
    assert any("Alpha Bank Private" in r.message for r in p001)


def test_qa_pr4_p002_exact_boundary_20_vs_21_and_null_tools(
    tmp_path: Path,
) -> None:
    """P002 passes at exact boundary 20, warns at 21, handles tools: null and
    non-numeric string options cleanly."""
    app_dir = tmp_path / "app"
    (app_dir / "agents" / "agent_20").mkdir(parents=True)
    (app_dir / "agents" / "agent_21").mkdir(parents=True)
    (app_dir / "agents" / "agent_null").mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "App", "rootAgent": "agent_20"}),
        encoding="utf-8",
    )
    (app_dir / "agents" / "agent_20" / "agent_20.json").write_text(
        json.dumps(
            {
                "displayName": "agent_20",
                "tools": [f"t{i}" for i in range(20)],
            }
        ),
        encoding="utf-8",
    )
    (app_dir / "agents" / "agent_21" / "agent_21.json").write_text(
        json.dumps(
            {
                "displayName": "agent_21",
                "tools": [f"t{i}" for i in range(21)],
            }
        ),
        encoding="utf-8",
    )

    rep = collect_lint_report(app_dir)
    p002 = [r for r in rep.results if r.rule_id == "P002"]
    assert len(p002) == 1
    assert "agent_21" in p002[0].message
    assert "21 tools (cap 20)" in p002[0].message
    assert p002[0].severity.value == "warning"

    # P002 directly handles tools: null without crashing.
    (app_dir / "agents" / "agent_null" / "agent_null.json").write_text(
        json.dumps({"displayName": "agent_null", "tools": None}),
        encoding="utf-8",
    )
    p002_rule = build_registry().get("P002")
    assert p002_rule is not None
    null_ctx = LintContext(
        project_root=app_dir,
        app_dir=app_dir,
        evals_dir=app_dir / "evals",
        app_root=app_dir,
    )
    p002_null = p002_rule.check(app_dir / "app.json", "", null_ctx)
    assert len(p002_null) == 1
    assert "agent_21" in p002_null[0].message
    (app_dir / "agents" / "agent_null" / "agent_null.json").unlink()

    # Non-numeric option falls back to default 20 cleanly.
    rep_bad = collect_lint_report(
        app_dir, rule_options={"P002": {"max_tools": "bad"}}
    )
    p002_bad = [r for r in rep_bad.results if r.rule_id == "P002"]
    assert len(p002_bad) == 1
    assert "cap 20" in p002_bad[0].message

    # cxaslint.yaml options.P002.max_tools is respected.
    (app_dir / "cxaslint.yaml").write_text(
        "options:\n  P002:\n    max_tools: 10\n", encoding="utf-8"
    )
    rep_cfg = collect_lint_report(app_dir)
    p002_cfg = [r for r in rep_cfg.results if r.rule_id == "P002"]
    assert len(p002_cfg) == 2


def test_qa_pr4_p003_tags_and_zero_false_positives_on_valid_syntax(
    tmp_path: Path,
) -> None:
    """P003 catches leftover build placeholders & conditionals while ignoring
    valid CES tags, modality tags, emails, and Python decorators."""
    app_dir = tmp_path / "app"
    (app_dir / "agents" / "root_agent").mkdir(parents=True)
    (app_dir / "tools" / "py_tool" / "python_function").mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "App", "rootAgent": "root_agent"}),
        encoding="utf-8",
    )
    (app_dir / "agents" / "root_agent" / "root_agent.json").write_text(
        json.dumps({"displayName": "root_agent", "tools": ["py_tool"]}),
        encoding="utf-8",
    )
    (app_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "Call {@TOOL: py_tool} or {@AGENT: root_agent}.\n"
        "{@startModality_Voice}\nSpeak briefly.\n{@endModality_Voice}\n"
        "Contact support@example.com or ops+help@sub.example.org.\n",
        encoding="utf-8",
    )
    (
        app_dir / "tools" / "py_tool" / "python_function" / "python_code.py"
    ).write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class Item:\n"
        "    @property\n"
        "    def email(self) -> str:\n"
        "        return 'help@example.com'\n",
        encoding="utf-8",
    )

    clean = collect_lint_report(app_dir)
    assert not [r for r in clean.results if r.rule_id == "P003"]

    # Now inject every unresolved build tag form.
    (app_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "a=@@brand.name@@\n"
        "b=@@env.api:users@@\n"
        "c=@@routing.core@@\n"
        "d={@startChannel_WEB}\n"
        "e={@endBrand_brand_1}\n"
        "f=@@brand.unclosed\n",
        encoding="utf-8",
    )
    dirty = collect_lint_report(app_dir)
    p003 = [r for r in dirty.results if r.rule_id == "P003"]
    assert len(p003) == 6
    joined = " ".join(r.message for r in p003)
    for token in (
        "@@brand.name@@",
        "@@env.api:users@@",
        "@@routing.core@@",
        "{@startChannel_WEB}",
        "{@endBrand_brand_1}",
        "@@brand.unclosed",
    ):
        assert token in joined


def test_qa_pr4_p004_channel_scoping_and_system_tools_across_rules(
    tmp_path: Path,
) -> None:
    """P004 scopes disabled resources strictly to the active channel, handles
    {@AGENT:x} with/without space, and never flags CES system tools in P004,
    S002, S004, or A005."""
    core_dir, manifests_dir = _make_workspace(tmp_path)

    # WEB channel keeps web_only_agent and disabled_tool -> ZERO P004/S002/S004.
    out_web = tmp_path / "built_web"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_web)
    rep_web = collect_lint_report(out_web, manifests_dir=manifests_dir)
    for rid in ("P004", "S002", "S004", "A005"):
        assert not [r for r in rep_web.results if r.rule_id == rid], (
            f"Unexpected {rid} on WEB build: "
            f"{[r for r in rep_web.results if r.rule_id == rid]}"
        )

    # VOICE_TELEPHONY build: inject {@AGENT:web_only_agent} (no space) and
    # {@TOOL: disabled_tool} (with space), plus rootAgent disabled check.
    out_voice = tmp_path / "built_voice"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_voice
    )
    instr = out_voice / "agents" / "root_agent" / "instruction.txt"
    instr.write_text(
        "Transfer to {@AGENT:web_only_agent}, call {@TOOL: disabled_tool}, "
        "and finish with {@TOOL: end_session} and "
        "{@TOOL: customize_response}.\n",
        encoding="utf-8",
    )
    app_data = json.loads((out_voice / "app.json").read_text("utf-8"))
    app_data["rootAgent"] = "web_only_agent"
    (out_voice / "app.json").write_text(json.dumps(app_data), encoding="utf-8")

    rep_voice = collect_lint_report(out_voice, manifests_dir=manifests_dir)
    p004 = [r for r in rep_voice.results if r.rule_id == "P004"]
    assert len(p004) == 3
    assert any("rootAgent" in r.message for r in p004)
    assert any("web_only_agent" in r.message for r in p004)
    assert any("disabled_tool" in r.message for r in p004)
    for sys_tool in CES_PLATFORM_SYSTEM_TOOLS:
        assert not any(sys_tool in r.message for r in p004)
        assert not any(
            sys_tool in r.message
            for r in rep_voice.results
            if r.rule_id in {"S002", "S004"}
        )


@pytest.mark.parametrize(
    "bad_model",
    [
        "default",
        "latest",
        "auto",
        "gemini-1.5-pro",
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "GEMINI-2.5-PRO",
        "foo-latest",
        "",
        "   ",
    ],
)
def test_qa_pr4_p005_flags_all_aliases_deprecated_and_blank_models(
    tmp_path: Path, bad_model: str
) -> None:
    """P005 flags every disallowed alias, deprecated model, and blank model."""
    app_dir = tmp_path / "app"
    (app_dir / "agents" / "r").mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "displayName": "App",
                "rootAgent": "r",
                "modelSettings": {"model": bad_model},
            }
        ),
        encoding="utf-8",
    )
    (app_dir / "agents" / "r" / "r.json").write_text(
        json.dumps({"displayName": "r", "modelSettings": None}),
        encoding="utf-8",
    )
    rep = collect_lint_report(app_dir)
    p005 = [r for r in rep.results if r.rule_id == "P005"]
    assert len(p005) == 1
    assert p005[0].file == "app.json"


def test_qa_pr4_p005_valid_ga_models_and_null_model_settings_pass(
    tmp_path: Path,
) -> None:
    """Valid gemini-3.5-flash / gemini-3.5-flash-lite and modelSettings: null
    pass P005 cleanly across app, agents, guardrails, and dataStoreTool."""
    core_dir, manifests_dir = _make_workspace(
        tmp_path,
        models={
            "agent": "gemini-3.5-flash",
            "guardrail": "gemini-3.5-flash-lite",
            "data_store": "gemini-3.5-flash-lite",
        },
        locked=True,
    )
    # Set modelSettings: null in core to verify builder replaces it cleanly.
    app_path = core_dir / "app.json"
    app_doc = json.loads(app_path.read_text("utf-8"))
    app_doc["modelSettings"] = None
    app_path.write_text(json.dumps(app_doc), encoding="utf-8")

    agent_path = core_dir / "agents" / "root_agent" / "root_agent.json"
    agent_doc = json.loads(agent_path.read_text("utf-8"))
    agent_doc["modelSettings"] = {
        "temperature": 0.2,
        "model": "gemini-2.0-flash",
    }
    agent_path.write_text(json.dumps(agent_doc), encoding="utf-8")

    out_dir = tmp_path / "built_ga"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)

    built_app = json.loads((out_dir / "app.json").read_text("utf-8"))
    assert built_app["modelSettings"] == {"model": "gemini-3.5-flash"}
    assert built_app["locked"] is True

    built_agent = json.loads(
        (out_dir / "agents" / "root_agent" / "root_agent.json").read_text(
            "utf-8"
        )
    )
    assert built_agent["modelSettings"] == {
        "temperature": 0.2,
        "model": "gemini-3.5-flash",
    }

    rep = collect_lint_report(out_dir, manifests_dir=manifests_dir)
    assert not [r for r in rep.results if r.rule_id == "P005"]


def test_qa_pr4_model_pins_validation_rejects_bad_types_and_extra_keys() -> (
    None
):
    """ModelPins rejects blank strings, non-strings, and unknown slot keys."""
    for bad in (
        {"agent": ""},
        {"guardrail": "   "},
        {"data_store": "\t\n"},
        {"agent": 123},
        {"agent": True},
        {"unknown_slot": "gemini-3.5-flash"},
    ):
        with pytest.raises((ValueError, ManifestError)):
            ModelPins.model_validate(bad)


def test_qa_pr4_push_unlocked_target_with_locked_bundle_preserves_source(
    tmp_path: Path,
) -> None:
    """Unlocked live target + bundle locked:true does NOT pre-unlock, strips
    locked:true only in staged ZIP (source untouched), versions, then locks."""
    app_dir = tmp_path / "locked_bundle"
    app_dir.mkdir()
    src_app_json = app_dir / "app.json"
    src_app_json.write_text(
        json.dumps({"displayName": "Prod App", "locked": True}),
        encoding="utf-8",
    )

    target_name = "projects/p/locations/us/apps/unlocked1"
    apps_client = MagicMock()
    apps_client.creds = MagicMock()
    live = MagicMock()
    live.locked = False
    apps_client.get_app.return_value = live

    imported_locked: list[object] = []

    def _import_app(
        *,
        app_name: str,
        app_content: bytes,
        conflict_strategy: str | None = None,
    ) -> MagicMock:
        with zipfile.ZipFile(io.BytesIO(app_content), "r") as zf:
            member = next(n for n in zf.namelist() if n.endswith("app.json"))
            imported_locked.append(json.loads(zf.read(member)).get("locked"))
        resp = MagicMock()
        resp.result.return_value.name = app_name
        return resp

    apps_client.import_app.side_effect = _import_app

    res = cli_app._app_push(
        app_dir=str(app_dir),
        apps_client=apps_client,
        target_app_name=target_name,
        identifier=target_name,
        display_name="Prod App",
    )

    assert res == target_name
    assert imported_locked == [False]
    # Caller's source app.json MUST remain untouched.
    assert json.loads(src_app_json.read_text("utf-8"))["locked"] is True
    # Only one update_app call: post-import lock (no pre-unlock).
    assert apps_client.update_app.call_count == 1
    assert apps_client.update_app.call_args.kwargs == {"locked": True}


def test_qa_pr4_push_failure_recovery_when_relock_also_raises(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """If import_app fails AND finally re-lock also raises, original import
    failure is still reported and exit code 1 is preserved."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "App", "locked": True}), encoding="utf-8"
    )

    target_name = "projects/p/locations/us/apps/locked_fail"
    apps_client = MagicMock()
    live = MagicMock()
    live.locked = True
    apps_client.get_app.return_value = live
    apps_client.import_app.side_effect = RuntimeError("original import error")

    def _update_app(name: str, **kwargs: object) -> MagicMock:
        if kwargs.get("locked") is True:
            raise RuntimeError("relock rpc error")
        return MagicMock()

    apps_client.update_app.side_effect = _update_app

    with pytest.raises(SystemExit) as exc:
        cli_app._app_push(
            app_dir=str(app_dir),
            apps_client=apps_client,
            target_app_name=target_name,
            identifier=target_name,
            display_name="App",
        )

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "original import error" in captured.out
    assert "relock rpc error" in captured.err
    assert apps_client.update_app.call_count == 2
    assert apps_client.update_app.call_args_list[0].kwargs == {"locked": False}
    assert apps_client.update_app.call_args_list[1].kwargs == {"locked": True}


def test_qa_pr4_push_omitted_lock_preserves_existing_live_lock(
    tmp_path: Path,
) -> None:
    """When live target is locked and bundle omits 'locked', push unlocks for
    import and preserves the lock afterwards."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "App"}), encoding="utf-8"
    )

    target_name = "projects/p/locations/us/apps/locked_omit"
    apps_client = MagicMock()
    live = MagicMock()
    live.locked = True
    apps_client.get_app.return_value = live
    resp = MagicMock()
    resp.result.return_value.name = target_name
    apps_client.import_app.return_value = resp

    cli_app._app_push(
        app_dir=str(app_dir),
        apps_client=apps_client,
        target_app_name=target_name,
        identifier=target_name,
        display_name="App",
    )
    assert apps_client.update_app.call_count == 2
    assert apps_client.update_app.call_args_list[0].kwargs == {"locked": False}
    assert apps_client.update_app.call_args_list[1].kwargs == {"locked": True}


def test_qa_pr4_scope_boundaries_no_push_lint_or_downstream_files() -> None:
    """Confirm PR 4 excludes cxas push --lint (no #593 collision) and does not
    leak PR 3/5/8 modules."""
    parser = get_parser()
    push_help = _run_cli(["push", "--help"])
    assert push_help.returncode == 0
    assert "--lint" not in push_help.stdout
    with pytest.raises(SystemExit):
        parser.parse_args(["push", "--app-dir", ".", "--lint"])

    assert not (_REPO_ROOT / "src/cxas_scrapi/build/python_bundler.py").exists()
    assert not (_REPO_ROOT / "src/cxas_scrapi/cli/drift.py").exists()
    assert not (_REPO_ROOT / "src/cxas_scrapi/cli/backport.py").exists()


def test_qa_pr4_cli_e2e_build_lint_and_list_rules(tmp_path: Path) -> None:
    """Real CLI subprocess E2E: cxas build -> cxas lint clean (0), violations
    (1), and cxas lint --list-rules reports >= 109 rules including P001-P005."""
    list_proc = _run_cli(["lint", "--list-rules"])
    assert list_proc.returncode == 0, list_proc.stderr
    rule_lines = [
        ln
        for ln in list_proc.stdout.splitlines()
        if re.match(r"^\s+[A-Z]\d{3}\s+", ln)
    ]
    assert len(rule_lines) >= 109  # noqa: PLR2004
    for pid in ("P001", "P002", "P003", "P004", "P005"):
        assert pid in list_proc.stdout

    core_dir, manifests_dir = _make_workspace(
        tmp_path,
        models={
            "agent": "gemini-3.5-flash",
            "guardrail": "gemini-3.5-flash-lite",
            "data_store": "gemini-3.5-flash-lite",
        },
        locked=True,
    )
    out_dir = tmp_path / "dist_b1_voice"
    build_proc = _run_cli(
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--profile",
            "brand_1",
            "--channel",
            "VOICE_TELEPHONY",
            "--env",
            "prod",
            "--out",
            str(out_dir),
        ]
    )
    assert build_proc.returncode == 0, build_proc.stderr + build_proc.stdout

    # Clean lint exits 0.
    lint_ok = _run_cli(
        [
            "lint",
            "--app-dir",
            str(out_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--rule",
            "P001,P002,P003,P004,P005",
        ]
    )
    assert lint_ok.returncode == 0, lint_ok.stdout + lint_ok.stderr

    # Inject P001 leak + P003 tag + P005 deprecated model -> exits 1.
    (out_dir / "global_instruction.txt").write_text(
        "Welcome to Beta Bank @@brand.missing@@.\n", encoding="utf-8"
    )
    app_doc = json.loads((out_dir / "app.json").read_text("utf-8"))
    app_doc["modelSettings"]["model"] = "gemini-2.5-flash"
    (out_dir / "app.json").write_text(json.dumps(app_doc), encoding="utf-8")

    lint_fail = _run_cli(
        [
            "lint",
            "--app-dir",
            str(out_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--rule",
            "P001,P002,P003,P004,P005",
        ]
    )
    assert lint_fail.returncode == 1
    assert "P001" in lint_fail.stdout
    assert "P003" in lint_fail.stdout
    assert "P005" in lint_fail.stdout


# ── Failing Reproduction Tests for Open QA Defects ──────────────────────


def test_qa_pr4_001_explicit_invalid_manifests_brand_channel_must_not_silently_pass(
    tmp_path: Path,
) -> None:
    """QA-PR4-001 (HIGH): Explicit --manifests-dir (missing or malformed),
    unknown --brand, or unknown --channel must fail loudly instead of silently
    skipping P001/P004 and exiting 0."""
    core_dir, manifests_dir = _make_workspace(tmp_path)
    out_dir = tmp_path / "built_b1"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_dir
    )
    # Plant a sister-brand leak so any silent skip is a real compliance bypass.
    (out_dir / "global_instruction.txt").write_text(
        "Leaked Beta Bank text.\n", encoding="utf-8"
    )

    # 1. Missing --manifests-dir must NOT exit 0.
    missing_proc = _run_cli(
        [
            "lint",
            "--app-dir",
            str(out_dir),
            "--manifests-dir",
            str(tmp_path / "does_not_exist"),
            "--rule",
            "P001,P004",
        ]
    )
    assert missing_proc.returncode != 0, (
        "QA-PR4-001: explicit missing --manifests-dir silently passed with "
        f"exit 0:\n{missing_proc.stdout}"
    )

    # 2. Unknown --brand must NOT exit 0.
    unknown_brand_proc = _run_cli(
        [
            "lint",
            "--app-dir",
            str(out_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--profile",
            "nonexistent_brand",
            "--rule",
            "P001,P004",
        ]
    )
    assert unknown_brand_proc.returncode != 0, (
        "QA-PR4-001: explicit unknown --brand silently passed with exit 0:\n"
        f"{unknown_brand_proc.stdout}"
    )

    # 3. Unknown --channel must NOT exit 0.
    unknown_chan_proc = _run_cli(
        [
            "lint",
            "--app-dir",
            str(out_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--profile",
            "brand_1",
            "--channel",
            "NONEXISTENT_CHANNEL",
            "--rule",
            "P004",
        ]
    )
    assert unknown_chan_proc.returncode != 0, (
        "QA-PR4-001: explicit unknown --channel silently passed with exit 0:\n"
        f"{unknown_chan_proc.stdout}"
    )


def test_qa_pr4_002_p002_invalid_bool_inf_nonpositive_max_tools_handled_safely(
    tmp_path: Path,
) -> None:
    """QA-PR4-002 (HIGH): P002 must not crash with OverflowError on float('inf')
    and must reject bool / non-positive (<= 0) max_tools values (falling back
    to default 20)."""
    app_dir = tmp_path / "app"
    (app_dir / "agents" / "small_agent").mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "App", "rootAgent": "small_agent"}),
        encoding="utf-8",
    )
    (app_dir / "agents" / "small_agent" / "small_agent.json").write_text(
        json.dumps({"displayName": "small_agent", "tools": ["t1", "t2"]}),
        encoding="utf-8",
    )

    for bad_cap in (float("inf"), float("-inf"), True, False, 0, -1):
        rep = collect_lint_report(
            app_dir, rule_options={"P002": {"max_tools": bad_cap}}
        )
        p002 = [r for r in rep.results if r.rule_id == "P002"]
        assert not p002, (
            f"QA-PR4-002: invalid max_tools={bad_cap!r} should fall back to "
            f"default 20 (no warning for 2-tool agent), got: {p002}"
        )


def test_qa_pr4_003_yaml_configs_enforced_by_p002_p004_p005_and_push_lock(
    tmp_path: Path,
) -> None:
    """QA-PR4-003 (HIGH): YAML-format CES apps (app.yaml, agents/*/*.yaml,
    guardrails/*/*.yaml, tools/*/*.yaml) must be checked by P002/P004/P005."""
    app_dir = tmp_path / "yaml_app"
    (app_dir / "agents" / "root_agent").mkdir(parents=True)
    (app_dir / "guardrails" / "g1").mkdir(parents=True)
    (app_dir / "tools" / "kb").mkdir(parents=True)

    (app_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "displayName": "YamlApp",
                "rootAgent": "root_agent",
                "locked": True,
                "modelSettings": {"model": "gemini-1.5-pro"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (app_dir / "agents" / "root_agent" / "root_agent.yaml").write_text(
        yaml.safe_dump(
            {
                "displayName": "root_agent",
                "tools": [f"t{i}" for i in range(21)],
                "modelSettings": {"model": "default"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (app_dir / "guardrails" / "g1" / "g1.yaml").write_text(
        yaml.safe_dump(
            {
                "displayName": "g1",
                "llmPolicy": {"modelSettings": {"model": "gemini-2.5-flash"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (app_dir / "tools" / "kb" / "kb.yaml").write_text(
        yaml.safe_dump(
            {
                "displayName": "kb",
                "dataStoreTool": {
                    "modalityConfigs": [
                        {
                            "modalityType": "TEXT",
                            "rewriterConfig": {
                                "modelSettings": {"model": "foo-latest"}
                            },
                        }
                    ]
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    rep = collect_lint_report(app_dir)
    p002 = [r for r in rep.results if r.rule_id == "P002"]
    p005 = [r for r in rep.results if r.rule_id == "P005"]
    assert len(p002) == 1, f"QA-PR4-003: P002 ignored root_agent.yaml: {p002}"
    assert len(p005) == 4, (
        f"QA-PR4-003: P005 ignored YAML model slots (expected 4): {p005}"
    )


def test_qa_pr4_004_governed_push_explicit_locked_false_unlocks_live_app(
    tmp_path: Path,
) -> None:
    """QA-PR4-004 (HIGH): When live target is locked and the bundle explicitly
    declares locked: false, a successful push must leave the app unlocked
    (not immediately re-lock it to locked=True)."""
    app_dir = tmp_path / "unlock_bundle"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "Unlocked App", "locked": False}),
        encoding="utf-8",
    )

    target_name = "projects/p/locations/us/apps/locked_target"
    apps_client = MagicMock()
    live = MagicMock()
    live.locked = True
    apps_client.get_app.return_value = live
    resp = MagicMock()
    resp.result.return_value.name = target_name
    apps_client.import_app.return_value = resp

    cli_app._app_push(
        app_dir=str(app_dir),
        apps_client=apps_client,
        target_app_name=target_name,
        identifier=target_name,
        display_name="Unlocked App",
    )

    lock_calls = [c.kwargs for c in apps_client.update_app.call_args_list]
    assert lock_calls == [{"locked": False}], (
        f"QA-PR4-004: explicit locked=false bundle re-locked live app: "
        f"{lock_calls}"
    )


def test_qa_pr4_005_malformed_metadata_and_nonlist_fields_do_not_crash(
    tmp_path: Path,
) -> None:
    """QA-PR4-005 (MEDIUM): Non-string deployments/*.yaml fields and non-list
    tools/childAgents/modalityConfigs fields must not crash P001/P004/P005."""
    app_dir = tmp_path / "app"
    man_dir = tmp_path / "manifests"
    (app_dir / "deployments").mkdir(parents=True)
    (app_dir / "agents" / "r").mkdir(parents=True)
    (app_dir / "tools" / "kb").mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "App", "rootAgent": "r"}), encoding="utf-8"
    )
    (app_dir / "deployments" / "web.yaml").write_text(
        "profileId: [brand_1]\nchannel: [WEB]\n", encoding="utf-8"
    )
    (app_dir / "agents" / "r" / "r.json").write_text(
        json.dumps({"displayName": ["r"], "tools": 123, "childAgents": 456}),
        encoding="utf-8",
    )
    (app_dir / "tools" / "kb" / "kb.json").write_text(
        json.dumps(
            {
                "displayName": "kb",
                "dataStoreTool": {"modalityConfigs": 789},
            }
        ),
        encoding="utf-8",
    )
    (man_dir / "profiles" / "brand_1").mkdir(parents=True)
    (man_dir / "profiles" / "brand_1" / "identity.yaml").write_text(
        "profile_id: brand_1\ndisplay_name: Alpha\n", encoding="utf-8"
    )
    (man_dir / "profiles" / "brand_1" / "channel_policy.yaml").write_text(
        "profile_id: brand_1\nchannels:\n  WEB:\n    disabled_tools: [x]\n",
        encoding="utf-8",
    )

    reg = build_registry()
    for rid, brand, chan in (
        ("P001", None, None),
        ("P004", "brand_1", "WEB"),
        ("P005", "brand_1", "WEB"),
    ):
        rule_obj = reg.get(rid)
        assert rule_obj is not None
        ctx = LintContext(
            project_root=app_dir,
            app_dir=app_dir,
            evals_dir=app_dir / "evals",
            app_root=app_dir,
            manifests_dir=man_dir,
            profile_id=brand,
            channel=chan,
        )
        # Must return cleanly without raising TypeError.
        rule_obj.check(app_dir / "app.json", "", ctx)


def test_qa_pr4_006_p004_catches_case_insensitive_and_space_instruction_refs(
    tmp_path: Path,
) -> None:
    """QA-PR4-006 (MEDIUM): P004 must catch lowercase ({@tool: x}, {@agent: y})
    and space-delimited ({@TOOL x}) instruction references just like S002."""
    core_dir, manifests_dir = _make_workspace(tmp_path)
    out_dir = tmp_path / "built_voice"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_dir
    )
    instr = out_dir / "agents" / "root_agent" / "instruction.txt"
    instr.write_text(
        "Try {@tool: disabled_tool}, {@TOOL disabled_tool}, "
        "and {@agent: web_only_agent}.\n",
        encoding="utf-8",
    )

    rep = collect_lint_report(out_dir, manifests_dir=manifests_dir)
    p004 = [r for r in rep.results if r.rule_id == "P004"]
    assert len(p004) == 3, (
        f"QA-PR4-006: P004 missed case-insensitive/space-delimited "
        f"instruction refs: {p004}"
    )


# ── Round 1 Re-Test Regression Tests (Verifying 18395c16 Fixes) ─────────


def test_qa_pr4_r1_yaml_app_lock_staging_and_p004_yaml_enforcement(
    tmp_path: Path,
) -> None:
    """Round 1 (QA-PR4-003): Pushing app.yaml with locked: true stages
    locked: false without mutating source app.yaml, and P004 checks YAML
    app/agent/tool configs."""
    app_dir = tmp_path / "yaml_locked"
    (app_dir / "agents" / "root_agent").mkdir(parents=True)
    (app_dir / "tools" / "disabled_tool").mkdir(parents=True)
    src_yaml = app_dir / "app.yaml"
    src_yaml.write_text(
        yaml.safe_dump(
            {
                "displayName": "YamlLocked",
                "rootAgent": "web_only_agent",
                "locked": True,
                "enableFeature": True,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (app_dir / "agents" / "root_agent" / "root_agent.yaml").write_text(
        yaml.safe_dump(
            {
                "displayName": "root_agent",
                "tools": ["disabled_tool"],
                "childAgents": ["web_only_agent"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (app_dir / "tools" / "disabled_tool" / "disabled_tool.yaml").write_text(
        yaml.safe_dump({"displayName": "disabled_tool"}, sort_keys=False),
        encoding="utf-8",
    )

    # 1. Verify P004 enforces YAML app/agent/tool configs.
    _core, manifests_dir = _make_workspace(tmp_path / "m")
    rep = collect_lint_report(
        app_dir,
        manifests_dir=manifests_dir,
        profile="brand_1",
        channel="VOICE_TELEPHONY",
    )
    p004 = [r for r in rep.results if r.rule_id == "P004"]
    assert len(p004) == 4

    # 2. Verify cxas push stages locked=false in app.yaml and preserves source.
    target_name = "projects/p/locations/us/apps/yaml1"
    apps_client = MagicMock()
    live = MagicMock()
    live.locked = False
    apps_client.get_app.return_value = live
    staged_payloads: list[dict[str, object]] = []

    def _import_app(
        *,
        app_name: str,
        app_content: bytes,
        conflict_strategy: str | None = None,
    ) -> MagicMock:
        with zipfile.ZipFile(io.BytesIO(app_content), "r") as zf:
            member = next(n for n in zf.namelist() if n.endswith("app.yaml"))
            staged_payloads.append(yaml.safe_load(zf.read(member)))
        resp = MagicMock()
        resp.result.return_value.name = app_name
        return resp

    apps_client.import_app.side_effect = _import_app
    cli_app._app_push(
        app_dir=str(app_dir),
        apps_client=apps_client,
        target_app_name=target_name,
        identifier=target_name,
        display_name="YamlLocked",
    )
    assert len(staged_payloads) == 1
    assert staged_payloads[0]["locked"] is False
    assert staged_payloads[0]["enableFeature"] is True
    assert yaml.safe_load(src_yaml.read_text("utf-8"))["locked"] is True
    assert apps_client.update_app.call_count == 1
    assert apps_client.update_app.call_args.kwargs == {"locked": True}


def test_qa_pr4_r1_explicit_locked_false_failure_still_restores_lock(
    tmp_path: Path,
) -> None:
    """Round 1 (QA-PR4-004): If import fails while pushing a locked: false
    bundle to an already-locked live app, finally MUST restore locked=True."""
    app_dir = tmp_path / "unlock_fail"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "App", "locked": False}), encoding="utf-8"
    )
    target_name = "projects/p/locations/us/apps/locked_restore"
    apps_client = MagicMock()
    live = MagicMock()
    live.locked = True
    apps_client.get_app.return_value = live
    apps_client.import_app.side_effect = RuntimeError("network drop")

    with pytest.raises(SystemExit) as exc:
        cli_app._app_push(
            app_dir=str(app_dir),
            apps_client=apps_client,
            target_app_name=target_name,
            identifier=target_name,
            display_name="App",
        )
    assert exc.value.code == 1
    assert [c.kwargs for c in apps_client.update_app.call_args_list] == [
        {"locked": False},
        {"locked": True},
    ]


def test_qa_pr4_r1_plain_app_no_flags_clean_and_broken_manifests_fails(
    tmp_path: Path,
) -> None:
    """Round 1 (QA-PR4-001): Plain non-polymorphic apps without flags produce
    zero P001/P004 findings; explicit broken --manifests-dir or --brand without
    manifests fails loudly."""
    plain_dir = tmp_path / "plain"
    (plain_dir / "agents" / "r").mkdir(parents=True)
    (plain_dir / "app.json").write_text(
        json.dumps({"displayName": "Plain", "rootAgent": "r"}),
        encoding="utf-8",
    )
    (plain_dir / "agents" / "r" / "r.json").write_text(
        json.dumps({"displayName": "r", "tools": ["end_session"]}),
        encoding="utf-8",
    )
    plain_proc = _run_cli(
        ["lint", "--app-dir", str(plain_dir), "--rule", "P001,P004"]
    )
    assert plain_proc.returncode == 0, plain_proc.stdout + plain_proc.stderr

    # Explicit --brand without manifests fails loudly.
    brand_only = _run_cli(
        [
            "lint",
            "--app-dir",
            str(plain_dir),
            "--profile",
            "brand_1",
            "--rule",
            "P001",
        ]
    )
    assert brand_only.returncode == 1

    # Explicit broken --manifests-dir fails loudly in text mode.
    broken_man = tmp_path / "broken_manifests"
    (broken_man / "brands" / "brand_1").mkdir(parents=True)
    (broken_man / "brands" / "brand_1" / "identity.yaml").write_text(
        ":::invalid_yaml:::\n  [\n", encoding="utf-8"
    )
    broken_proc = _run_cli(
        [
            "lint",
            "--app-dir",
            str(plain_dir),
            "--manifests-dir",
            str(broken_man),
            "--rule",
            "P001",
        ]
    )
    assert broken_proc.returncode == 1
    assert "Failed to load manifests" in broken_proc.stderr


def test_qa_pr4_009_lint_json_invalid_manifests_no_unbound_local_error(
    tmp_path: Path,
) -> None:
    """QA-PR4-009 (HIGH, regression in 18395c16): cxas lint --json with an
    invalid/missing --manifests-dir must emit structured SETUP JSON instead of
    crashing with UnboundLocalError on local 'json' shadowing."""
    plain_dir = tmp_path / "plain"
    (plain_dir / "agents" / "r").mkdir(parents=True)
    (plain_dir / "app.json").write_text(
        json.dumps({"displayName": "Plain", "rootAgent": "r"}),
        encoding="utf-8",
    )
    (plain_dir / "agents" / "r" / "r.json").write_text(
        json.dumps({"displayName": "r", "tools": ["end_session"]}),
        encoding="utf-8",
    )

    proc = _run_cli(
        [
            "lint",
            "--app-dir",
            str(plain_dir),
            "--manifests-dir",
            str(tmp_path / "missing_manifests"),
            "--json",
            "--rule",
            "P001",
        ]
    )
    assert proc.returncode == 1
    assert "UnboundLocalError" not in proc.stderr, (
        f"QA-PR4-009: app_lint crashed with UnboundLocalError:\n{proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert payload[0]["rule_id"] == "SETUP"


def test_qa_pr4_007_model_pins_strips_whitespace() -> None:
    """QA-PR4-007 (LOW): ModelPins strips leading/trailing whitespace."""
    pins = ModelPins.model_validate(
        {
            "agent": "  gemini-3.5-flash  ",
            "guardrail": "\tgemini-3.5-flash-lite\n",
            "data_store": " gemini-3.5-flash-lite ",
        }
    )
    assert pins.agent == "gemini-3.5-flash"
    assert pins.guardrail == "gemini-3.5-flash-lite"
    assert pins.data_store == "gemini-3.5-flash-lite"


def test_qa_pr4_008_post_import_lock_failure_not_duplicated_in_finally(
    tmp_path: Path,
) -> None:
    """QA-PR4-008 (LOW): If post-import update_app(locked=True) raises, finally
    must not redundantly call update_app(locked=True) a second time."""
    app_dir = tmp_path / "lock_fail"
    app_dir.mkdir()
    (app_dir / "app.json").write_text(
        json.dumps({"displayName": "App", "locked": True}), encoding="utf-8"
    )
    target_name = "projects/p/locations/us/apps/lock_err"
    apps_client = MagicMock()
    live = MagicMock()
    live.locked = False
    apps_client.get_app.return_value = live
    resp = MagicMock()
    resp.result.return_value.name = target_name
    apps_client.import_app.return_value = resp
    apps_client.update_app.side_effect = RuntimeError("lock rpc denied")

    with pytest.raises(SystemExit) as exc:
        cli_app._app_push(
            app_dir=str(app_dir),
            apps_client=apps_client,
            target_app_name=target_name,
            identifier=target_name,
            display_name="App",
        )
    assert exc.value.code == 1
    assert apps_client.update_app.call_count == 1
