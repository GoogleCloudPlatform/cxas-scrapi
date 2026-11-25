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

"""Tests for per-brand overrides.yaml layering and brand_native composition."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from cxas_scrapi.build import (
    BrandOverrides,
    BuildError,
    LineReplacement,
    ManifestError,
    apply_brand_overrides,
    build_all,
    build_app,
    load_manifests,
)
from cxas_scrapi.build.overrides import (
    apply_append_rules_lines,
    apply_line_replacements_strict,
)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _make_workspace(tmp_path: Path) -> tuple[Path, Path]:
    core = tmp_path / "core"
    manifests = tmp_path / "manifests"

    _write_json(
        core / "app.json",
        {
            "displayName": "@@profile.display_name@@ App",
            "rootAgent": "root_agent",
        },
    )
    (core / "global_instruction.txt").write_text(
        "Follow @@profile.display_name@@ policies.\nKeep answers accurate.\n",
        encoding="utf-8",
    )
    _write_json(
        core / "environment.json",
        {
            "tools": {
                "order_api": {
                    "openApiTool": {
                        "url": "https://default.example.com/orders",
                    }
                }
            }
        },
    )

    _write_json(
        core / "agents" / "root_agent" / "root_agent.json",
        {
            "displayName": "root_agent",
            "description": "Shared root agent.",
            "childAgents": ["web_helper"],
            "tools": ["order_api", "kb_search", "py_helper", "web_card"],
            "modelSettings": {"model": "gemini-3.5-flash", "temperature": 0.5},
        },
    )
    (core / "agents" / "root_agent" / "instruction.txt").write_text(
        "Standard greeting.\nUse {@TOOL: order_api} when needed.\n"
        "Use {@AGENT: web_helper} for web cards.\n",
        encoding="utf-8",
    )

    _write_json(
        core / "agents" / "web_helper" / "web_helper.json",
        {
            "displayName": "web_helper",
            "description": "Web-only helper.",
            "tools": ["web_card"],
        },
    )
    (core / "agents" / "web_helper" / "instruction.txt").write_text(
        "Render rich web cards.\n", encoding="utf-8"
    )

    _write_json(
        core / "tools" / "order_api" / "order_api.json",
        {
            "displayName": "order_api",
            "openApiTool": {
                "description": "Default order lookup.",
                "openApiSchema": "tools/order_api/open_api_tool/open_api_schema.yaml",
            },
        },
    )
    oa_schema = (
        core / "tools" / "order_api" / "open_api_tool" / "open_api_schema.yaml"
    )
    oa_schema.parent.mkdir(parents=True, exist_ok=True)
    oa_schema.write_text(
        "openapi: 3.0.0\n"
        "info:\n  title: Orders\n  version: 1.0.0\n"
        "servers:\n  - url: https://default.example.com\n"
        "paths:\n  /v1/orders:\n    get:\n      summary: List orders\n",
        encoding="utf-8",
    )

    _write_json(
        core / "tools" / "kb_search" / "kb_search.json",
        {
            "displayName": "kb_search",
            "dataStoreTool": {"description": "Default KB search."},
        },
    )
    _write_json(
        core / "tools" / "py_helper" / "py_helper.json",
        {
            "displayName": "py_helper",
            "pythonFunction": {"description": "Default python helper."},
        },
    )
    _write_json(
        core / "tools" / "web_card" / "web_card.json",
        {
            "displayName": "web_card",
            "pythonFunction": {"description": "Web-only card tool."},
        },
    )

    _write_yaml(
        manifests / "profiles" / "alpha" / "identity.yaml",
        {
            "profile_id": "alpha",
            "display_name": "Alpha Retail",
            "urls": {"orders": "https://orders.alpha.example.com/v1/orders"},
        },
    )
    _write_yaml(
        manifests / "profiles" / "alpha" / "channel_policy.yaml",
        {
            "profile_id": "alpha",
            "supported_countries": ["US"],
            "channels": {
                "WEB": {
                    "entry_agent": "root_agent",
                    "supported_modalities": ["TEXT"],
                },
                "VOICE_TELEPHONY": {
                    "entry_agent": "root_agent",
                    "disabled_agents": ["web_helper"],
                    "disabled_tools": ["web_card"],
                    "supported_modalities": ["AUDIO"],
                },
            },
        },
    )
    _write_yaml(
        manifests / "envs" / "prod.yaml",
        {
            "environment": "prod",
            "templates": {
                "api": "https://api.prod.example.com/{profile}/{service}"
            },
        },
    )
    return core, manifests


# ── Stage 1-4 Override Layering ──────────────────────────────────────────


def test_overrides_all_four_stages_brand_and_channel(tmp_path: Path) -> None:
    core, manifests = _make_workspace(tmp_path)
    _write_yaml(
        manifests / "profiles" / "alpha" / "overrides.yaml",
        {
            "profile_id": "alpha",
            "global_instruction": {
                "replace_lines": [
                    {
                        "match": "Keep answers accurate.",
                        "with": "Keep @@profile.display_name@@ answers concise.",
                    }
                ],
                "append_rules": [
                    "Never share internal account IDs.",
                ],
                "channels": {
                    "VOICE_TELEPHONY": {
                        "append_rules": [
                            "Never share internal account IDs.",
                            "Speak in short sentences.",
                        ]
                    }
                },
            },
            "agents": {
                "root_agent": {
                    "description": "Root agent for @@profile.display_name@@.",
                    "temperature": 0.25,
                    "model": "gemini-3.5-pro",
                    "replace_lines": [
                        {
                            "match": "Standard greeting.",
                            "with": "Welcome to @@profile.display_name@@.",
                        },
                        {
                            "match": "Use {@AGENT: web_helper} for web cards.",
                            "with": "Assist callers directly.",
                        },
                    ],
                    "append_rules": ["Always confirm next steps."],
                    "channels": {
                        "VOICE_TELEPHONY": {
                            "temperature": 0.1,
                            "append_rules": [
                                "Pause briefly before tool calls."
                            ],
                        }
                    },
                },
                # Brand-wide override for an agent pruned on VOICE_TELEPHONY
                # must apply on WEB and be silently skipped on VOICE_TELEPHONY.
                "web_helper": {
                    "description": "Alpha web helper.",
                },
            },
            "tools": {
                "order_api": {
                    "description": "Lookup @@profile.display_name@@ orders.",
                    "url": "@@profile.urls.orders@@",
                    "channels": {
                        "VOICE_TELEPHONY": {
                            "description": "Voice order lookup for @@profile.display_name@@.",
                        }
                    },
                },
                "kb_search": {
                    "description": "Search @@profile.display_name@@ KB.",
                },
                "py_helper": {
                    "description": "Run @@profile.display_name@@ helper.",
                },
            },
        },
    )

    out_voice = tmp_path / "out_voice"
    report = build_app(
        core, manifests, "alpha", "VOICE_TELEPHONY", "prod", out_voice
    )

    # Pruned reference to web_helper was replaced in overrides.yaml -> 0 warnings!
    assert report.warnings == []

    # Stage 1: global_instruction preserves # Profile: prefix, applies replace &
    # idempotently appends rules (duplicate rule across brand+channel appears once).
    gi = (out_voice / "global_instruction.txt").read_text("utf-8")
    assert gi.startswith("# Profile: Alpha Retail\n")
    assert "Keep Alpha Retail answers concise." in gi
    assert gi.count("Never share internal account IDs.") == 1
    assert "Speak in short sentences." in gi

    # Stage 2 + Stage 4: agent config + instruction + channel temperature
    ag_cfg = json.loads(
        (out_voice / "agents" / "root_agent" / "root_agent.json").read_text(
            "utf-8"
        )
    )
    assert ag_cfg["description"] == "Root agent for Alpha Retail."
    assert ag_cfg["modelSettings"]["model"] == "gemini-3.5-pro"
    assert ag_cfg["modelSettings"]["temperature"] == pytest.approx(0.1)

    ag_instr = (
        out_voice / "agents" / "root_agent" / "instruction.txt"
    ).read_text("utf-8")
    assert "Welcome to Alpha Retail." in ag_instr
    assert "Always confirm next steps." in ag_instr
    assert "Pause briefly before tool calls." in ag_instr
    assert "web_helper" not in ag_instr

    # Stage 3 + Stage 4: tools (OpenAPI + DataStore + Python + env + schema)
    oa_cfg = json.loads(
        (out_voice / "tools" / "order_api" / "order_api.json").read_text(
            "utf-8"
        )
    )
    assert (
        oa_cfg["openApiTool"]["description"]
        == "Voice order lookup for Alpha Retail."
    )
    env_data = json.loads((out_voice / "environment.json").read_text("utf-8"))
    assert (
        env_data["tools"]["order_api"]["openApiTool"]["url"]
        == "https://orders.alpha.example.com/v1/orders"
    )
    # Path /v1/orders matches schema paths -> servers[].url gets scheme://netloc!
    oa_yaml = (
        out_voice
        / "tools"
        / "order_api"
        / "open_api_tool"
        / "open_api_schema.yaml"
    ).read_text("utf-8")
    assert "- url: https://orders.alpha.example.com" in oa_yaml

    kb_cfg = json.loads(
        (out_voice / "tools" / "kb_search" / "kb_search.json").read_text(
            "utf-8"
        )
    )
    assert kb_cfg["dataStoreTool"]["description"] == "Search Alpha Retail KB."

    py_cfg = json.loads(
        (out_voice / "tools" / "py_helper" / "py_helper.json").read_text(
            "utf-8"
        )
    )
    assert py_cfg["pythonFunction"]["description"] == "Run Alpha Retail helper."

    # Verify WEB build applies brand-wide web_helper override and WEB temperature
    out_web = tmp_path / "out_web"
    build_app(core, manifests, "alpha", "WEB", "prod", out_web)
    web_root = json.loads(
        (out_web / "agents" / "root_agent" / "root_agent.json").read_text(
            "utf-8"
        )
    )
    assert web_root["modelSettings"]["temperature"] == pytest.approx(0.25)
    web_helper = json.loads(
        (out_web / "agents" / "web_helper" / "web_helper.json").read_text(
            "utf-8"
        )
    )
    assert web_helper["description"] == "Alpha web helper."


def test_global_instruction_full_replace_preserves_identity_prefix_and_missing_core(
    tmp_path: Path,
) -> None:
    core, manifests = _make_workspace(tmp_path)
    # Remove core global_instruction.txt to test creation from overrides.yaml
    (core / "global_instruction.txt").unlink()
    _write_yaml(
        manifests / "profiles" / "alpha" / "overrides.yaml",
        {
            "profile_id": "alpha",
            "global_instruction": {
                "instruction": "Custom global instructions for @@profile.display_name@@.",
                "channels": {
                    "VOICE_TELEPHONY": {
                        "instruction": "Voice-only global instructions for @@profile.display_name@@.",
                    }
                },
            },
        },
    )

    out_web = tmp_path / "out_web"
    build_app(core, manifests, "alpha", "WEB", "prod", out_web)
    gi_web = (out_web / "global_instruction.txt").read_text("utf-8")
    assert gi_web.startswith("# Profile: Alpha Retail\n")
    assert "\n\nCustom global instructions for Alpha Retail.\n" in gi_web

    out_voice = tmp_path / "out_voice"
    build_app(core, manifests, "alpha", "VOICE_TELEPHONY", "prod", out_voice)
    gi_voice = (out_voice / "global_instruction.txt").read_text("utf-8")
    assert gi_voice.startswith("# Profile: Alpha Retail\n")
    assert "\n\nVoice-only global instructions for Alpha Retail.\n" in gi_voice


def test_openapi_url_override_preserves_env_var_placeholder_in_schema(
    tmp_path: Path,
) -> None:
    core, manifests = _make_workspace(tmp_path)
    oa_schema = (
        core / "tools" / "order_api" / "open_api_tool" / "open_api_schema.yaml"
    )
    oa_schema.write_text(
        "openapi: 3.0.0\nservers:\n  - url: $env_var\npaths:\n  /v1/orders: {}\n",
        encoding="utf-8",
    )
    _write_yaml(
        manifests / "profiles" / "alpha" / "overrides.yaml",
        {
            "profile_id": "alpha",
            "tools": {
                "order_api": {
                    "url": "https://orders.alpha.example.com/v1/orders"
                }
            },
        },
    )

    out = tmp_path / "out"
    build_app(core, manifests, "alpha", "WEB", "prod", out)
    schema_after = (
        out / "tools" / "order_api" / "open_api_tool" / "open_api_schema.yaml"
    ).read_text("utf-8")
    assert "- url: $env_var" in schema_after
    env_after = json.loads((out / "environment.json").read_text("utf-8"))
    assert (
        env_after["tools"]["order_api"]["openApiTool"]["url"]
        == "https://orders.alpha.example.com/v1/orders"
    )


def test_yaml_agent_and_tool_configs_supported_in_overrides(
    tmp_path: Path,
) -> None:
    core, manifests = _make_workspace(tmp_path)
    # Replace root_agent.json with root_agent.yaml
    (core / "agents" / "root_agent" / "root_agent.json").unlink()
    _write_yaml(
        core / "agents" / "root_agent" / "root_agent.yaml",
        {
            "displayName": "root_agent",
            "description": "YAML root",
            "modelSettings": {"model": "gemini-3.5-flash"},
        },
    )
    _write_yaml(
        manifests / "profiles" / "alpha" / "overrides.yaml",
        {
            "profile_id": "alpha",
            "agents": {
                "root_agent": {
                    "description": "Overridden YAML root",
                    "model": "gemini-3.5-pro",
                    "temperature": 0.3,
                }
            },
        },
    )

    out = tmp_path / "out"
    build_app(core, manifests, "alpha", "WEB", "prod", out)
    cfg = yaml.safe_load(
        (out / "agents" / "root_agent" / "root_agent.yaml").read_text("utf-8")
    )
    assert cfg["description"] == "Overridden YAML root"
    assert cfg["modelSettings"]["model"] == "gemini-3.5-pro"
    assert cfg["modelSettings"]["temperature"] == pytest.approx(0.3)


# ── Strict Replacement, Idempotency & Security Validations ───────────────


def test_replace_lines_strict_missing_and_ambiguous_fail() -> None:
    rep_missing = [
        LineReplacement(match="Nonexistent anchor", replace_with="X")
    ]
    with pytest.raises(BuildError, match="match not found"):
        apply_line_replacements_strict(
            "Line A\nLine B\n",
            rep_missing,
            render_fn=lambda s: s,
            context_label="test",
        )

    rep_ambig = [LineReplacement(match="dup", replace_with="new")]
    with pytest.raises(BuildError, match="ambiguous"):
        apply_line_replacements_strict(
            "dup\ndup\n",
            rep_ambig,
            render_fn=lambda s: s,
            context_label="test",
        )

    # Unique substring replacement preserves surrounding text
    rep_sub = [LineReplacement(match="old-token", replace_with="new-token")]
    res = apply_line_replacements_strict(
        "prefix old-token suffix\n",
        rep_sub,
        render_fn=lambda s: s,
        context_label="test",
    )
    assert res == "prefix new-token suffix\n"


def test_append_rules_is_idempotent() -> None:
    base = "Rule 0\nRule 1\n"
    rules = ["Rule 1", "Rule 2", "Rule 2", "Rule 3"]
    once = apply_append_rules_lines(base, rules, render_fn=lambda s: s)
    twice = apply_append_rules_lines(once, rules, render_fn=lambda s: s)
    assert once == "Rule 0\nRule 1\nRule 2\nRule 3\n"
    assert twice == once


@pytest.mark.parametrize(
    "bad_id",
    ["..", ".", "../escape", "a/b", "a\\b", "a:b", " padded ", ""],
)
def test_override_resource_id_path_traversal_rejected(bad_id: str) -> None:
    with pytest.raises(ValueError, match="invalid resource id"):
        BrandOverrides.model_validate(
            {"profile_id": "alpha", "agents": {bad_id: {"description": "x"}}}
        )
    with pytest.raises(ValueError, match="invalid resource id"):
        BrandOverrides.model_validate(
            {"profile_id": "alpha", "tools": {bad_id: {"description": "x"}}}
        )


def test_overrides_manifest_validation_errors(tmp_path: Path) -> None:
    _core, manifests = _make_workspace(tmp_path)
    ov_path = manifests / "profiles" / "alpha" / "overrides.yaml"

    # Malformed YAML
    ov_path.write_text("profile_id: [unclosed\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="invalid YAML"):
        load_manifests(manifests)

    # Brand ID mismatch with directory
    _write_yaml(ov_path, {"profile_id": "other"})
    with pytest.raises(ManifestError, match="does not match directory name"):
        load_manifests(manifests)

    # Unknown channel key
    _write_yaml(
        ov_path,
        {
            "profile_id": "alpha",
            "global_instruction": {"channels": {"BOGUS_CHANNEL": {}}},
        },
    )
    with pytest.raises(ManifestError, match="unknown channel"):
        load_manifests(manifests)

    # Boolean temperature rejected
    _write_yaml(
        ov_path,
        {
            "profile_id": "alpha",
            "agents": {"root_agent": {"temperature": True}},
        },
    )
    with pytest.raises(ManifestError, match="boolean"):
        load_manifests(manifests)

    # Unknown extra field rejected
    _write_yaml(ov_path, {"profile_id": "alpha", "unknown_field": 123})
    with pytest.raises(ManifestError):
        load_manifests(manifests)


def test_channel_override_targeting_pruned_or_unknown_resource_fails(
    tmp_path: Path,
) -> None:
    core, manifests = _make_workspace(tmp_path)

    # Unknown agent
    _write_yaml(
        manifests / "profiles" / "alpha" / "overrides.yaml",
        {
            "profile_id": "alpha",
            "agents": {"nonexistent_agent": {"description": "x"}},
        },
    )
    with pytest.raises(BuildError, match="unknown agent 'nonexistent_agent'"):
        build_app(core, manifests, "alpha", "WEB", "prod", tmp_path / "o1")

    # Channel override targeting disabled agent on VOICE_TELEPHONY
    _write_yaml(
        manifests / "profiles" / "alpha" / "overrides.yaml",
        {
            "profile_id": "alpha",
            "agents": {
                "web_helper": {
                    "channels": {"VOICE_TELEPHONY": {"description": "oops"}}
                }
            },
        },
    )
    with pytest.raises(BuildError, match="targets disabled agent 'web_helper'"):
        build_app(
            core, manifests, "alpha", "VOICE_TELEPHONY", "prod", tmp_path / "o2"
        )

    # URL override on non-OpenAPI tool fails loudly
    _write_yaml(
        manifests / "profiles" / "alpha" / "overrides.yaml",
        {
            "profile_id": "alpha",
            "tools": {"py_helper": {"url": "https://example.com"}},
        },
    )
    with pytest.raises(BuildError, match="not an openApiTool"):
        build_app(core, manifests, "alpha", "WEB", "prod", tmp_path / "o3")


def test_overrides_reject_openapi_schema_path_escape(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    outside = tmp_path / "outside.yaml"
    outside.write_text("servers:\n  - url: https://secret\n", encoding="utf-8")
    _write_json(
        out_dir / "tools" / "order_api" / "order_api.json",
        {
            "displayName": "order_api",
            "openApiTool": {
                "description": "Orders",
                "openApiSchema": "../outside.yaml",
            },
        },
    )
    ov = BrandOverrides.model_validate(
        {
            "profile_id": "alpha",
            "tools": {"order_api": {"url": "https://evil.example.com"}},
        }
    )
    with pytest.raises(BuildError, match=r"escapes output directory|outside"):
        apply_brand_overrides(
            out_dir=out_dir,
            brand_id="alpha",
            channel="WEB",
            permitted_channels={"WEB"},
            ov=ov,
            render_fn=lambda s: s,
            known_agents=set(),
            known_tools={"order_api"},
        )


# ── composition_mode: brand_native ───────────────────────────────────────


def _make_brand_native_workspace(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "workspace"
    manifests = ws / "manifests"

    # Brand-owned domain tree under workspace/brands/beta/domains/
    beta_domains = ws / "profiles" / "beta" / "domains"
    _write_json(
        beta_domains / "app.json",
        {
            "displayName": "@@profile.display_name@@ Native",
            "rootAgent": "beta_concierge",
        },
    )
    (beta_domains / "global_instruction.txt").write_text(
        "Brand-native global guidance for @@profile.display_name@@.\n",
        encoding="utf-8",
    )
    _write_json(
        beta_domains / "agents" / "beta_concierge" / "beta_concierge.json",
        {
            "displayName": "beta_concierge",
            "childAgents": ["shared_escalation", "web_chat_only"],
            "tools": ["shared_audit_tool"],
        },
    )
    (beta_domains / "agents" / "beta_concierge" / "instruction.txt").write_text(
        "Welcome to @@profile.display_name@@ on @@build.channel@@.\n"
        "{@startChannel_WEB}Show web banner.{@endChannel_WEB}\n"
        "{@startChannel_VOICE_TELEPHONY}Speak clearly.{@endChannel_VOICE_TELEPHONY}\n",
        encoding="utf-8",
    )
    # Brand-owned override of a shared trait agent (brand MUST win!)
    _write_json(
        beta_domains
        / "agents"
        / "shared_escalation"
        / "shared_escalation.json",
        {
            "displayName": "shared_escalation",
            "description": "Brand-owned escalation config.",
        },
    )
    (
        beta_domains / "agents" / "shared_escalation" / "instruction.txt"
    ).write_text(
        "Beta custom escalation flow for @@profile.display_name@@.\n",
        encoding="utf-8",
    )

    # Shared traits under workspace/traits/ (named trait packs)
    traits_esc = ws / "traits" / "escalations"
    _write_json(
        traits_esc / "agents" / "shared_escalation" / "shared_escalation.json",
        {
            "displayName": "shared_escalation",
            "description": "Generic trait escalation config.",
        },
    )
    (
        traits_esc / "agents" / "shared_escalation" / "instruction.txt"
    ).write_text("Generic trait escalation flow.\n", encoding="utf-8")
    _write_json(
        traits_esc / "agents" / "web_chat_only" / "web_chat_only.json",
        {"displayName": "web_chat_only", "description": "Trait web agent."},
    )
    (traits_esc / "agents" / "web_chat_only" / "instruction.txt").write_text(
        "Trait web chat instructions.\n", encoding="utf-8"
    )

    traits_audit = ws / "traits" / "compliance"
    _write_json(
        traits_audit / "tools" / "shared_audit_tool" / "shared_audit_tool.json",
        {
            "displayName": "shared_audit_tool",
            "pythonFunction": {
                "description": "Audit tool for @@profile.display_name@@."
            },
        },
    )

    _write_yaml(
        manifests / "profiles" / "beta" / "identity.yaml",
        {
            "profile_id": "beta",
            "display_name": "Beta Services",
            "composition_mode": "brand_native",
        },
    )
    _write_yaml(
        manifests / "profiles" / "beta" / "channel_policy.yaml",
        {
            "profile_id": "beta",
            "supported_countries": ["US"],
            "channels": {
                "WEB": {
                    "entry_agent": "beta_concierge",
                    "supported_modalities": ["TEXT"],
                },
                "VOICE_TELEPHONY": {
                    "entry_agent": "beta_concierge",
                    "disabled_agents": ["web_chat_only"],
                    "supported_modalities": ["AUDIO"],
                },
            },
        },
    )
    _write_yaml(
        manifests / "profiles" / "beta" / "overrides.yaml",
        {
            "profile_id": "beta",
            "agents": {
                "beta_concierge": {
                    "append_rules": ["Always sign off politely."]
                }
            },
        },
    )
    _write_yaml(manifests / "envs" / "prod.yaml", {"environment": "prod"})
    return ws, manifests


def test_brand_native_composition_brand_wins_traits_prune_and_overrides(
    tmp_path: Path,
) -> None:
    ws, manifests = _make_brand_native_workspace(tmp_path)
    out = tmp_path / "dist_voice"

    report = build_app(ws, manifests, "beta", "VOICE_TELEPHONY", "prod", out)
    assert report.composition_mode == "brand_native"

    # Brand-owned agent + conditional rendering + overrides.yaml applied
    concierge_instr = (
        out / "agents" / "beta_concierge" / "instruction.txt"
    ).read_text("utf-8")
    assert "Welcome to Beta Services on VOICE_TELEPHONY." in concierge_instr
    assert "Speak clearly." in concierge_instr
    assert "Show web banner." not in concierge_instr
    assert "Always sign off politely." in concierge_instr

    # Brand-owned shared_escalation WINS over traits/escalations version!
    esc_instr = (
        out / "agents" / "shared_escalation" / "instruction.txt"
    ).read_text("utf-8")
    assert "Beta custom escalation flow for Beta Services." in esc_instr
    assert "Generic trait" not in esc_instr

    # Shared trait tool merged and token-rendered
    audit_cfg = json.loads(
        (
            out / "tools" / "shared_audit_tool" / "shared_audit_tool.json"
        ).read_text("utf-8")
    )
    assert (
        audit_cfg["pythonFunction"]["description"]
        == "Audit tool for Beta Services."
    )

    # Channel pruning removed trait-provided web_chat_only on VOICE_TELEPHONY
    assert not (out / "agents" / "web_chat_only").exists()
    concierge_cfg = json.loads(
        (out / "agents" / "beta_concierge" / "beta_concierge.json").read_text(
            "utf-8"
        )
    )
    assert "web_chat_only" not in concierge_cfg["childAgents"]

    # Deployment YAML emitted
    dep = yaml.safe_load(
        (out / "deployments" / "voice-telephony.yaml").read_text("utf-8")
    )
    assert dep["profileId"] == "beta"
    assert dep["channel"] == "VOICE_TELEPHONY"


def test_brand_native_missing_dirs_and_symlinks_rejected(
    tmp_path: Path,
) -> None:
    core, manifests = _make_workspace(tmp_path)

    # Neither brands/alpha/domains nor traits exists -> BuildError
    with pytest.raises(BuildError, match="brand_native"):
        build_app(
            core,
            manifests,
            "alpha",
            "WEB",
            "prod",
            tmp_path / "out_missing",
            composition_mode="brand_native",
        )

    # Invalid composition_mode rejected
    with pytest.raises(BuildError, match="Unknown composition_mode"):
        build_app(
            core,
            manifests,
            "alpha",
            "WEB",
            "prod",
            tmp_path / "out_bad_mode",
            composition_mode="bogus_mode",  # type: ignore[arg-type]
        )

    # Symlink inside brand_native domains rejected
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    domains = core / "profiles" / "alpha" / "domains"
    domains.mkdir(parents=True)
    os.symlink(secret, domains / "leaked.txt")
    with pytest.raises(BuildError, match="Symlinks are not permitted"):
        build_app(
            core,
            manifests,
            "alpha",
            "WEB",
            "prod",
            tmp_path / "out_sym",
            composition_mode="brand_native",
        )


def test_build_all_mixed_composition_modes_and_cli_flag(tmp_path: Path) -> None:
    ws, manifests = _make_brand_native_workspace(tmp_path)
    # Add canonical_overlay brand (alpha) using ws as core_dir
    _write_json(
        ws / "app.json",
        {
            "displayName": "@@profile.display_name@@ Canonical",
            "rootAgent": "r1",
        },
    )
    _write_json(
        ws / "agents" / "r1" / "r1.json",
        {"displayName": "r1"},
    )
    (ws / "agents" / "r1" / "instruction.txt").write_text(
        "Canonical @@profile.display_name@@.\n", encoding="utf-8"
    )
    _write_yaml(
        manifests / "profiles" / "alpha" / "identity.yaml",
        {"profile_id": "alpha", "display_name": "Alpha Canonical"},
    )
    _write_yaml(
        manifests / "profiles" / "alpha" / "channel_policy.yaml",
        {
            "profile_id": "alpha",
            "supported_countries": ["US"],
            "channels": {"WEB": {"entry_agent": "r1"}},
        },
    )

    reports = build_all(ws, manifests, tmp_path / "all_dist", "prod")
    modes = {(r.profile_id, r.channel): r.composition_mode for r in reports}
    assert modes[("alpha", "WEB")] == "canonical_overlay"
    assert modes[("beta", "WEB")] == "brand_native"
    assert modes[("beta", "VOICE_TELEPHONY")] == "brand_native"

    # CLI --composition-mode flag works end-to-end
    cli_out = tmp_path / "cli_out"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "src"}
    repo_root = Path(__file__).resolve().parents[3]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "cxas_scrapi.cli.main",
            "build",
            "--core-dir",
            str(ws),
            "--manifests-dir",
            str(manifests),
            "--profile",
            "beta",
            "--channel",
            "WEB",
            "--env",
            "prod",
            "--composition-mode",
            "brand_native",
            "--out",
            str(cli_out),
            "--json",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["composition_mode"] == "brand_native"
    assert (cli_out / "agents" / "beta_concierge" / "instruction.txt").is_file()
