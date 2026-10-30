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


"""Tests for loading custom lint rules."""

import argparse
import json
import sys
import typing
from pathlib import Path

import pytest

from cxas_scrapi.cli import app as cli_app
from cxas_scrapi.utils.linter import (
    _REGISTERED_IDS,
    _RULE_REGISTRY,
    CustomRuleError,
    LintConfig,
    Rule,
    build_registry,
    get_registered_rules,
    reset_registry,
    rule,
)

RULE_SOURCE = """
from cxas_scrapi.utils.linter import Rule, Severity, rule


@rule("{category}")
class {cls}(Rule):
    id = "{rule_id}"
    target = "{target}"
    name = "no-todo"
    description = "Instruction contains TODO"
    default_severity = Severity.ERROR

    def check(self, file_path, content, context):
        if "TODO" not in content:
            return []
        rel = str(file_path.relative_to(context.project_root))
        return [self.make_result(file=rel, message="TODO left in instruction")]
"""


@pytest.fixture
def rules_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> typing.Iterator[Path]:
    """A sys.path entry for test rule modules.

    The rule registry and the rule modules are restored afterwards, so
    each test sees only the built-in rules.
    """
    build_registry()
    saved_rules = {cat: list(rules) for cat, rules in _RULE_REGISTRY.items()}
    saved_ids = set(_REGISTERED_IDS)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    reset_registry()
    _RULE_REGISTRY.update(saved_rules)
    _REGISTERED_IDS.update(saved_ids)
    for name, module in list(sys.modules.items()):
        if str(tmp_path) in str(getattr(module, "__file__", None) or ""):
            del sys.modules[name]


def _write_rules(
    root: Path,
    module: str,
    rule_id: str = "TST001",
    category: str = "instructions",
    target: str = "",
    classes: tuple[str, ...] = ("NoTodo",),
) -> None:
    """Write a module that defines one rule class per name in ``classes``."""
    (root / f"{module}.py").write_text(
        "".join(
            RULE_SOURCE.format(
                cls=cls, rule_id=rule_id, category=category, target=target
            )
            for cls in classes
        )
    )


def _install(root: Path, entry_points: dict[str, str]) -> None:
    """Install a distribution that declares custom rule entry points."""
    dist_info = root / "tst_custom_rules-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: tst-custom-rules\nVersion: 1.0\n"
    )
    lines = [f"{name} = {value}" for name, value in entry_points.items()]
    (dist_info / "entry_points.txt").write_text(
        "[cxas_scrapi.lint_rules]\n" + "\n".join(lines) + "\n"
    )


# ── Entry points ─────────────────────────────────────────────────────────


def test_entry_point_rules_are_registered(rules_root: Path) -> None:
    _write_rules(rules_root, "tst_rules")
    _install(rules_root, {"tst": "tst_rules"})

    registry = build_registry()

    custom_rule = registry.get("TST001")
    assert custom_rule is not None
    assert custom_rule.source == "tst"
    assert registry.get("T002").source == ""


def test_list_rules_names_the_source(
    rules_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_rules(rules_root, "tst_rules")
    _install(rules_root, {"tst": "tst_rules"})

    build_registry().list_rules()

    lines = capsys.readouterr().out.splitlines()
    assert any("TST001" in line and "[custom: tst]" in line for line in lines)
    assert not any("T002" in line and "[custom:" in line for line in lines)


def test_building_twice_registers_entry_point_rules_once(
    rules_root: Path,
) -> None:
    _write_rules(rules_root, "tst_rules")
    _install(rules_root, {"tst": "tst_rules"})

    build_registry()
    registry = build_registry()

    assert [r.id for r in registry.all_rules()].count("TST001") == 1


# ── Rejected custom rules ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "category",
    [
        "tools",  # Same (id, category) as the built-in T002.
        "instructions",  # Same id as T002, in another category.
    ],
)
def test_custom_rule_with_a_builtin_id_is_rejected(
    rules_root: Path, category: str
) -> None:
    _write_rules(rules_root, "tst_rules", rule_id="T002", category=category)
    _install(rules_root, {"tst": "tst_rules"})

    with pytest.raises(CustomRuleError, match=r"T002 from 'tst'"):
        build_registry()


def test_two_sources_with_the_same_rule_id_are_rejected(
    rules_root: Path,
) -> None:
    _write_rules(rules_root, "tst_first")
    _write_rules(rules_root, "tst_second")
    _install(rules_root, {"first": "tst_first", "second": "tst_second"})

    with pytest.raises(CustomRuleError, match="TST001"):
        build_registry()


def test_custom_rule_in_an_unknown_category_is_rejected(
    rules_root: Path,
) -> None:
    _write_rules(rules_root, "tst_rules", category="instruction")
    _install(rules_root, {"tst": "tst_rules"})

    with pytest.raises(CustomRuleError, match=r"TST001.*'instruction'"):
        build_registry()


@pytest.mark.parametrize(
    ("category", "target"),
    [
        ("structure", ""),
        ("schema", ""),
        ("structure", "agent_configs"),
    ],
)
def test_target_dispatched_rule_without_a_known_target_is_rejected(
    rules_root: Path, category: str, target: str
) -> None:
    _write_rules(rules_root, "tst_rules", category=category, target=target)
    _install(rules_root, {"tst": "tst_rules"})

    with pytest.raises(CustomRuleError, match=r"TST001.*target"):
        build_registry()


def test_structure_rule_may_use_a_target_that_only_schema_rules_use(
    rules_root: Path,
) -> None:
    # run_rules matches structure and schema rules to the same files, and
    # no built-in structure rule targets app_config.
    _write_rules(
        rules_root, "tst_rules", category="structure", target="app_config"
    )
    _install(rules_root, {"tst": "tst_rules"})

    assert build_registry().get("TST001").source == "tst"


def test_rule_defined_twice_in_one_source_is_rejected(
    rules_root: Path,
) -> None:
    _write_rules(rules_root, "tst_rules", classes=("NoTodo", "NoTodoAgain"))
    _install(rules_root, {"tst": "tst_rules"})

    with pytest.raises(CustomRuleError, match=r"TST001.*twice"):
        build_registry()


def test_custom_rule_without_an_id_is_rejected(rules_root: Path) -> None:
    _write_rules(rules_root, "tst_rules", rule_id="")
    _install(rules_root, {"tst": "tst_rules"})

    with pytest.raises(CustomRuleError, match="no rule ID"):
        build_registry()


def test_source_that_fails_to_import_raises_custom_rule_error(
    rules_root: Path,
) -> None:
    (rules_root / "tst_broken.py").write_text("import tst_missing_module\n")
    _install(rules_root, {"tst": "tst_broken"})

    with pytest.raises(
        CustomRuleError,
        match=r"'tst'.*ModuleNotFoundError: .*tst_missing_module",
    ):
        build_registry()


def test_rejected_custom_rules_leave_builtin_rules_registered(
    rules_root: Path,
) -> None:
    before = sorted(
        (r.id, r.category) for rs in get_registered_rules().values() for r in rs
    )
    _write_rules(rules_root, "tst_rules", rule_id="T002", category="tools")
    _install(rules_root, {"tst": "tst_rules"})

    with pytest.raises(CustomRuleError):
        build_registry()

    after = sorted(
        (r.id, r.category) for rs in get_registered_rules().values() for r in rs
    )
    assert after == before


def test_rule_decorator_registers_again_after_a_source_fails(
    rules_root: Path,
) -> None:
    (rules_root / "tst_broken.py").write_text("import tst_missing_module\n")
    _install(rules_root, {"tst": "tst_broken"})
    with pytest.raises(CustomRuleError):
        build_registry()

    @rule("instructions")
    class AfterFailure(Rule):
        id = "TST099"

        def check(
            self, file_path: Path, content: str, context: typing.Any
        ) -> list:
            return []

    registered = [r.id for rs in get_registered_rules().values() for r in rs]
    assert "TST099" in registered


# ── cxaslint.yaml custom_rules ───────────────────────────────────────────


def _configure(project: Path, custom_rules: list[str]) -> LintConfig:
    project.mkdir(exist_ok=True)
    (project / "cxaslint.yaml").write_text(
        "custom_rules:\n" + "".join(f"  - {p}\n" for p in custom_rules)
    )
    return LintConfig.load(project)


def test_config_rule_file_is_registered(rules_root: Path) -> None:
    project = rules_root / "project"
    (project / "lint_rules").mkdir(parents=True)
    _write_rules(project / "lint_rules", "tst_file_rules")
    config = _configure(project, ["lint_rules/tst_file_rules.py"])

    registry = build_registry(config)

    assert registry.get("TST001").source == "tst_file_rules.py"


def test_config_rule_package_is_registered(rules_root: Path) -> None:
    package = rules_root / "project" / "tst_pkg"
    package.mkdir(parents=True)
    # The package imports its own module by absolute name.
    (package / "__init__.py").write_text("from tst_pkg import rules  # noqa\n")
    _write_rules(package, "rules")
    config = _configure(rules_root / "project", ["tst_pkg"])

    registry = build_registry(config)

    assert registry.get("TST001").source == "tst_pkg"


def test_building_twice_with_config_rules_registers_them_once(
    rules_root: Path,
) -> None:
    project = rules_root / "project"
    project.mkdir()
    _write_rules(project, "tst_file_rules")
    config = _configure(project, ["tst_file_rules.py"])

    build_registry(config)
    registry = build_registry(config)

    assert [r.id for r in registry.all_rules()].count("TST001") == 1


def test_missing_config_rule_file_is_rejected(rules_root: Path) -> None:
    config = _configure(rules_root / "project", ["lint_rules/missing.py"])

    with pytest.raises(CustomRuleError, match=r"'missing.py'"):
        build_registry(config)


def test_config_rule_file_that_shadows_a_loaded_module_is_rejected(
    rules_root: Path,
) -> None:
    project = rules_root / "project"
    project.mkdir()
    _write_rules(project, "json")
    config = _configure(project, ["json.py"])

    with pytest.raises(CustomRuleError, match=r"'json.py'.*already"):
        build_registry(config)


def test_config_rule_file_that_fails_to_import_fails_on_every_build(
    rules_root: Path,
) -> None:
    project = rules_root / "project"
    project.mkdir()
    (project / "tst_flaky.py").write_text("raise RuntimeError('broken')\n")
    config = _configure(project, ["tst_flaky.py"])

    for _ in range(2):
        with pytest.raises(CustomRuleError, match="RuntimeError: broken"):
            build_registry(config)


def test_config_rule_file_that_is_not_python_is_rejected(
    rules_root: Path,
) -> None:
    project = rules_root / "project"
    project.mkdir()
    (project / "rules.txt").write_text("not python\n")
    config = _configure(project, ["rules.txt"])

    with pytest.raises(CustomRuleError, match="not a Python file"):
        build_registry(config)


@pytest.mark.parametrize(
    "value",
    [
        "custom_rules: team_rules.py\n",  # A string, not a list.
        "custom_rules:\n  - 123\n",  # An entry that is not a path.
    ],
)
def test_custom_rules_must_be_a_list_of_paths(
    rules_root: Path, value: str
) -> None:
    project = rules_root / "project"
    project.mkdir()
    (project / "cxaslint.yaml").write_text(value)

    with pytest.raises(ValueError, match="custom_rules"):
        LintConfig.load(project)


# ── cxas lint ────────────────────────────────────────────────────────────


def _make_app(project: Path, instruction: str) -> None:
    project.mkdir()
    (project / "app.json").write_text(
        '{"name": "test-app", "displayName": "Test App",'
        ' "rootAgent": "root_agent"}'
    )
    agent_dir = project / "agents" / "root_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "instruction.txt").write_text(instruction)
    (agent_dir / "root_agent.json").write_text(
        '{"displayName": "root_agent", "tools": ["end_session"]}'
    )


def _lint(project: Path, **flags: typing.Any) -> int:
    """Run ``cxas lint`` on a project and return its exit code."""
    args = argparse.Namespace(app_dir=str(project), **flags)
    with pytest.raises(SystemExit) as excinfo:
        cli_app.app_lint(args)
    return excinfo.value.code


def test_lint_runs_entry_point_rules(
    rules_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_rules(rules_root, "tst_rules")
    _install(rules_root, {"tst": "tst_rules"})
    _make_app(rules_root / "project", "<role>TODO</role>")

    code = _lint(rules_root / "project", json_output=True, rule="TST001")

    assert code == 1
    results = json.loads(capsys.readouterr().out)
    assert [(r["rule_id"], r["severity"]) for r in results] == [
        ("TST001", "error")
    ]


def test_lint_fails_when_custom_rules_are_rejected(
    rules_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_rules(rules_root, "tst_rules", rule_id="T002")
    _install(rules_root, {"tst": "tst_rules"})
    _make_app(rules_root / "project", "<role>clean</role>")

    code = _lint(rules_root / "project", json_output=True)

    assert code == 1
    results = json.loads(capsys.readouterr().out)
    assert len(results) == 1
    # The same keys as every other lint result.
    assert sorted(results[0]) == [
        "file",
        "fix_suggestion",
        "line",
        "message",
        "rule_id",
        "severity",
    ]
    assert (results[0]["rule_id"], results[0]["severity"]) == (
        "SETUP",
        "error",
    )
    assert "T002" in results[0]["message"]


def test_lint_reports_rejected_custom_rules_as_text(
    rules_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_rules(rules_root, "tst_rules", rule_id="T002")
    _install(rules_root, {"tst": "tst_rules"})
    _make_app(rules_root / "project", "<role>clean</role>")

    code = _lint(rules_root / "project")

    assert code == 1
    assert "[SETUP] Custom rule T002 from 'tst'" in capsys.readouterr().out


def test_lint_runs_config_rules(
    rules_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = rules_root / "project"
    _make_app(project, "<role>TODO</role>")
    _write_rules(project, "tst_file_rules")
    _configure(project, ["tst_file_rules.py"])

    code = _lint(project, json_output=True, rule="TST001")

    assert code == 1
    results = json.loads(capsys.readouterr().out)
    assert [r["rule_id"] for r in results] == ["TST001"]


def test_list_rules_includes_config_rules(
    rules_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = rules_root / "project"
    project.mkdir()
    _write_rules(project, "tst_file_rules")
    _configure(project, ["tst_file_rules.py"])

    code = _lint(project, list_rules=True)

    assert code == 0
    assert "[custom: tst_file_rules.py]" in capsys.readouterr().out


def test_config_severity_override_applies_to_custom_rules(
    rules_root: Path,
) -> None:
    project = rules_root / "project"
    _make_app(project, "<role>TODO</role>")
    _write_rules(project, "tst_file_rules")
    (project / "cxaslint.yaml").write_text(
        "custom_rules:\n  - tst_file_rules.py\nrules:\n  TST001: off\n"
    )

    assert _lint(project, rule="TST001") == 0
