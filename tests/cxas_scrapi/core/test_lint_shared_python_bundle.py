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

"""Unit tests for C025 (SharedPythonBundleIntegrity) lint rule."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cxas_scrapi.core.python_bundler import (
    BUNDLE_BEGIN_PREFIX,
    BUNDLE_END_LINE,
    bundle_shared_python,
    synthesize_python_env_files,
)
from cxas_scrapi.utils.lint_rules.callbacks import SharedPythonBundleIntegrity
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    Severity,
    build_context,
    build_registry,
    run_rules,
)


def _write_json(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _ctx(app_root: Path) -> LintContext:
    return LintContext(
        project_root=app_root,
        app_dir=app_root,
        evals_dir=app_root / "evals",
        app_root=app_root,
    )


def _run_c025(
    app_root: Path,
    *,
    config: LintConfig | None = None,
    categories: list[str] | None = None,
) -> LintReport:
    cfg = config or LintConfig.load(app_root)
    disc = Discovery(app_root, app_root / "evals")
    ctx = build_context(app_root, cfg, disc)
    report = LintReport()
    run_rules(
        build_registry(),
        cfg,
        ctx,
        disc,
        report,
        categories=categories,
        specific_rules={"C025"},
    )
    return report


def _make_minimal_app(root: Path) -> Path:
    app_json = _write_json(
        root / "app.json", {"displayName": "app", "rootAgent": "root"}
    )
    _write_json(
        root / "agents" / "root" / "root.json",
        {"displayName": "root", "tools": ["end_session"]},
    )
    (root / "agents" / "root" / "instruction.txt").write_text(
        "<role>Assistant</role>\n", encoding="utf-8"
    )
    return app_json


def test_c025_syntax_and_utf8_errors_in_shared_python(tmp_path: Path) -> None:
    _make_minimal_app(tmp_path)
    (tmp_path / "python_lib").mkdir(parents=True)
    (tmp_path / "python_lib" / "bad_syntax.py").write_text(
        "def broken(:\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "python_lib" / "non_utf8.py").write_bytes(b"\xff\xfe\x00")
    (tmp_path / "pythonEnvFiles" / "content" / "python_lib").mkdir(parents=True)
    (
        tmp_path / "pythonEnvFiles" / "content" / "python_lib" / "env_bad.py"
    ).write_text("class Broken(:\n    pass\n", encoding="utf-8")

    rep = _run_c025(tmp_path)
    by_file = {r.file: r for r in rep.results}
    assert set(by_file) == {
        "python_lib/bad_syntax.py",
        "python_lib/non_utf8.py",
        "pythonEnvFiles/content/python_lib/env_bad.py",
    }
    assert all(r.severity == Severity.ERROR for r in rep.results)
    assert by_file["python_lib/bad_syntax.py"].line == 1
    assert by_file["pythonEnvFiles/content/python_lib/env_bad.py"].line == 1


def test_c025_malformed_truncated_reversed_and_duplicate_markers(
    tmp_path: Path,
) -> None:
    _make_minimal_app(tmp_path)
    cases = {
        "cb_trunc": f"{BUNDLE_BEGIN_PREFIX} ===\nx = 1\n",
        "cb_trunc_begin": f"{BUNDLE_BEGIN_PREFIX}\n{BUNDLE_END_LINE}\n",
        "cb_trunc_end": f"{BUNDLE_BEGIN_PREFIX} ===\n# === END CXAS_SHARED_BUNDLE\n",
        "cb_reversed": f"{BUNDLE_END_LINE}\n{BUNDLE_BEGIN_PREFIX} ===\n",
        "cb_nested": (
            f"{BUNDLE_BEGIN_PREFIX} ===\n"
            f"{BUNDLE_BEGIN_PREFIX} ===\n"
            f"{BUNDLE_END_LINE}\n"
            f"{BUNDLE_END_LINE}\n"
        ),
        "cb_duplicate": (
            f"{BUNDLE_BEGIN_PREFIX} ===\n"
            f"{BUNDLE_END_LINE}\n"
            f"{BUNDLE_BEGIN_PREFIX} ===\n"
            f"{BUNDLE_END_LINE}\n"
        ),
    }
    for name, src in cases.items():
        p = (
            tmp_path
            / "agents"
            / "root"
            / "before_model_callbacks"
            / name
            / "python_code.py"
        )
        p.parent.mkdir(parents=True)
        p.write_text(src, encoding="utf-8")

    rep = _run_c025(tmp_path)
    assert len(rep.results) == len(cases)
    assert all(r.severity == Severity.ERROR for r in rep.results)
    assert {Path(r.file).parent.name for r in rep.results} == set(cases)


def test_c025_type_checking_guards_skipped_and_runtime_else_checked(
    tmp_path: Path,
) -> None:
    app_json = _make_minimal_app(tmp_path)

    # TYPE_CHECKING & typing.TYPE_CHECKING & not TYPE_CHECKING else skipped
    cb_tc = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks"
        / "cb_tc"
        / "python_code.py"
    )
    cb_tc.parent.mkdir(parents=True)
    cb_tc.write_text(
        "import typing\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from python_lib.types import BrandCtx\n"
        "if typing.TYPE_CHECKING:\n"
        "    import python_lib.more_types\n"
        "if TYPE_CHECKING and True:\n"
        "    import python_lib.and_types\n"
        "if not TYPE_CHECKING:\n"
        "    x = 1\n"
        "else:\n"
        "    import python_lib.not_tc_else\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    return None\n",
        encoding="utf-8",
    )
    assert (
        SharedPythonBundleIntegrity().check(app_json, "", _ctx(tmp_path)) == []
    )

    # Runtime import in `else:` of `if TYPE_CHECKING:` MUST be caught
    cb_else = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks"
        / "cb_else"
        / "python_code.py"
    )
    cb_else.parent.mkdir(parents=True)
    cb_else.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import python_lib.type_only\n"
        "else:\n"
        "    import python_lib.runtime_else\n",
        encoding="utf-8",
    )
    # Runtime import in body of `if not TYPE_CHECKING:` MUST be caught
    cb_not_tc = (
        tmp_path
        / "agents"
        / "root"
        / "before_model_callbacks"
        / "cb_not_tc"
        / "python_code.py"
    )
    cb_not_tc.parent.mkdir(parents=True)
    cb_not_tc.write_text(
        "from typing import TYPE_CHECKING\n"
        "if not TYPE_CHECKING:\n"
        "    import python_lib.runtime_body\n",
        encoding="utf-8",
    )

    rep = _run_c025(tmp_path)
    assert len(rep.results) == 2  # noqa: PLR2004
    assert {Path(r.file).parent.name for r in rep.results} == {
        "cb_else",
        "cb_not_tc",
    }
    assert all(r.severity == Severity.ERROR for r in rep.results)


def test_c025_inline_bundle_and_real_bundler_integration(
    tmp_path: Path,
) -> None:
    _make_minimal_app(tmp_path)
    (tmp_path / "python_lib").mkdir(parents=True)
    (tmp_path / "python_lib" / "helpers.py").write_text(
        "def greet() -> str:\n    return 'hi'\n", encoding="utf-8"
    )
    tool_py = tmp_path / "tools" / "t1" / "python_function" / "python_code.py"
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(
        "from python_lib.helpers import greet\n"
        "def t1() -> dict:\n"
        "    return {'msg': greet()}\n",
        encoding="utf-8",
    )

    # Before bundling: local python_lib/ only -> WARNING
    rep_unbuilt = _run_c025(tmp_path)
    assert len(rep_unbuilt.results) == 1
    assert rep_unbuilt.results[0].severity == Severity.WARNING
    assert "cxas build/package" in rep_unbuilt.results[0].message

    # After inline bundling via real bundle_shared_python -> PASS
    bundle_shared_python(tmp_path)
    assert _run_c025(tmp_path).results == []


def test_c025_native_python_env_pass_missing_module_and_corrupt_yaml(
    tmp_path: Path,
) -> None:
    _make_minimal_app(tmp_path)
    (tmp_path / "python_lib" / "sub").mkdir(parents=True)
    (tmp_path / "python_lib" / "sub" / "mod.py").write_text(
        "def fn() -> str:\n    return 'ok'\n", encoding="utf-8"
    )
    tool_py = tmp_path / "tools" / "t1" / "python_function" / "python_code.py"
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(
        "from python_lib.sub.mod import fn\n"
        "def t1() -> dict:\n"
        "    return {'msg': fn()}\n",
        encoding="utf-8",
    )

    # Synthesize real pythonEnvFiles/ -> PASS
    synthesize_python_env_files(tmp_path)
    assert _run_c025(tmp_path).results == []

    # Missing module in pythonEnvFiles/content/ -> ERROR
    tool_py.write_text(
        "from python_lib.sub.missing_mod import nope\n"
        "def t1() -> dict:\n"
        "    return {'msg': nope()}\n",
        encoding="utf-8",
    )
    rep_missing = _run_c025(tmp_path)
    assert len(rep_missing.results) == 1
    assert rep_missing.results[0].severity == Severity.ERROR
    assert (
        "missing from pythonEnvFiles/content/" in rep_missing.results[0].message
    )

    # Corrupt pythonEnvFiles.yaml -> ERROR on manifest file
    (tmp_path / "pythonEnvFiles" / "pythonEnvFiles.yaml").write_text(
        "pythonEnvFiles: [unclosed\n", encoding="utf-8"
    )
    rep_corrupt = _run_c025(tmp_path)
    assert any(
        r.file == "pythonEnvFiles/pythonEnvFiles.yaml"
        and r.severity == Severity.ERROR
        for r in rep_corrupt.results
    )


def test_c025_missing_everywhere_emits_error(tmp_path: Path) -> None:
    _make_minimal_app(tmp_path)
    tool_py = tmp_path / "tools" / "t1" / "python_function" / "python_code.py"
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(
        "from python_lib.helpers import greet\n"
        "def t1() -> dict:\n"
        "    return {'msg': greet()}\n",
        encoding="utf-8",
    )

    rep = _run_c025(tmp_path, categories=["callbacks"])
    assert len(rep.results) == 1
    assert rep.results[0].rule_id == "C025"
    assert rep.results[0].severity == Severity.ERROR
    assert "ModuleNotFoundError" in rep.results[0].message


def test_c025_skips_symlinks_and_hidden_dirs(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_bad = outside / "escape.py"
    outside_bad.write_text("def broken(:\n", encoding="utf-8")

    app = tmp_path / "app"
    _make_minimal_app(app)
    (app / "python_lib" / ".hidden").mkdir(parents=True)
    (app / "python_lib" / ".hidden" / "bad.py").write_text(
        "def broken(:\n", encoding="utf-8"
    )
    (app / "python_lib" / "__pycache__").mkdir(parents=True)
    (app / "python_lib" / "__pycache__" / "bad.py").write_text(
        "def broken(:\n", encoding="utf-8"
    )
    os.symlink(outside_bad, app / "python_lib" / "sym_bad.py")
    os.symlink(outside, app / "pythonEnvFiles")

    assert _run_c025(app).results == []


def test_c025_cxaslint_severity_override_and_off(tmp_path: Path) -> None:
    _make_minimal_app(tmp_path)
    tool_py = tmp_path / "tools" / "t1" / "python_function" / "python_code.py"
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(
        "import python_lib.missing\n",
        encoding="utf-8",
    )

    (tmp_path / "cxaslint.yaml").write_text(
        "rules:\n  C025: info\n", encoding="utf-8"
    )
    rep_info = _run_c025(tmp_path)
    assert len(rep_info.results) == 1
    assert rep_info.results[0].severity == Severity.INFO

    (tmp_path / "cxaslint.yaml").write_text(
        "rules:\n  C025: off\n", encoding="utf-8"
    )
    rep_off = _run_c025(tmp_path)
    assert rep_off.results == []


def test_c025_inline_bundle_tamper_stale_and_docstring_markers(
    tmp_path: Path,
) -> None:
    _make_minimal_app(tmp_path)
    (tmp_path / "python_lib").mkdir(parents=True)
    (tmp_path / "python_lib" / "helpers.py").write_text(
        "def greet() -> str:\n    return 'hi'\n", encoding="utf-8"
    )
    tool_py = tmp_path / "tools" / "t1" / "python_function" / "python_code.py"
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(
        "from python_lib.helpers import greet\n"
        "def t1() -> dict:\n"
        '    """Mention marker safely:\n'
        f"    {BUNDLE_BEGIN_PREFIX} (sha256:0000000000000000) ===\n"
        '    """\n'
        "    return {'msg': greet()}\n",
        encoding="utf-8",
    )

    bundle_shared_python(tmp_path)
    bundled = tool_py.read_text(encoding="utf-8")
    assert _run_c025(tmp_path).results == []

    # Tampered payload fails digest check
    tool_py.write_text(
        bundled.replace("return 'hi'", "return 'hacked'", 1), encoding="utf-8"
    )
    rep_tampered = _run_c025(tmp_path)
    assert len(rep_tampered.results) == 1
    assert rep_tampered.results[0].severity == Severity.ERROR
    assert "digest mismatch" in rep_tampered.results[0].message

    # Stale bundle missing newly added import fails coverage check
    tool_py.write_text(
        bundled + "\nimport python_lib.extra_mod\n", encoding="utf-8"
    )
    rep_stale = _run_c025(tmp_path)
    assert len(rep_stale.results) == 1
    assert rep_stale.results[0].severity == Severity.ERROR
    assert "python_lib.extra_mod" in rep_stale.results[0].message


def test_c025_native_env_namespace_subpackage_and_unsafe_manifest_paths(
    tmp_path: Path,
) -> None:
    _make_minimal_app(tmp_path)
    (tmp_path / "python_lib" / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "python_lib" / "pkg" / "sub" / "mod.py").write_text(
        "VAL = 42\n", encoding="utf-8"
    )
    tool_py = tmp_path / "tools" / "t1" / "python_function" / "python_code.py"
    tool_py.parent.mkdir(parents=True)
    tool_py.write_text(
        "from python_lib.pkg import sub\n"
        "def t1() -> dict:\n"
        "    return {'v': sub.mod.VAL}\n",
        encoding="utf-8",
    )

    synthesize_python_env_files(tmp_path)
    assert _run_c025(tmp_path).results == []

    # Path traversal in pythonEnvFiles.yaml is rejected
    (tmp_path / "pythonEnvFiles" / "pythonEnvFiles.yaml").write_text(
        "pythonEnvFiles:\n  - name: evil\n    filePath: ../app.json\n",
        encoding="utf-8",
    )
    rep_traversal = _run_c025(tmp_path)
    assert any(
        r.file == "pythonEnvFiles/pythonEnvFiles.yaml"
        and r.severity == Severity.ERROR
        and "unsafe filePath" in r.message
        for r in rep_traversal.results
    )


def test_sample_support_agent_clean_with_c025_registered() -> None:
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
    registry = build_registry()
    assert len(registry.all_rules()) >= 105  # noqa: PLR2004
    assert registry.get("C025") is not None
    report = LintReport()
    run_rules(registry, cfg, ctx, discovery, report)
    assert report.errors == []
    assert report.warnings == []
