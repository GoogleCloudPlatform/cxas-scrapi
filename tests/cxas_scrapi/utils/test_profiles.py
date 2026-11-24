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

"""Unit tests for cxas_scrapi.utils.profiles and foundry config integration."""

from __future__ import annotations

import builtins
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from cxas_scrapi.utils.profiles import (
    ProfileCycleError,
    ProfileError,
    ProfileNotFoundError,
    ProtectedProfileError,
    apply_app_overrides_in_dir,
    deep_merge,
    guard_env_profile_target_conflict,
    guard_protected_mutation,
    list_profiles,
    load_profile,
    merge_app_config_data,
    render_app_config_diff,
    render_gecx_config_diff,
    resolve_runtime_config,
    restore_overridden_app_values,
    snapshot_overridden_app_values,
    validate_profile,
)


def _write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


class TestDiscoveryAndIsolation:
    """Profile discovery order and multi-project isolation."""

    def test_explicit_file_path_takes_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        custom = _write_yaml(
            tmp_path / "custom" / "my.yml",
            {
                "target": {
                    "project_id": "explicit-proj",
                    "location": "us",
                    "app_id": "app-1",
                }
            },
        )
        prof = load_profile(str(custom))
        assert prof is not None
        assert prof.source_path == custom.resolve()
        assert prof.target.project_id == "explicit-proj"
        assert prof.source_kind == "cli"

    def test_named_profile_supports_yml_yaml_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "a.yml",
            {"target": {"project_id": "p-a"}},
        )
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "b.yaml",
            {"target": {"project_id": "p-b"}},
        )
        _write_json(
            tmp_path / ".cxas" / "profiles" / "c.json",
            {"target": {"project_id": "p-c"}},
        )

        assert load_profile("a").target.project_id == "p-a"  # type: ignore[union-attr]
        assert load_profile("b").target.project_id == "p-b"  # type: ignore[union-attr]
        assert load_profile("c").target.project_id == "p-c"  # type: ignore[union-attr]

    def test_env_var_cxas_profile_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "dev.yml",
            {"target": {"project_id": "dev-proj", "location": "us"}},
        )
        monkeypatch.setenv("CXAS_PROFILE", "dev")

        prof = load_profile(None)
        assert prof is not None
        assert prof.name == "dev"
        assert prof.source_kind == "env"
        assert prof.target.project_id == "dev-proj"

    def test_returns_none_when_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CXAS_PROFILE", raising=False)
        assert load_profile(None) is None

    def test_active_project_isolates_sibling_profiles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".agents").mkdir()
        (tmp_path / ".active-project").write_text("proj_a\n", encoding="utf-8")

        _write_json(
            tmp_path / "proj_a" / "gecx-config.json",
            {"gcp_project_id": "a-base", "deployed_app_id": "a-app"},
        )
        _write_json(
            tmp_path / "proj_b" / "gecx-config.json",
            {"gcp_project_id": "b-base", "deployed_app_id": "b-app"},
        )
        _write_yaml(
            tmp_path / "proj_a" / ".cxas" / "profiles" / "dev.yml",
            {"target": {"project_id": "proj-a-dev"}},
        )
        _write_yaml(
            tmp_path / "proj_b" / ".cxas" / "profiles" / "only_b.yml",
            {"target": {"project_id": "proj-b-only"}},
        )

        # Active project is proj_a: sees proj_a's dev, NOT proj_b's only_b.
        prof_a = load_profile("dev")
        assert prof_a is not None
        assert prof_a.target.project_id == "proj-a-dev"
        with pytest.raises(ProfileNotFoundError):
            load_profile("only_b")

        # Switching via GECX_PROJECT env var isolates to proj_b.
        monkeypatch.setenv("GECX_PROJECT", "proj_b")
        prof_b = load_profile("only_b")
        assert prof_b is not None
        assert prof_b.target.project_id == "proj-b-only"
        with pytest.raises(ProfileNotFoundError):
            load_profile("dev")


class TestInheritanceAndCycles:
    """Single inheritance (`extends`) and cycle detection."""

    def test_extends_merges_target_overrides_and_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "dev.yml",
            {
                "protected": False,
                "target": {
                    "project_id": "dev-proj",
                    "location": "europe-west2",
                    "app_id": "base-app",
                },
                "app_dir": "src/agent",
                "overrides": {
                    "gecx-config.json": {
                        "model": "gemini-2.5-flash",
                        "gcs_bucket": "gs://old-bucket",
                    },
                    "app.json": {
                        "displayName": "Dev Agent",
                        "modelSettings": {
                            "model": "gemini-2.5-flash",
                            "temperature": 0.2,
                        },
                    },
                },
            },
        )
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "plesciuc.local.yml",
            {
                "extends": "dev",
                "target": {
                    "app_id": "personal-v2",
                    "display_name": "DA - Plesciuc v2",
                },
                "overrides": {
                    "gecx-config.json": {
                        "model": "gemini-2.5-pro",
                        "gcs_bucket": None,
                    },
                    "app.json": {
                        "displayName": "DA - Plesciuc v2",
                        "modelSettings": {"model": "gemini-2.5-pro"},
                    },
                },
            },
        )

        prof = load_profile("plesciuc.local")
        assert prof is not None
        assert len(prof.chain) == 2
        assert prof.target.project_id == "dev-proj"
        assert prof.target.location == "europe-west2"
        assert prof.target.app_id == "personal-v2"
        assert prof.target.display_name == "DA - Plesciuc v2"
        assert prof.app_dir == (tmp_path / "src/agent").resolve()
        assert prof.app_overrides == {
            "displayName": "DA - Plesciuc v2",
            "modelSettings": {
                "model": "gemini-2.5-pro",
                "temperature": 0.2,
            },
        }
        gecx = prof.resolve_gecx_config({"gcs_bucket": "gs://from-base"})
        assert gecx["gcp_project_id"] == "dev-proj"
        assert gecx["location"] == "europe-west2"
        assert gecx["deployed_app_id"] == "personal-v2"
        assert gecx["model"] == "gemini-2.5-pro"
        # Child null tombstone is preserved through inheritance and deletes the
        # key from base gecx-config.json when resolved.
        assert prof.gecx_config_overrides["gcs_bucket"] is None
        assert "gcs_bucket" not in gecx

    def test_extends_cycle_detection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_yaml(tmp_path / ".cxas" / "profiles" / "a.yml", {"extends": "b"})
        _write_yaml(tmp_path / ".cxas" / "profiles" / "b.yml", {"extends": "a"})

        with pytest.raises(ProfileCycleError, match="a -> b -> a"):
            load_profile("a")


class TestGuardrailsAndValidation:
    """Protected profile guard, --to conflict, whitelist, secret hygiene."""

    def test_protected_guard_blocks_locally_and_allows_with_flag_or_ci(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CI", raising=False)
        (tmp_path / ".git").mkdir()
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "prod.yml",
            {
                "protected": True,
                "target": {
                    "project_id": "prod-proj",
                    "location": "us",
                    "app_id": "prod-app",
                },
            },
        )
        prof = load_profile("prod")
        assert prof is not None

        with pytest.raises(ProtectedProfileError, match="protected profile"):
            guard_protected_mutation(prof, command="push")
        out = capsys.readouterr().out
        assert "Profile: prod -> prod-proj/us/prod-app" in out

        # Allowed with --allow-protected
        guard_protected_mutation(prof, command="push", allow_protected=True)
        assert (
            "Profile: prod -> prod-proj/us/prod-app" in capsys.readouterr().out
        )

        # Allowed when CI=true
        monkeypatch.setenv("CI", "true")
        guard_protected_mutation(prof, command="push", allow_protected=False)
        assert (
            "Profile: prod -> prod-proj/us/prod-app" in capsys.readouterr().out
        )

    def test_env_profile_with_explicit_to_fails_fast(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "dev.yml",
            {"target": {"project_id": "p", "location": "us", "app_id": "a"}},
        )
        monkeypatch.setenv("CXAS_PROFILE", "dev")
        env_prof = load_profile(None)
        with pytest.raises(ProfileError, match="CXAS_PROFILE"):
            guard_env_profile_target_conflict(env_prof, explicit_to="other-app")

        # Explicitly passed --profile is allowed with --to
        cli_prof = load_profile("dev")
        guard_env_profile_target_conflict(cli_prof, explicit_to="other-app")

    def test_rejects_duplicate_target_keys_in_gecx_overrides(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "bad.yml",
            {
                "target": {"project_id": "p1"},
                "overrides": {
                    "gecx-config.json": {"gcp_project_id": "p2"},
                },
            },
        )
        with pytest.raises(ProfileError, match="gcp_project_id"):
            load_profile("bad")

    def test_rejects_disallowed_override_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "bad.yml",
            {"overrides": {"environment.json": {"app": {}}}},
        )
        with pytest.raises(ProfileError, match="not allowed"):
            load_profile("bad")

    def test_warns_on_secret_like_keys_but_allows_safe_tokens(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "sec.yml",
            {
                "overrides": {
                    "app.json": {
                        "api_key": "hardcoded-123",
                        "maxOutputTokens": 2048,
                    }
                }
            },
        )
        prof = load_profile("sec")
        assert prof is not None
        assert any("api_key" in w for w in prof.warnings)
        assert not any("maxOutputTokens" in w for w in prof.warnings)

    def test_env_file_path_expansion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        monkeypatch.setenv("MY_SECRETS_DIR", str(tmp_path / "sec"))
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "dev.yml",
            {"env_file": "${MY_SECRETS_DIR}/dev.env.json"},
        )
        prof = load_profile("dev")
        assert prof is not None
        assert prof.env_file == (tmp_path / "sec" / "dev.env.json").resolve()


class TestDeepMergeAndStaging:
    """Deep merge semantics, post-merge validation, app.json & app.yaml staging."""

    def test_deep_merge_null_delete_and_list_replace_warning(self) -> None:
        warnings_out: list[str] = []
        base = {
            "displayName": "Base",
            "gcs_bucket": "gs://bucket",
            "modelSettings": {"model": "old", "temperature": 0.1},
            "variableDeclarations": [{"name": "a"}, {"name": "b"}],
        }
        overlay = {
            "gcs_bucket": None,
            "modelSettings": {"model": "new"},
            "variableDeclarations": [{"name": "only_c"}],
        }
        merged = deep_merge(base, overlay, warnings_out=warnings_out)
        assert "gcs_bucket" not in merged
        assert merged["modelSettings"] == {"model": "new", "temperature": 0.1}
        assert merged["variableDeclarations"] == [{"name": "only_c"}]
        assert any("variableDeclarations" in w for w in warnings_out)

    def test_post_merge_validation_blocks_deleting_required_app_keys(
        self,
    ) -> None:
        base = {"displayName": "My App", "rootAgent": "root"}
        with pytest.raises(ProfileError, match="displayName"):
            merge_app_config_data(base, {"displayName": None})
        with pytest.raises(ProfileError, match="rootAgent"):
            merge_app_config_data(base, {"rootAgent": None})

    def test_null_tombstones_survive_single_and_inherited_profiles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_json(
            tmp_path / "app.json",
            {
                "displayName": "Base App",
                "rootAgent": "root",
                "description": "Remove me",
                "modelSettings": {
                    "model": "gemini-2.5-flash",
                    "temperature": 0.2,
                },
            },
        )
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "single.yml",
            {
                "overrides": {
                    "app.json": {
                        "description": None,
                        "modelSettings": {"temperature": None},
                    },
                    "gecx-config.json": {"gcs_bucket": None},
                }
            },
        )
        single = load_profile("single")
        assert single is not None
        assert single.app_overrides["description"] is None
        assert single.app_overrides["modelSettings"]["temperature"] is None

        staged = tmp_path / "staged_single"
        staged.mkdir()
        _write_json(
            staged / "app.json",
            json.loads((tmp_path / "app.json").read_text(encoding="utf-8")),
        )
        apply_app_overrides_in_dir(staged, single)
        merged_single = json.loads(
            (staged / "app.json").read_text(encoding="utf-8")
        )
        assert "description" not in merged_single
        assert "temperature" not in merged_single["modelSettings"]
        assert "gcs_bucket" not in single.resolve_gecx_config(
            {"gcs_bucket": "gs://base"}
        )

        # Inherited child null tombstone deleting key from base app.json
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "child.yml",
            {
                "extends": "single",
                "overrides": {
                    "app.json": {"displayName": None},
                },
            },
        )
        _, errors, _ = validate_profile("child")
        assert any("displayName" in err for err in errors)

    def test_staging_handles_app_yaml_and_optional_app_wrapper(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "dev.yml",
            {
                "overrides": {
                    "app.json": {
                        "displayName": "Wrapped Override",
                        "modelSettings": {"model": "gemini-2.5-pro"},
                    }
                }
            },
        )
        prof = load_profile("dev")
        assert prof is not None

        # app.yaml with top-level "app" wrapper
        staged = tmp_path / "staged"
        staged.mkdir()
        _write_yaml(
            staged / "app.yaml",
            {
                "app": {
                    "displayName": "Original",
                    "rootAgent": "root",
                    "modelSettings": {"model": "gemini-2.5-flash"},
                }
            },
        )
        cfg_path, _ = apply_app_overrides_in_dir(staged, prof)
        assert cfg_path == staged / "app.yaml"
        loaded = yaml.safe_load(
            (staged / "app.yaml").read_text(encoding="utf-8")
        )
        assert loaded["app"]["displayName"] == "Wrapped Override"
        assert loaded["app"]["modelSettings"]["model"] == "gemini-2.5-pro"
        assert loaded["app"]["rootAgent"] == "root"

    def test_render_diffs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_json(
            tmp_path / "app.json",
            {"displayName": "Base Name", "rootAgent": "root"},
        )
        gecx = _write_json(
            tmp_path / "gecx-config.json",
            {
                "gcp_project_id": "base-p",
                "location": "us",
                "deployed_app_id": "a",
            },
        )
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "dev.yml",
            {
                "target": {"project_id": "dev-p"},
                "overrides": {"app.json": {"displayName": "Dev Name"}},
            },
        )
        prof = load_profile("dev")
        assert prof is not None
        app_diff = render_app_config_diff(tmp_path, prof)
        assert '-  "displayName": "Base Name"' in app_diff
        assert '+  "displayName": "Dev Name"' in app_diff

        gecx_diff = render_gecx_config_diff(gecx, prof)
        assert "dev-p" in gecx_diff


class TestPullPreservationAndRuntimeConfig:
    """Safe pull --profile preservation, list/validate, and foundry config.py."""

    def test_pull_snapshot_and_restore_preserves_base_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "dev.yml",
            {
                "overrides": {
                    "app.json": {
                        "displayName": "Personal Sandbox",
                        "modelSettings": {"model": "gemini-2.5-pro"},
                    }
                }
            },
        )
        prof = load_profile("dev")
        assert prof is not None

        app_dir = tmp_path / "agent"
        _write_json(
            app_dir / "app.json",
            {
                "displayName": "Tracked Base",
                "modelSettings": {
                    "model": "gemini-2.5-flash",
                    "temperature": 0.2,
                },
            },
        )
        snap = snapshot_overridden_app_values(app_dir, prof)

        # Simulate remote pull overwriting app.json with sandbox values + a real remote edit
        _write_json(
            app_dir / "app.json",
            {
                "displayName": "Personal Sandbox",
                "modelSettings": {
                    "model": "gemini-2.5-pro",
                    "temperature": 0.5,
                },
            },
        )
        restored = restore_overridden_app_values(app_dir, snap, prof)
        assert "displayName" in restored
        assert "modelSettings.model" in restored

        final = json.loads((app_dir / "app.json").read_text(encoding="utf-8"))
        assert final["displayName"] == "Tracked Base"
        assert final["modelSettings"]["model"] == "gemini-2.5-flash"
        # Non-overridden remote edit is kept!
        assert final["modelSettings"]["temperature"] == 0.5

    def test_list_and_validate_profiles(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "dev.yml",
            {"target": {"project_id": "p", "location": "us", "app_id": "a"}},
        )
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "prod.yml",
            {
                "protected": True,
                "target": {
                    "project_id": "p",
                    "location": "us",
                    "app_id": "prod",
                },
            },
        )
        summaries = list_profiles()
        names = [s.name for s in summaries]
        assert names == ["dev", "prod"]
        assert summaries[1].protected is True

        prof, errors, _ = validate_profile("prod")
        assert prof is not None
        assert errors == []

    def test_resolve_runtime_config_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".git").mkdir()
        _write_json(
            tmp_path / "gecx-config.json",
            {
                "gcp_project_id": "base-proj",
                "location": "us",
                "deployed_app_id": "base-app",
                "modality": "text",
            },
        )
        _write_yaml(
            tmp_path / ".cxas" / "profiles" / "dev.yml",
            {
                "target": {"project_id": "prof-proj", "app_id": "prof-app"},
                "overrides": {"gecx-config.json": {"modality": "audio"}},
            },
        )

        rt = resolve_runtime_config("dev", cli_overrides={"app_id": "cli-app"})
        assert rt.project_id == "prof-proj"
        assert rt.location == "us"
        assert rt.app_id == "cli-app"
        assert rt.modality == "audio"
        assert rt.app_name == "projects/prof-proj/locations/us/apps/cli-app"

    def test_foundry_config_respects_cxas_profile_and_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        cfg_script = (
            repo_root
            / ".agents"
            / "skills"
            / "cxas-agent-foundry"
            / "scripts"
            / "config.py"
        )
        assert cfg_script.is_file()

        spec = importlib.util.spec_from_file_location(
            "foundry_config_test", cfg_script
        )
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        (tmp_path / ".agents").mkdir()
        proj = tmp_path / "myproj"
        _write_json(
            proj / "gecx-config.json",
            {
                "gcp_project_id": "base-proj",
                "location": "us",
                "deployed_app_id": "base-app",
            },
        )
        _write_yaml(
            proj / ".cxas" / "profiles" / "sandbox.yml",
            {
                "target": {
                    "project_id": "sandbox-proj",
                    "location": "europe-west2",
                    "app_id": "sandbox-app",
                },
                "overrides": {"gecx-config.json": {"modality": "audio"}},
            },
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CXAS_PROFILE", "sandbox")
        mod._project_dir = None

        cfg = mod.load_config()
        assert cfg["gcp_project_id"] == "sandbox-proj"
        assert cfg["location"] == "europe-west2"
        assert cfg["deployed_app_id"] == "sandbox-app"
        assert cfg["modality"] == "audio"
        assert (
            mod.load_app_name()
            == "projects/sandbox-proj/locations/europe-west2/apps/sandbox-app"
        )

        # Graceful fallback when cxas_scrapi cannot be imported
        real_import = builtins.__import__

        def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("cxas_scrapi"):
                raise ImportError("simulated missing cxas_scrapi")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        fallback_cfg = mod.load_config()
        assert fallback_cfg["gcp_project_id"] == "base-proj"
        assert fallback_cfg["deployed_app_id"] == "base-app"
