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

"""Unit tests for shared sync and push top-level entry warnings."""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest

from cxas_scrapi.cli import app as cli_app
from cxas_scrapi.cli.main import get_parser
from cxas_scrapi.utils.shared_sync import sync_shared

if TYPE_CHECKING:
    from pathlib import Path


def test_find_ignored_top_level_entries_filters_valid_and_metadata(
    tmp_path: Path,
) -> None:
    """Valid CXAS roots and standard repo metadata should not be flagged."""
    (tmp_path / "app.yaml").write_text("name: test\n", encoding="utf-8")
    (tmp_path / "agents").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)

    # Standard repo/tooling metadata
    for meta_dir in (
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    ):
        (tmp_path / meta_dir).mkdir()
    for meta_file in (
        ".gitignore",
        ".DS_Store",
        "README.md",
        "cxaslint.yaml",
        "Dockerfile",
        "cxas_types.pyi",
    ):
        (tmp_path / meta_file).write_text("", encoding="utf-8")

    assert (
        cli_app._find_ignored_top_level_entries(
            tmp_path, cli_app.VALID_APP_ROOTS
        )
        == []
    )

    # Add unknown top-level directories/files
    (tmp_path / "shared").mkdir()
    (tmp_path / "custom_scripts").mkdir()
    (tmp_path / "notes.txt").write_text("local notes", encoding="utf-8")

    ignored = cli_app._find_ignored_top_level_entries(
        tmp_path, cli_app.VALID_APP_ROOTS
    )
    assert ignored == ["custom_scripts", "notes.txt", "shared"]


def test_app_push_warns_on_ignored_top_level_entries(
    tmp_path: Path, capsys: Any
) -> None:
    """_app_push emits a warning when unknown top-level directories exist."""
    (tmp_path / "app.yaml").write_text("name: test\n", encoding="utf-8")
    (tmp_path / "shared").mkdir()
    (tmp_path / "custom_scripts").mkdir()
    (tmp_path / "README.md").write_text("# Doc\n", encoding="utf-8")

    mock_apps_client = mock.MagicMock()
    mock_lro = mock.MagicMock()
    mock_imported = mock.MagicMock()
    mock_imported.name = "projects/p/locations/us/apps/a1"
    mock_imported.warnings = []
    mock_lro.result.return_value = mock_imported
    mock_apps_client.import_as_new_app.return_value = mock_lro

    cli_app._app_push(
        app_dir=str(tmp_path),
        apps_client=mock_apps_client,
        display_name="Test App",
    )

    captured = capsys.readouterr()
    assert "Warning: The following top-level entries in " in captured.out
    assert "custom_scripts, shared" in captured.out
    assert "README.md" not in captured.out


def test_callback_directive_sync_and_check_drift(tmp_path: Path) -> None:
    """# @cxas-shared: <rel_path> synchronizes callbacks and detects drift."""
    shared_cb = tmp_path / "shared" / "callbacks" / "auth_guard.py"
    shared_cb.parent.mkdir(parents=True)
    shared_cb.write_text(
        "def before_model(callback_context, llm_request):\n    return None\n",
        encoding="utf-8",
    )

    target_cb = (
        tmp_path
        / "agents"
        / "billing_agent"
        / "before_model_callbacks"
        / "auth_guard"
        / "python_code.py"
    )
    target_cb.parent.mkdir(parents=True)
    target_cb.write_text(
        "# @cxas-shared: shared/callbacks/auth_guard.py\n"
        "def before_model(callback_context, llm_request):\n"
        "    return 'stale'\n",
        encoding="utf-8",
    )

    # Check mode detects drift without modifying the file on disk
    check_report = sync_shared(tmp_path, check=True)
    assert not check_report.in_sync
    assert check_report.drifted == [
        "agents/billing_agent/before_model_callbacks/auth_guard/python_code.py"
    ]
    assert "return 'stale'" in target_cb.read_text(encoding="utf-8")

    # Sync mode updates the target callback with the header + canonical content
    sync_report = sync_shared(tmp_path, check=False)
    assert sync_report.in_sync
    assert sync_report.updated == [
        "agents/billing_agent/before_model_callbacks/auth_guard/python_code.py"
    ]
    expected_text = (
        "# @cxas-shared: shared/callbacks/auth_guard.py\n"
        "def before_model(callback_context, llm_request):\n"
        "    return None\n"
    )
    assert target_cb.read_text(encoding="utf-8") == expected_text

    # Subsequent check and sync are idempotent and report in_sync
    recheck_report = sync_shared(tmp_path, check=True)
    assert recheck_report.in_sync
    assert recheck_report.drifted == []
    assert recheck_report.updated == []


def test_instruction_include_sync_and_round_trip_idempotency(
    tmp_path: Path,
) -> None:
    """<!-- @cxas-include: ... --> synchronizes blocks idempotently."""
    tone_file = tmp_path / "shared" / "instructions" / "tone.txt"
    tone_file.parent.mkdir(parents=True)
    tone_file.write_text(
        "Always greet the caller warmly.\nKeep answers under two sentences.\n",
        encoding="utf-8",
    )

    instr_file = tmp_path / "agents" / "root_agent" / "instruction.txt"
    instr_file.parent.mkdir(parents=True)
    instr_file.write_text(
        "<role>You are a support agent.</role>\n"
        "<!-- @cxas-include: shared/instructions/tone.txt -->\n"
        "Old tone instruction\n"
        "<!-- @cxas-end-include -->\n"
        "<taskflow>Help the caller.</taskflow>\n",
        encoding="utf-8",
    )

    # 1. Check mode detects drift
    check_report = sync_shared(tmp_path, check=True)
    assert not check_report.in_sync
    assert check_report.drifted == ["agents/root_agent/instruction.txt"]

    # 2. First sync updates the block while preserving start/end markers
    report_1 = sync_shared(tmp_path, check=False)
    assert report_1.in_sync
    assert report_1.updated == ["agents/root_agent/instruction.txt"]

    synced_content = instr_file.read_text(encoding="utf-8")
    assert (
        "<!-- @cxas-include: shared/instructions/tone.txt -->\n"
        "Always greet the caller warmly.\n"
        "Keep answers under two sentences.\n"
        "<!-- @cxas-end-include -->"
    ) in synced_content

    # 3. Second sync (simulating round-trip after cxas pull) is a no-op
    report_2 = sync_shared(tmp_path, check=False)
    assert report_2.in_sync
    assert report_2.updated == []
    assert instr_file.read_text(encoding="utf-8") == synced_content


def test_manifest_yaml_target_materialization(tmp_path: Path) -> None:
    """shared/manifest.yaml materializes targets across multiple agents."""
    shared_dir = tmp_path / "shared"
    (shared_dir / "callbacks").mkdir(parents=True)
    source_cb = shared_dir / "callbacks" / "common_before_model.py"
    source_cb.write_text(
        "def common_cb(ctx, req):\n    pass\n",
        encoding="utf-8",
    )

    manifest = shared_dir / "manifest.yaml"
    manifest.write_text(
        "mappings:\n"
        "  - source: shared/callbacks/common_before_model.py\n"
        "    targets:\n"
        "      - agents/agent_a/before_model_callbacks/cb/python_code.py\n"
        "      - agents/agent_b/before_model_callbacks/cb/python_code.py\n",
        encoding="utf-8",
    )

    # Check mode reports missing targets as out-of-sync
    check_report = sync_shared(tmp_path, check=True)
    assert not check_report.in_sync
    assert set(check_report.drifted) == {
        "agents/agent_a/before_model_callbacks/cb/python_code.py",
        "agents/agent_b/before_model_callbacks/cb/python_code.py",
    }

    # Sync mode creates both target files with the # @cxas-shared header
    sync_report = sync_shared(tmp_path, check=False)
    assert sync_report.in_sync
    assert len(sync_report.updated) == 2

    for agent_name in ("agent_a", "agent_b"):
        target = (
            tmp_path
            / "agents"
            / agent_name
            / "before_model_callbacks"
            / "cb"
            / "python_code.py"
        )
        assert target.is_file()
        assert target.read_text(encoding="utf-8") == (
            "# @cxas-shared: shared/callbacks/common_before_model.py\n"
            "def common_cb(ctx, req):\n    pass\n"
        )


@pytest.mark.parametrize(
    "malicious_rel_path",
    [
        "../outside_secret.py",
        "shared/../../outside_secret.py",
        "/etc/passwd",
    ],
)
def test_path_traversal_protection_rejects_escaping_paths(
    tmp_path: Path, malicious_rel_path: str
) -> None:
    """Paths that escape app_dir via '..' or absolute paths are rejected."""
    app_dir = tmp_path / "my_app"
    target_cb = (
        app_dir
        / "agents"
        / "agent_a"
        / "before_model_callbacks"
        / "cb"
        / "python_code.py"
    )
    target_cb.parent.mkdir(parents=True)
    target_cb.write_text(
        f"# @cxas-shared: {malicious_rel_path}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid shared path"):
        sync_shared(app_dir, check=True)


def test_path_traversal_protection_in_instruction_and_manifest(
    tmp_path: Path,
) -> None:
    """Instruction includes and manifest targets also reject '..' escapes."""
    app_dir = tmp_path / "my_app"
    instr = app_dir / "agents" / "agent_a" / "instruction.txt"
    instr.parent.mkdir(parents=True)
    instr.write_text(
        "<!-- @cxas-include: ../secret.txt -->\n<!-- @cxas-end-include -->\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid shared path"):
        sync_shared(app_dir, check=False)

    # Remove bad instruction and test manifest target escape
    instr.unlink()
    shared_cb = app_dir / "shared" / "cb.py"
    shared_cb.parent.mkdir(parents=True)
    shared_cb.write_text("x = 1\n", encoding="utf-8")
    (app_dir / "shared" / "manifest.yaml").write_text(
        "mappings:\n"
        "  - source: shared/cb.py\n"
        "    targets:\n"
        "      - ../escaped_target.py\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid shared path"):
        sync_shared(app_dir, check=False)


def test_cli_sync_shared_parser_and_exit_code(
    tmp_path: Path, capsys: Any
) -> None:
    """CLI 'cxas sync-shared --check --json' exits 1 on drift and 0 in sync."""
    shared_cb = tmp_path / "shared" / "cb.py"
    shared_cb.parent.mkdir(parents=True)
    shared_cb.write_text("def run():\n    return 42\n", encoding="utf-8")

    target_cb = tmp_path / "agents" / "a1" / "cb.py"
    target_cb.parent.mkdir(parents=True)
    target_cb.write_text(
        "# @cxas-shared: shared/cb.py\ndef run():\n    return 0\n",
        encoding="utf-8",
    )

    parser = get_parser()
    check_args = parser.parse_args(
        ["sync-shared", "--app-dir", str(tmp_path), "--check", "--json"]
    )
    with pytest.raises(SystemExit) as excinfo:
        check_args.func(check_args)
    assert excinfo.value.code == 1

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["in_sync"] is False
    assert payload["drifted"] == ["agents/a1/cb.py"]

    # Now run without --check to materialize
    sync_args = parser.parse_args(
        ["sync-shared", "--app-dir", str(tmp_path), "--json"]
    )
    sync_args.func(sync_args)
    synced_payload = json.loads(capsys.readouterr().out)
    assert synced_payload["in_sync"] is True
    assert synced_payload["updated"] == ["agents/a1/cb.py"]

    # Re-run --check; should succeed without SystemExit
    check_args_after = argparse.Namespace(
        app_dir=str(tmp_path), check=True, json_output=False
    )
    cli_app.app_sync_shared(check_args_after)
    assert "in sync" in capsys.readouterr().out


def test_app_push_warns_on_drifted_shared_targets_and_sync_shared_flag(
    tmp_path: Path, capsys: Any
) -> None:
    """_app_push warns when shared targets have drifted or syncs with flag."""
    (tmp_path / "app.yaml").write_text("name: test\n", encoding="utf-8")
    shared_cb = tmp_path / "shared" / "cb.py"
    shared_cb.parent.mkdir(parents=True)
    shared_cb.write_text("def run():\n    return 'fresh'\n", encoding="utf-8")

    target_cb = tmp_path / "agents" / "a1" / "cb.py"
    target_cb.parent.mkdir(parents=True)
    target_cb.write_text(
        "# @cxas-shared: shared/cb.py\ndef run():\n    return 'stale'\n",
        encoding="utf-8",
    )

    mock_apps_client = mock.MagicMock()
    mock_lro = mock.MagicMock()
    mock_imported = mock.MagicMock()
    mock_imported.name = "projects/p/locations/us/apps/a1"
    mock_imported.warnings = []
    mock_lro.result.return_value = mock_imported
    mock_apps_client.import_as_new_app.return_value = mock_lro

    # Without --sync-shared, _app_push prints non-blocking drift warning
    cli_app._app_push(
        app_dir=str(tmp_path),
        apps_client=mock_apps_client,
        display_name="Test App",
    )
    out_1 = capsys.readouterr().out
    assert "are out of sync with their canonical sources" in out_1
    assert "agents/a1/cb.py" in out_1

    # With --sync-shared, _app_push materializes before packaging
    cli_app._app_push(
        app_dir=str(tmp_path),
        apps_client=mock_apps_client,
        display_name="Test App",
        args=argparse.Namespace(sync_shared=True),
    )
    out_2 = capsys.readouterr().out
    assert "Synchronized 1 shared target(s) before push" in out_2
    assert "return 'fresh'" in target_cb.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# QA Adversarial & Reproduction Tests (PR 3)
# ---------------------------------------------------------------------------


def test_qa_flat_and_nested_callback_layouts_and_multiple_includes(
    tmp_path: Path,
) -> None:
    """QA: Flat and nested callback layouts and multi-block includes sync idempotently."""
    (tmp_path / "shared" / "callbacks").mkdir(parents=True)
    (tmp_path / "shared" / "instructions").mkdir(parents=True)

    (tmp_path / "shared" / "callbacks" / "cb.py").write_text(
        "def before_model(ctx, req):\n    return None\n",
        encoding="utf-8",
    )
    (tmp_path / "shared" / "instructions" / "tone.txt").write_text(
        "Be concise.", encoding="utf-8"
    )
    (tmp_path / "shared" / "instructions" / "safety.txt").write_text(
        "Never share secrets.", encoding="utf-8"
    )

    # Flat callback layout: agents/<agent>/before_model_callbacks_01/python_code.py
    flat_cb = (
        tmp_path
        / "agents"
        / "agent_flat"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    flat_cb.parent.mkdir(parents=True)
    flat_cb.write_text(
        "# comment line 1\n# @cxas-shared: shared/callbacks/cb.py\n",
        encoding="utf-8",
    )

    # Nested callback layout: agents/<agent>/before_model_callbacks/<cb>/python_code.py
    nested_cb = (
        tmp_path
        / "agents"
        / "agent_nested"
        / "before_model_callbacks"
        / "cb_01"
        / "python_code.py"
    )
    nested_cb.parent.mkdir(parents=True)
    nested_cb.write_text(
        "# @cxas-shared: ./shared/callbacks/cb.py\n",
        encoding="utf-8",
    )

    # Multiple includes in global_instruction.txt
    global_instr = tmp_path / "global_instruction.txt"
    global_instr.write_text(
        "Header\n"
        "<!-- @cxas-include: shared/instructions/tone.txt -->\n"
        "old tone\n"
        "<!-- @cxas-end-include -->\n"
        "Middle\n"
        "<!-- @cxas-include: shared/instructions/safety.txt -->\n"
        "old safety\n"
        "<!-- @cxas-end-include -->\n"
        "Footer\n",
        encoding="utf-8",
    )

    rep1 = sync_shared(tmp_path, check=False)
    assert rep1.in_sync
    assert len(rep1.updated) == 3

    rep2 = sync_shared(tmp_path, check=False)
    assert rep2.in_sync
    assert rep2.updated == []

    rep_check = sync_shared(tmp_path, check=True)
    assert rep_check.in_sync
    assert rep_check.drifted == []


def test_qa_missing_source_and_missing_end_tag_errors(tmp_path: Path) -> None:
    """QA: Missing source files raise FileNotFoundError; unclosed includes raise ValueError."""
    cb_path = tmp_path / "agents" / "a1" / "cb.py"
    cb_path.parent.mkdir(parents=True)
    cb_path.write_text(
        "# @cxas-shared: shared/does_not_exist.py\n", encoding="utf-8"
    )

    with pytest.raises(FileNotFoundError, match="does not exist"):
        sync_shared(tmp_path, check=True)

    cb_path.unlink()
    instr = tmp_path / "agents" / "a1" / "instruction.txt"
    instr.write_text(
        "<!-- @cxas-include: shared/tone.txt -->\nunclosed block\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unclosed"):
        sync_shared(tmp_path, check=True)


def test_qa_source_symlink_escape_rejected(tmp_path: Path) -> None:
    """QA: Source file that is a symlink pointing outside app_dir is rejected."""
    outside = tmp_path / "outside_secret.py"
    outside.write_text("SECRET = 1\n", encoding="utf-8")

    app_dir = tmp_path / "app"
    (app_dir / "shared").mkdir(parents=True)
    (app_dir / "shared" / "evil.py").symlink_to(outside)

    cb = app_dir / "agents" / "a1" / "cb.py"
    cb.parent.mkdir(parents=True)
    cb.write_text("# @cxas-shared: shared/evil.py\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid shared path"):
        sync_shared(app_dir, check=True)


def test_qa_001_find_ignored_entries_detects_non_workflow_github_items(
    tmp_path: Path,
) -> None:
    """QA-001: .github entries outside .github/workflows must be flagged as ignored."""
    (tmp_path / "app.yaml").write_text("name: test\n", encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "other").mkdir(parents=True)
    (tmp_path / ".github" / "CODEOWNERS").write_text(
        "* @owner\n", encoding="utf-8"
    )

    ignored = cli_app._find_ignored_top_level_entries(
        tmp_path, cli_app.VALID_APP_ROOTS
    )
    assert ".github/CODEOWNERS" in ignored
    assert ".github/other" in ignored


def test_qa_002_malformed_manifest_yaml_raises_value_error_and_handles_cli_json(
    tmp_path: Path, capsys: Any
) -> None:
    """QA-002: Malformed YAML in shared/manifest.yaml must raise ValueError and emit JSON error."""
    (tmp_path / "shared").mkdir(parents=True)
    (tmp_path / "shared" / "manifest.yaml").write_text(
        "mappings:\n  - source: [unclosed\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Malformed YAML"):
        sync_shared(tmp_path, check=True)

    args = argparse.Namespace(
        app_dir=str(tmp_path), check=True, json_output=True
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_app.app_sync_shared(args)
    assert excinfo.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["in_sync"] is False
    assert "Malformed YAML" in payload["error"]


def test_qa_003_symlink_targets_in_agents_do_not_crash_or_corrupt_source(
    tmp_path: Path,
) -> None:
    """QA-003: Symlinked target files in agents/ must not crash relative_to or overwrite canonical source."""
    outside = tmp_path / "outside.py"
    outside.write_text("# @cxas-shared: shared/cb.py\n", encoding="utf-8")

    app_dir = tmp_path / "app"
    (app_dir / "shared").mkdir(parents=True)
    src_cb = app_dir / "shared" / "cb.py"
    src_cb.write_text("def run():\n    return 1\n", encoding="utf-8")
    (app_dir / "agents" / "a1").mkdir(parents=True)

    # Case A: symlink in agents/ pointing outside app_dir should raise Invalid shared path
    ext_link = app_dir / "agents" / "a1" / "ext_cb.py"
    ext_link.symlink_to(outside)
    with pytest.raises(ValueError, match="Invalid shared path"):
        sync_shared(app_dir, check=True)
    ext_link.unlink()

    # Case B: symlink in agents/ pointing to canonical source in shared/cb.py
    in_link = app_dir / "agents" / "a1" / "in_cb.py"
    in_link.symlink_to(src_cb)
    (app_dir / "shared" / "manifest.yaml").write_text(
        "mappings:\n"
        "  - source: shared/cb.py\n"
        "    targets:\n"
        "      - agents/a1/in_cb.py\n",
        encoding="utf-8",
    )
    report = sync_shared(app_dir, check=False)
    assert report.items[0].target == "agents/a1/in_cb.py"
    assert not in_link.is_symlink()
    # Canonical source must not be mutated with # @cxas-shared header
    assert src_cb.read_text(encoding="utf-8") == "def run():\n    return 1\n"


def test_qa_004_conflicting_manifest_targets_and_orphan_end_include_rejected(
    tmp_path: Path,
) -> None:
    """QA-004: Conflicting manifest sources for the same target and orphan end-include tags raise ValueError."""
    (tmp_path / "shared").mkdir(parents=True)
    (tmp_path / "shared" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "shared" / "b.py").write_text("x = 2\n", encoding="utf-8")
    manifest = tmp_path / "shared" / "manifest.yaml"
    manifest.write_text(
        "mappings:\n"
        "  - source: shared/a.py\n"
        "    targets:\n"
        "      - agents/a1/cb.py\n"
        "  - source: shared/b.py\n"
        "    targets:\n"
        "      - agents/a1/cb.py\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Conflicting"):
        sync_shared(tmp_path, check=False)

    manifest.unlink()
    (tmp_path / "shared" / "one.txt").write_text("ONE\n", encoding="utf-8")
    instr = tmp_path / "agents" / "a1" / "instruction.txt"
    instr.parent.mkdir(parents=True, exist_ok=True)
    instr.write_text(
        "<!-- @cxas-include: shared/one.txt -->\n"
        "ONE\n"
        "<!-- @cxas-end-include -->\n"
        "<!-- @cxas-end-include -->\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cxas-end-include"):
        sync_shared(tmp_path, check=True)


def test_qa_005_app_push_sync_shared_handles_missing_source_cleanly(
    tmp_path: Path, capsys: Any
) -> None:
    """QA-005: _app_push with --sync-shared exits cleanly with code 1 when sync_shared fails."""
    (tmp_path / "app.yaml").write_text("name: test\n", encoding="utf-8")
    cb = tmp_path / "agents" / "a1" / "cb.py"
    cb.parent.mkdir(parents=True)
    cb.write_text("# @cxas-shared: shared/missing.py\n", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        cli_app._app_push(
            app_dir=str(tmp_path),
            apps_client=mock.MagicMock(),
            display_name="Test App",
            args=argparse.Namespace(sync_shared=True),
        )
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "shared/missing.py" in (captured.out + captured.err)
