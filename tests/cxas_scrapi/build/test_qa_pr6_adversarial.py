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

"""Adversarial E2E and edge-case QA tests for PR 6 (overrides & brand_native).

Contains:
- Passing adversarial edge-case & CLI E2E regression tests (``test_qa_pr6_...``).
- Failing reproduction tests for open QA defects (``test_qa_pr6_001_...``
  through ``test_qa_pr6_008_...``), which must turn green once fixed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

from cxas_scrapi.build.builder import BuildError, build_app
from cxas_scrapi.build.manifests import ManifestError, load_manifests

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


def _make_pr6_workspace(tmp_path: Path) -> tuple[Path, Path]:
    core_dir = tmp_path / "core"
    (core_dir / "agents" / "root_agent").mkdir(parents=True)
    (core_dir / "agents" / "web_only_agent").mkdir(parents=True)
    (core_dir / "tools" / "order_api" / "open_api_tool").mkdir(parents=True)
    (core_dir / "tools" / "kb_store").mkdir(parents=True)
    (core_dir / "tools" / "calc_fn" / "python_function").mkdir(parents=True)

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
    (core_dir / "environment.json").write_text(
        json.dumps(
            {
                "region": "us-central1",
                "tools": {
                    "order_api": {
                        "openApiTool": {"url": "https://old-env.example.com/v1"}
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "global_instruction.txt").write_text(
        "Base global instruction for @@profile.display_name@@.\n",
        encoding="utf-8",
    )
    (core_dir / "agents" / "root_agent" / "root_agent.json").write_text(
        json.dumps(
            {
                "displayName": "root_agent",
                "instruction": "agents/root_agent/instruction.txt",
                "childAgents": ["web_only_agent"],
                "tools": ["order_api", "kb_store", "calc_fn"],
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "Standard greeting.\nCost ($100.00) [a-z]+.* applies.\n",
        encoding="utf-8",
    )
    (core_dir / "agents" / "web_only_agent" / "web_only_agent.json").write_text(
        json.dumps({"displayName": "web_only_agent"}),
        encoding="utf-8",
    )
    (core_dir / "tools" / "order_api" / "order_api.json").write_text(
        json.dumps(
            {
                "displayName": "order_api",
                "openApiTool": {
                    "description": "Generic orders API",
                    "openApiSchema": (
                        "tools/order_api/open_api_tool/open_api_schema.yaml"
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    (
        core_dir
        / "tools"
        / "order_api"
        / "open_api_tool"
        / "open_api_schema.yaml"
    ).write_text(
        "openapi: 3.0.0\n"
        "info:\n"
        "  title: Orders\n"
        "  version: '1.0'\n"
        "servers:\n"
        "  - url: https://old.example.com\n"
        "paths:\n"
        "  /v1/orders:\n"
        "    get:\n"
        "      summary: List orders\n",
        encoding="utf-8",
    )
    (core_dir / "tools" / "kb_store" / "kb_store.json").write_text(
        json.dumps(
            {
                "displayName": "kb_store",
                "dataStoreTool": {"description": "Generic KB"},
            }
        ),
        encoding="utf-8",
    )
    (core_dir / "tools" / "calc_fn" / "calc_fn.json").write_text(
        json.dumps(
            {
                "displayName": "calc_fn",
                "pythonFunction": {"description": "Generic calc"},
            }
        ),
        encoding="utf-8",
    )
    (
        core_dir / "tools" / "calc_fn" / "python_function" / "python_code.py"
    ).write_text("def calc_fn() -> int:\n    return 1\n", encoding="utf-8")

    manifests_dir = tmp_path / "manifests"
    b1 = manifests_dir / "profiles" / "brand_1"
    b1.mkdir(parents=True)
    (b1 / "identity.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "display_name": "Brand One",
                "legal_entity": "Brand One Corp",
                "lexicon": {"account": "Checking"},
                "urls": {"support": "https://support.brand1.example.com"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (b1 / "channel_policy.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "supported_countries": ["US"],
                "channels": {
                    "WEB": {"supported_modalities": ["TEXT"]},
                    "VOICE_TELEPHONY": {
                        "disabled_agents": ["web_only_agent"],
                        "disabled_tools": ["kb_store"],
                        "supported_modalities": ["AUDIO"],
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    envs_dir = manifests_dir / "envs"
    envs_dir.mkdir(parents=True)
    (envs_dir / "prod.yaml").write_text(
        yaml.safe_dump(
            {
                "environment": "prod",
                "templates": {
                    "api": "https://api.example.com/{profile_slug}/{env}/{service}"
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return core_dir, manifests_dir


# ── Passing Adversarial Edge-Case & E2E Tests ───────────────────────────


@pytest.mark.parametrize(
    "bad_yaml",
    [
        ":\n  - [unclosed",
        "- not_a_mapping\n",
        "profile_id: wrong_brand\n",
        "profile_id: brand_1\nunknown_top_key: 1\n",
        "profile_id: brand_1\nagents:\n  root_agent:\n    add_tools: [x]\n",
        "profile_id: brand_1\nagents:\n  root_agent:\n    temperature: .nan\n",
        "profile_id: brand_1\nagents:\n  root_agent:\n    temperature: .inf\n",
        "profile_id: brand_1\nagents:\n  root_agent:\n    temperature: -.inf\n",
        "profile_id: brand_1\nagents:\n  root_agent:\n    temperature: true\n",
        "profile_id: brand_1\nagents:\n  root_agent:\n    temperature: -0.1\n",
        "profile_id: brand_1\nagents:\n  root_agent:\n    temperature: 2.1\n",
        "profile_id: brand_1\nagents:\n  root_agent:\n    model: '   '\n",
        "profile_id: brand_1\nagents:\n  123: {}\n",
    ],
)
def test_qa_pr6_overrides_manifest_schema_rejections(
    tmp_path: Path, bad_yaml: str
) -> None:
    """Verify malformed YAML, NaN/Inf/bool temp, and extra fields fail."""
    _, manifests_dir = _make_pr6_workspace(tmp_path)
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        bad_yaml, encoding="utf-8"
    )
    with pytest.raises(ManifestError):
        load_manifests(manifests_dir)


def test_qa_pr6_replace_lines_regex_metachars_multiline_and_delete(
    tmp_path: Path,
) -> None:
    """Verify regex metachars, multiline replace, line deletion, ambiguity."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    (core_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "Line A\n"
        "Cost ($100.00) [a-z]+.* applies.\n"
        "Block start\n"
        "Block end\n"
        "Delete me.\n",
        encoding="utf-8",
    )
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "agents": {
                    "root_agent": {
                        "replace_lines": [
                            {
                                "match": "Cost ($100.00) [a-z]+.* applies.",
                                "with": r"Fee ($50.00) \1 for @@profile.display_name@@.",
                            },
                            {
                                "match": "Block start\nBlock end",
                                "with": "Merged block",
                            },
                            {
                                "match": "Delete me.",
                                "with": "",
                            },
                        ]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_rep"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)
    rendered = (
        out_dir / "agents" / "root_agent" / "instruction.txt"
    ).read_text("utf-8")
    assert rendered == (
        "Line A\n"
        r"Fee ($50.00) \1 for Brand One."
        "\nMerged block\n"
    )

    # Multi-occurrence anchor must raise BuildError
    (core_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "Repeat\nRepeat\n", encoding="utf-8"
    )
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "agents": {
                    "root_agent": {
                        "replace_lines": [{"match": "Repeat", "with": "Once"}]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="ambiguous"):
        build_app(
            core_dir, manifests_dir, "brand_1", "WEB", "prod", tmp_path / "o2"
        )


def test_qa_pr6_append_rules_trailing_newline_multiline_and_idempotency(
    tmp_path: Path,
) -> None:
    """Verify append_rules handles missing trailing newline & deduplication."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    # Intentionally omit trailing newline
    (core_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "Existing line\nMulti 1\nMulti 2", encoding="utf-8"
    )
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "agents": {
                    "root_agent": {
                        "append_rules": [
                            "Existing line",
                            "Multi 1\nMulti 2",
                            "{@startChannel_VOICE_TELEPHONY}\n"
                            "Voice only\n"
                            "{@endChannel_VOICE_TELEPHONY}",
                            "New rule for @@profile.display_name@@.",
                            "New rule for @@profile.display_name@@.",
                        ]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_append"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)
    rendered = (
        out_dir / "agents" / "root_agent" / "instruction.txt"
    ).read_text("utf-8")
    assert rendered == (
        "Existing line\nMulti 1\nMulti 2\nNew rule for Brand One.\n"
    )


def test_qa_pr6_overrides_tokens_conditionals_modalities_and_pruned_warnings(
    tmp_path: Path,
) -> None:
    """Verify tokens, conditionals, modalities, and pruned warnings in overrides."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "agents": {
                    "root_agent": {
                        "description": "Agent for @@env.api:orders@@",
                        "append_rules": [
                            "{@startChannel_VOICE_TELEPHONY}\n"
                            "Never route to {@AGENT: web_only_agent} or "
                            "{@TOOL: kb_store}.\n"
                            "{@endChannel_VOICE_TELEPHONY}",
                        ],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_voice"
    rep = build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_dir
    )
    ag_cfg = json.loads(
        (out_dir / "agents" / "root_agent" / "root_agent.json").read_text(
            "utf-8"
        )
    )
    assert ag_cfg["description"] == (
        "Agent for https://api.example.com/brand-1/prod/orders"
    )
    assert any(
        "mentions pruned agent web_only_agent" in w for w in rep.warnings
    )
    assert any("mentions pruned tool kb_store" in w for w in rep.warnings)

    # Dangling token in override must fail loudly
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "global_instruction": {
                    "append_rules": ["Broken @@profile.nonexistent"]
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="Unresolved"):
        build_app(
            core_dir, manifests_dir, "brand_1", "WEB", "prod", tmp_path / "o_d"
        )

    # Unbalanced modality tag introduced by override must fail loudly
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "agents": {
                    "root_agent": {
                        "append_rules": ["{@startModality_Voice}\nUnclosed"]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="Unbalanced modality"):
        build_app(
            core_dir, manifests_dir, "brand_1", "WEB", "prod", tmp_path / "o_m"
        )


def test_qa_pr6_datastore_and_python_tool_overrides_and_non_openapi_url(
    tmp_path: Path,
) -> None:
    """Verify dataStoreTool & pythonFunction descriptions and url rejection."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "tools": {
                    "kb_store": {
                        "description": "KB for @@profile.display_name@@",
                        "channels": {
                            "WEB": {
                                "description": "Web KB for @@profile.profile_id@@"
                            }
                        },
                    },
                    "calc_fn": {
                        "description": "Calc for @@profile.display_name@@",
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_tools"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)
    kb = json.loads(
        (out_dir / "tools" / "kb_store" / "kb_store.json").read_text("utf-8")
    )
    calc = json.loads(
        (out_dir / "tools" / "calc_fn" / "calc_fn.json").read_text("utf-8")
    )
    assert kb["dataStoreTool"]["description"] == "Web KB for brand_1"
    assert calc["pythonFunction"]["description"] == "Calc for Brand One"

    # Setting url on a dataStoreTool must raise BuildError
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "tools": {
                    "kb_store": {"url": "https://not-openapi.example.com"}
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(BuildError, match="not an openApiTool"):
        build_app(
            core_dir, manifests_dir, "brand_1", "WEB", "prod", tmp_path / "o_u"
        )


def test_qa_pr6_brand_native_precedence_packs_flat_and_missing_dirs(
    tmp_path: Path,
) -> None:
    """Verify brand_native precedence (brand > pack_a > pack_b) & flat traits."""
    ws = tmp_path / "ws"
    domains = ws / "profiles" / "brand_1" / "domains"
    (domains / "agents" / "root_agent").mkdir(parents=True)
    (domains / "app.json").write_text(
        json.dumps(
            {
                "displayName": "@@profile.display_name@@",
                "rootAgent": "root_agent",
            }
        ),
        encoding="utf-8",
    )
    (domains / "agents" / "root_agent" / "root_agent.json").write_text(
        json.dumps({"displayName": "root_agent"}), encoding="utf-8"
    )
    (domains / "agents" / "root_agent" / "instruction.txt").write_text(
        "Brand-owned root.\n", encoding="utf-8"
    )

    # Two named trait packs defining overlapping shared_agent & root_agent
    pack_a = ws / "traits" / "pack_a"
    (pack_a / "agents" / "root_agent").mkdir(parents=True)
    (pack_a / "agents" / "root_agent" / "instruction.txt").write_text(
        "Pack A root (must lose to brand).\n", encoding="utf-8"
    )
    (pack_a / "agents" / "shared_agent").mkdir(parents=True)
    (pack_a / "agents" / "shared_agent" / "shared_agent.json").write_text(
        json.dumps({"displayName": "shared_agent"}), encoding="utf-8"
    )
    (pack_a / "agents" / "shared_agent" / "instruction.txt").write_text(
        "Pack A shared (wins over pack_b).\n", encoding="utf-8"
    )

    pack_b = ws / "traits" / "pack_b"
    (pack_b / "agents" / "shared_agent").mkdir(parents=True)
    (pack_b / "agents" / "shared_agent" / "instruction.txt").write_text(
        "Pack B shared (must lose to pack_a).\n", encoding="utf-8"
    )
    (pack_b / "agents" / "pack_b_only").mkdir(parents=True)
    (pack_b / "agents" / "pack_b_only" / "pack_b_only.json").write_text(
        json.dumps({"displayName": "pack_b_only"}), encoding="utf-8"
    )

    manifests = ws / "manifests"
    b1 = manifests / "profiles" / "brand_1"
    b1.mkdir(parents=True)
    (b1 / "identity.yaml").write_text(
        "profile_id: brand_1\ndisplay_name: Brand Native\n"
        "composition_mode: brand_native\n",
        encoding="utf-8",
    )
    (b1 / "channel_policy.yaml").write_text(
        "profile_id: brand_1\nchannels:\n  WEB: {}\n", encoding="utf-8"
    )
    (manifests / "envs").mkdir(parents=True)
    (manifests / "envs" / "prod.yaml").write_text(
        "environment: prod\n", encoding="utf-8"
    )

    out_dir = tmp_path / "out_native"
    rep = build_app(ws, manifests, "brand_1", "WEB", "prod", out_dir)
    assert rep.composition_mode == "brand_native"
    assert (out_dir / "agents" / "root_agent" / "instruction.txt").read_text(
        "utf-8"
    ) == "Brand-owned root.\n"
    assert (out_dir / "agents" / "shared_agent" / "instruction.txt").read_text(
        "utf-8"
    ) == "Pack A shared (wins over pack_b).\n"
    assert (out_dir / "agents" / "pack_b_only").is_dir()

    # Traits-only (no brands/<id>/domains) with flat CES traits layout
    ws_flat = tmp_path / "ws_flat"
    (ws_flat / "traits" / "agents" / "flat_agent").mkdir(parents=True)
    (ws_flat / "traits" / "app.json").write_text(
        json.dumps(
            {
                "displayName": "@@profile.display_name@@",
                "rootAgent": "flat_agent",
            }
        ),
        encoding="utf-8",
    )
    (
        ws_flat / "traits" / "agents" / "flat_agent" / "flat_agent.json"
    ).write_text(json.dumps({"displayName": "flat_agent"}), encoding="utf-8")
    m_flat = ws_flat / "manifests"
    (m_flat / "profiles" / "brand_1").mkdir(parents=True)
    (m_flat / "profiles" / "brand_1" / "identity.yaml").write_text(
        "profile_id: brand_1\ndisplay_name: Brand Flat\n"
        "composition_mode: brand_native\n",
        encoding="utf-8",
    )
    (m_flat / "profiles" / "brand_1" / "channel_policy.yaml").write_text(
        "profile_id: brand_1\nchannels:\n  WEB: {}\n", encoding="utf-8"
    )
    (m_flat / "envs").mkdir(parents=True)
    (m_flat / "envs" / "prod.yaml").write_text(
        "environment: prod\n", encoding="utf-8"
    )
    out_flat = tmp_path / "out_flat"
    build_app(ws_flat, m_flat, "brand_1", "WEB", "prod", out_flat)
    assert (out_flat / "agents" / "flat_agent").is_dir()

    # Both domains and traits missing must raise BuildError
    ws_empty = tmp_path / "ws_empty"
    m_empty = ws_empty / "manifests"
    (m_empty / "profiles" / "brand_1").mkdir(parents=True)
    (m_empty / "profiles" / "brand_1" / "identity.yaml").write_text(
        "profile_id: brand_1\ndisplay_name: Brand Empty\n"
        "composition_mode: brand_native\n",
        encoding="utf-8",
    )
    (m_empty / "profiles" / "brand_1" / "channel_policy.yaml").write_text(
        "profile_id: brand_1\nchannels:\n  WEB: {}\n", encoding="utf-8"
    )
    (m_empty / "envs").mkdir(parents=True)
    (m_empty / "envs" / "prod.yaml").write_text(
        "environment: prod\n", encoding="utf-8"
    )
    with pytest.raises(BuildError, match="requires"):
        build_app(ws_empty, m_empty, "brand_1", "WEB", "prod", tmp_path / "o")


def test_qa_pr6_brand_native_symlinks_and_overlap_preserve_out_dir(
    tmp_path: Path,
) -> None:
    """Verify brand_native symlink and overlap checks fail before rmtree."""
    ws = tmp_path / "ws"
    domains = ws / "profiles" / "brand_1" / "domains"
    domains.mkdir(parents=True)
    (domains / "app.json").write_text(
        json.dumps({"rootAgent": "a1"}), encoding="utf-8"
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET", encoding="utf-8")
    (ws / "traits").mkdir(parents=True)
    (ws / "traits" / "evil_pack").symlink_to(outside)

    manifests = tmp_path / "manifests"
    b1 = manifests / "profiles" / "brand_1"
    b1.mkdir(parents=True)
    (b1 / "identity.yaml").write_text(
        "profile_id: brand_1\ndisplay_name: B1\ncomposition_mode: brand_native\n",
        encoding="utf-8",
    )
    (b1 / "channel_policy.yaml").write_text(
        "profile_id: brand_1\nchannels:\n  WEB: {}\n", encoding="utf-8"
    )
    (manifests / "envs").mkdir(parents=True)
    (manifests / "envs" / "prod.yaml").write_text(
        "environment: prod\n", encoding="utf-8"
    )

    existing_out = tmp_path / "existing_out"
    existing_out.mkdir()
    marker = existing_out / "keep.txt"
    marker.write_text("do_not_delete", encoding="utf-8")
    with pytest.raises(BuildError, match="Symlinks are not permitted"):
        build_app(ws, manifests, "brand_1", "WEB", "prod", existing_out)
    assert marker.is_file()

    # Remove symlink and test out_dir overlapping traits/ outside core_dir
    (ws / "traits" / "evil_pack").unlink()
    ext_traits = tmp_path / "traits"
    ext_traits.mkdir()
    (ext_traits / "app.json").write_text("{}", encoding="utf-8")
    ws_clean = tmp_path / "ws_clean"
    ws_clean.mkdir()
    with pytest.raises(BuildError, match="must not overlap"):
        build_app(
            ws_clean, manifests, "brand_1", "WEB", "prod", ext_traits / "sub"
        )
    assert (ext_traits / "app.json").is_file()


def test_qa_pr6_cli_e2e_brand_native_mixed_build_all_zip_and_bad_mode(
    tmp_path: Path,
) -> None:
    """Verify real CLI subprocesses for brand_native, mixed --all, and errors."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)

    # Add brand_2 configured as brand_native with its own domains/
    b2 = manifests_dir / "profiles" / "brand_2"
    b2.mkdir(parents=True)
    (b2 / "identity.yaml").write_text(
        "profile_id: brand_2\ndisplay_name: Brand Two\n"
        "composition_mode: brand_native\n",
        encoding="utf-8",
    )
    (b2 / "channel_policy.yaml").write_text(
        "profile_id: brand_2\nchannels:\n  WEB: {}\n", encoding="utf-8"
    )
    b2_domains = b2 / "domains"
    (b2_domains / "agents" / "b2_root").mkdir(parents=True)
    (b2_domains / "app.json").write_text(
        json.dumps(
            {"displayName": "@@profile.display_name@@", "rootAgent": "b2_root"}
        ),
        encoding="utf-8",
    )
    (b2_domains / "agents" / "b2_root" / "b2_root.json").write_text(
        json.dumps({"displayName": "b2_root"}), encoding="utf-8"
    )
    (b2_domains / "agents" / "b2_root" / "instruction.txt").write_text(
        "Hello from @@profile.display_name@@.\n", encoding="utf-8"
    )

    out_all = tmp_path / "out_mixed"
    res_all = _run_cli(
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--env",
            "prod",
            "--out",
            str(out_all),
            "--all",
            "--zip",
            "--json",
        ]
    )
    assert res_all.returncode == 0, res_all.stderr
    reports = json.loads(res_all.stdout)
    modes = {
        (r["profile_id"], r["channel"]): r["composition_mode"] for r in reports
    }
    assert modes[("brand_1", "WEB")] == "canonical_overlay"
    assert modes[("brand_2", "WEB")] == "brand_native"

    # Deterministic ZIP across two sequential brand_native builds
    out_z1 = tmp_path / "bn_z1"
    out_z2 = tmp_path / "bn_z2"
    for out_z in (out_z1, out_z2):
        r = _run_cli(
            [
                "build",
                "--core-dir",
                str(core_dir),
                "--manifests-dir",
                str(manifests_dir),
                "--profile",
                "brand_2",
                "--channel",
                "WEB",
                "--env",
                "prod",
                "--composition-mode",
                "brand_native",
                "--out",
                str(out_z),
                "--zip",
                "--json",
            ]
        )
        assert r.returncode == 0, r.stderr
    z1 = out_z1 / "brand_2_web.zip"
    z2 = out_z2 / "brand_2_web.zip"
    assert hashlib.sha256(z1.read_bytes()).hexdigest() == (
        hashlib.sha256(z2.read_bytes()).hexdigest()
    )
    with zipfile.ZipFile(z1, "r") as zf:
        assert "agents/b2_root/instruction.txt" in zf.namelist()

    # Invalid --composition-mode fails cleanly without Python traceback
    res_bad = _run_cli(
        [
            "build",
            "--core-dir",
            str(core_dir),
            "--manifests-dir",
            str(manifests_dir),
            "--profile",
            "brand_1",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--composition-mode",
            "unknown_mode",
            "--out",
            str(tmp_path / "bad_out"),
        ]
    )
    assert res_bad.returncode != 0
    assert "Traceback" not in res_bad.stderr


def test_qa_pr6_scope_hygiene_no_downstream_imports() -> None:
    """Verify PR 6 decoupled modules do not depend on PR 3/4/5/7/8 modules."""
    build_dir = _REPO_ROOT / "src" / "cxas_scrapi" / "build"
    pr6_modules = ("overrides.py", "errors.py", "manifests.py")
    forbidden = ("drift", "python_bundler", "polymorphic", "backport")
    for fname in pr6_modules:
        py_file = build_dir / fname
        src = py_file.read_text("utf-8")
        for mod in forbidden:
            assert mod not in src, f"Unexpected reference '{mod}' in {py_file}"


# ── Open Bug Reproduction Tests (Expected to FAIL until fixed) ──────────


def test_qa_pr6_001_canonical_overlay_rejects_symlinks_in_core_before_rmtree(
    tmp_path: Path,
) -> None:
    """QA-PR6-001 (HIGH): canonical_overlay must reject symlinks in core_dir."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    secret = tmp_path / "secret_outside.txt"
    secret.write_text("TOP_SECRET_DATA\n", encoding="utf-8")
    instr = core_dir / "agents" / "root_agent" / "instruction.txt"
    instr.unlink()
    instr.symlink_to(secret)

    existing_out = tmp_path / "existing_out"
    existing_out.mkdir()
    marker = existing_out / "keep.txt"
    marker.write_text("preserve_me", encoding="utf-8")

    with pytest.raises(BuildError, match=r"(?i)symlink"):
        build_app(
            core_dir, manifests_dir, "brand_1", "WEB", "prod", existing_out
        )
    assert marker.is_file()


@pytest.mark.parametrize(
    "quoted_line",
    ['  - url: "$env_var"', "  - url: '$env_var'"],
)
def test_qa_pr6_002_openapi_url_override_preserves_quoted_env_var_and_tool_placeholder(
    tmp_path: Path, quoted_line: str
) -> None:
    """QA-PR6-002 (HIGH): Quoted $env_var in schema & tool config preserved."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    (core_dir / "tools" / "order_api" / "order_api.json").write_text(
        json.dumps(
            {
                "displayName": "order_api",
                "openApiTool": {
                    "description": "Orders",
                    "url": "$env_var",
                    "openApiSchema": (
                        "tools/order_api/open_api_tool/open_api_schema.yaml"
                    ),
                },
            }
        ),
        encoding="utf-8",
    )
    (
        core_dir
        / "tools"
        / "order_api"
        / "open_api_tool"
        / "open_api_schema.yaml"
    ).write_text(
        f"openapi: 3.0.0\nservers:\n{quoted_line}\npaths:\n  /v1/orders: {{}}\n",
        encoding="utf-8",
    )
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "tools": {
                    "order_api": {"url": "https://brand1.example.com/v1"}
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_env_var"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)

    env_cfg = json.loads((out_dir / "environment.json").read_text("utf-8"))
    assert (
        env_cfg["tools"]["order_api"]["openApiTool"]["url"]
        == "https://brand1.example.com/v1"
    )
    tool_cfg = json.loads(
        (out_dir / "tools" / "order_api" / "order_api.json").read_text("utf-8")
    )
    assert tool_cfg["openApiTool"]["url"] == "$env_var"

    schema_doc = yaml.safe_load(
        (
            out_dir
            / "tools"
            / "order_api"
            / "open_api_tool"
            / "open_api_schema.yaml"
        ).read_text("utf-8")
    )
    assert schema_doc["servers"][0]["url"] == "$env_var"


def test_qa_pr6_003_openapi_url_override_multiline_servers_and_unrelated_urls(
    tmp_path: Path,
) -> None:
    """QA-PR6-003 (HIGH): Update multiline servers[].url; leave other - url:."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    (
        core_dir
        / "tools"
        / "order_api"
        / "open_api_tool"
        / "open_api_schema.yaml"
    ).write_text(
        "openapi: 3.0.0\n"
        "servers:\n"
        "  - description: Main\n"
        "    url: https://old.example.com\n"
        "paths:\n"
        "  /v1/orders:\n"
        "    get:\n"
        "      x-links:\n"
        "        - url: https://docs.unrelated.example.com\n",
        encoding="utf-8",
    )
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "tools": {
                    "order_api": {"url": "https://brand1.example.com/v1"}
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_multiline_oa"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)
    schema_doc = yaml.safe_load(
        (
            out_dir
            / "tools"
            / "order_api"
            / "open_api_tool"
            / "open_api_schema.yaml"
        ).read_text("utf-8")
    )
    assert schema_doc["servers"][0]["url"] == "https://brand1.example.com/v1"
    assert (
        schema_doc["paths"]["/v1/orders"]["get"]["x-links"][0]["url"]
        == "https://docs.unrelated.example.com"
    )


def test_qa_pr6_004_yaml_token_substitution_preserves_valid_yaml_syntax(
    tmp_path: Path,
) -> None:
    """QA-PR6-004 (HIGH): Special brand chars must not emit broken .yaml."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    (core_dir / "agents" / "root_agent" / "root_agent.json").unlink()
    (core_dir / "agents" / "root_agent" / "root_agent.yaml").write_text(
        'displayName: root_agent\ndescription: "Welcome to @@profile.display_name@@"\n',
        encoding="utf-8",
    )
    tricky_name = 'Acme: "Prime" Bank\nLine 2'
    (manifests_dir / "profiles" / "brand_1" / "identity.yaml").write_text(
        yaml.safe_dump(
            {"profile_id": "brand_1", "display_name": tricky_name},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_yaml_esc"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)
    parsed = yaml.safe_load(
        (out_dir / "agents" / "root_agent" / "root_agent.yaml").read_text(
            "utf-8"
        )
    )
    assert isinstance(parsed, dict)
    assert parsed["description"] == f"Welcome to {tricky_name}"


def test_qa_pr6_005_agent_override_respects_custom_instruction_filename(
    tmp_path: Path,
) -> None:
    """QA-PR6-005 (HIGH): Agent overrides must honor custom instruction path."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    (core_dir / "agents" / "root_agent" / "instruction.txt").unlink()
    (core_dir / "agents" / "root_agent" / "custom_prompt.txt").write_text(
        "Standard greeting.\n", encoding="utf-8"
    )
    (core_dir / "agents" / "root_agent" / "root_agent.json").write_text(
        json.dumps(
            {
                "displayName": "root_agent",
                "instruction": "agents/root_agent/custom_prompt.txt",
            }
        ),
        encoding="utf-8",
    )
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "agents": {
                    "root_agent": {
                        "replace_lines": [
                            {
                                "match": "Standard greeting.",
                                "with": "Custom greeting for @@profile.display_name@@.",
                            }
                        ]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_custom_instr"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)
    assert (out_dir / "agents" / "root_agent" / "custom_prompt.txt").read_text(
        "utf-8"
    ) == "Custom greeting for Brand One.\n"
    assert not (out_dir / "agents" / "root_agent" / "instruction.txt").exists()


def test_qa_pr6_006_global_instruction_identity_block_dedup_and_preservation(
    tmp_path: Path,
) -> None:
    """QA-PR6-006 (MEDIUM): Identity block not duplicated or dropped."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)

    # Case 1: core has no token, override places @@profile.identity_block@@ mid-doc
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "global_instruction": {
                    "instruction": "HEADER\n@@profile.identity_block@@\nFOOTER\n"
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    out1 = tmp_path / "out_mid"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out1)
    gi1 = (out1 / "global_instruction.txt").read_text("utf-8")
    assert gi1.count("# Profile: Brand One") == 1
    assert gi1.startswith("HEADER\n")

    # Case 2: core had @@profile.identity_block@@, override omits it -> preserved
    (core_dir / "global_instruction.txt").write_text(
        "HEADER\n@@profile.identity_block@@\nBase.\n", encoding="utf-8"
    )
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "global_instruction": {"instruction": "Brand override body."},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    out2 = tmp_path / "out_keep"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out2)
    gi2 = (out2 / "global_instruction.txt").read_text("utf-8")
    assert gi2.count("# Profile: Brand One") == 1
    assert "Brand override body." in gi2


def test_qa_pr6_007_yaml_agent_disabled_by_display_name_is_pruned(
    tmp_path: Path,
) -> None:
    """QA-PR6-007 (MEDIUM): YAML agents disabled by displayName must be pruned."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    (core_dir / "agents" / "yaml_sub").mkdir(parents=True)
    (core_dir / "agents" / "yaml_sub" / "yaml_sub.yaml").write_text(
        "displayName: YamlSubDisplay\n", encoding="utf-8"
    )
    (manifests_dir / "profiles" / "brand_1" / "channel_policy.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "channels": {
                    "WEB": {"disabled_agents": ["YamlSubDisplay"]},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_yaml_prune"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)
    assert not (out_dir / "agents" / "yaml_sub").exists()


def test_qa_pr6_008_replace_lines_rejects_whitespace_only_match(
    tmp_path: Path,
) -> None:
    """QA-PR6-008 (LOW): Whitespace-only replace_lines.match must be rejected."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    (core_dir / "agents" / "root_agent" / "instruction.txt").write_text(
        "Hello   world.\n", encoding="utf-8"
    )
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "agents": {
                    "root_agent": {
                        "replace_lines": [{"match": "   ", "with": " "}]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises((ManifestError, BuildError)):
        build_app(
            core_dir,
            manifests_dir,
            "brand_1",
            "WEB",
            "prod",
            tmp_path / "out_ws",
        )


# ── Round 1 Re-Test Regressions (Introduced in 3439b009) ────────────────


def test_qa_pr6_009_openapi_url_override_handles_yaml_safe_dump_indent0_servers(
    tmp_path: Path,
) -> None:
    """QA-PR6-009 (HIGH): OpenAPI url override works on indent-0 servers list."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    # Adding a @@profile.*@@ token in info.title causes builder.py Step 4 to
    # rewrite open_api_schema.yaml via yaml.safe_dump, which emits indent-0
    # sequence items (`servers:\n- url: ...`).
    (
        core_dir
        / "tools"
        / "order_api"
        / "open_api_tool"
        / "open_api_schema.yaml"
    ).write_text(
        "openapi: 3.0.0\n"
        "info:\n"
        '  title: "@@profile.display_name@@ Orders"\n'
        "  version: '1.0'\n"
        "servers:\n"
        "  - url: https://old.example.com\n"
        "paths:\n"
        "  /v1/orders: {}\n",
        encoding="utf-8",
    )
    (manifests_dir / "profiles" / "brand_1" / "overrides.yaml").write_text(
        yaml.safe_dump(
            {
                "profile_id": "brand_1",
                "tools": {
                    "order_api": {"url": "https://brand1.example.com/v1"}
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_indent0_oa"
    build_app(core_dir, manifests_dir, "brand_1", "WEB", "prod", out_dir)
    schema_doc = yaml.safe_load(
        (
            out_dir
            / "tools"
            / "order_api"
            / "open_api_tool"
            / "open_api_schema.yaml"
        ).read_text("utf-8")
    )
    assert schema_doc["info"]["title"] == "Brand One Orders"
    assert schema_doc["servers"][0]["url"] == "https://brand1.example.com/v1"


def test_qa_pr6_010_yaml_agent_without_tokens_prunes_child_agents_and_tools(
    tmp_path: Path,
) -> None:
    """QA-PR6-010 (MEDIUM): Tokenless YAML agents still prune childAgents/tools."""
    core_dir, manifests_dir = _make_pr6_workspace(tmp_path)
    (core_dir / "agents" / "root_agent" / "root_agent.json").unlink()
    (core_dir / "agents" / "root_agent" / "root_agent.yaml").write_text(
        "displayName: root_agent\n"
        "childAgents:\n"
        "  - web_only_agent\n"
        "tools:\n"
        "  - kb_store\n"
        "  - order_api\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out_yaml_prune_refs"
    build_app(
        core_dir, manifests_dir, "brand_1", "VOICE_TELEPHONY", "prod", out_dir
    )
    ag_cfg = yaml.safe_load(
        (out_dir / "agents" / "root_agent" / "root_agent.yaml").read_text(
            "utf-8"
        )
    )
    assert ag_cfg["childAgents"] == []
    assert ag_cfg["tools"] == ["order_api"]
