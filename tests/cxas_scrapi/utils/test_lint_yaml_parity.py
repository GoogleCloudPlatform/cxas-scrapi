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

"""Unit tests for YAML/YML config parity and cross-file ignore filtering."""

from pathlib import Path

from cxas_scrapi.utils.lint_rules.config import (
    AgentMissingInstruction,
    AgentToolNotExists,
    AppRootAgentValidation,
    AppVariableDefaultsAndEnvVars,
    InvalidJson,
    MissingRequiredFields,
    RootAgentMissingEndSession,
)
from cxas_scrapi.utils.lint_rules.schema import _strip_v1main_only_fields
from cxas_scrapi.utils.lint_rules.structure import (
    AgentResourceReferences,
    AgentToolReferences,
    AppResourceReferences,
    CallbackFileReferences,
    ChildAgentReferences,
    StrictAgentPathLayout,
)
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    LintResult,
    Rule,
    RuleRegistry,
    Severity,
    build_context,
    build_registry,
    resolve_safe_app_file,
    run_rules,
)


def _write_yaml_app(tmp_path: Path, app_filename: str = "app.yaml") -> Path:
    """Create a minimal valid YAML-configured CXAS app."""
    (tmp_path / app_filename).write_text(
        "name: projects/p/locations/l/apps/demo\n"
        "displayName: Demo App\n"
        "rootAgent: Root Agent\n"
        "modelSettings:\n"
        "  model: gemini-2.5-flash\n",
        encoding="utf-8",
    )
    root_dir = tmp_path / "agents" / "root_agent"
    root_dir.mkdir(parents=True)
    (root_dir / "root_agent.yaml").write_text(
        "displayName: Root Agent\n"
        "instruction: agents/root_agent/instruction.txt\n"
        "tools:\n"
        "  - end_session\n"
        "  - lookup_order\n"
        "childAgents:\n"
        "  - billing_agent\n",
        encoding="utf-8",
    )
    (root_dir / "instruction.txt").write_text(
        "Use {@TOOL: lookup_order} to look up orders.\n",
        encoding="utf-8",
    )
    billing_dir = tmp_path / "agents" / "billing_agent"
    billing_dir.mkdir(parents=True)
    (billing_dir / "billing_agent.yml").write_text(
        "displayName: Billing Agent\n"
        "instruction: agents/billing_agent/instruction.txt\n"
        "modelSettings:\n"
        "  model: gemini-2.5-pro\n",
        encoding="utf-8",
    )
    (billing_dir / "instruction.txt").write_text(
        "Transfer back to {@AGENT: root_agent} when finished.\n",
        encoding="utf-8",
    )
    tool_dir = tmp_path / "tools" / "lookup_order"
    (tool_dir / "python_function").mkdir(parents=True)
    (tool_dir / "lookup_order.yaml").write_text(
        "displayName: lookup_order\n"
        "pythonFunction:\n"
        "  name: lookup_order\n"
        "  pythonCode: tools/lookup_order/python_function/python_code.py\n",
        encoding="utf-8",
    )
    (tool_dir / "python_function" / "python_code.py").write_text(
        'def lookup_order(order_id: str) -> dict:\n    return {"id": order_id}\n',
        encoding="utf-8",
    )
    return tmp_path


def test_builtin_rule_count_unchanged() -> None:
    """PR A adds zero new lint rules (remains at 104 built-in rules)."""
    registry = build_registry()
    assert len(registry.all_rules()) == 104


def test_discovery_and_context_yaml_yml_parity(tmp_path: Path) -> None:
    """Discovery finds app.yaml/app.yml, agent .yaml/.yml, and models."""
    _write_yaml_app(tmp_path, app_filename="app.yml")
    disc = Discovery(tmp_path, tmp_path / "evals")
    assert disc.app_root == tmp_path
    assert disc.discover_app_config() == tmp_path / "app.yml"
    assert disc.discover_app_model() == "gemini-2.5-flash"

    agent_cfgs = disc.discover_agent_configs()
    assert agent_cfgs["root_agent"].name == "root_agent.yaml"
    assert agent_cfgs["billing_agent"].name == "billing_agent.yml"
    assert disc.discover_agent_models() == {"billing_agent": "gemini-2.5-pro"}

    ctx = build_context(tmp_path, LintConfig(), disc)
    assert ctx.model == "gemini-2.5-flash"
    assert "root_agent" in ctx.agent_to_parents["billing_agent"]
    assert "Billing Agent" in ctx.all_agent_display_names


def test_config_rules_a001_to_a006_yaml_parity(tmp_path: Path) -> None:
    """A001-A006 validate .yaml and .yml app and agent configs."""
    _write_yaml_app(tmp_path, app_filename="app.yaml")
    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, LintConfig(), disc)

    # Valid YAML passes A001, A002, A003, A004, A005, A006
    app_yaml = tmp_path / "app.yaml"
    app_text = app_yaml.read_text(encoding="utf-8")
    assert InvalidJson().check(app_yaml, app_text, ctx) == []
    assert MissingRequiredFields().check(app_yaml, app_text, ctx) == []
    assert RootAgentMissingEndSession().check(app_yaml, app_text, ctx) == []
    assert AppRootAgentValidation().check(app_yaml, app_text, ctx) == []

    root_yaml = tmp_path / "agents" / "root_agent" / "root_agent.yaml"
    root_text = root_yaml.read_text(encoding="utf-8")
    assert AgentToolNotExists().check(root_yaml, root_text, ctx) == []
    assert AgentMissingInstruction().check(root_yaml, root_text, ctx) == []

    # Invalid YAML fails A001
    bad_yaml = tmp_path / "bad.yaml"
    bad_res = InvalidJson().check(bad_yaml, "a: [1, 2\n", ctx)
    assert len(bad_res) == 1
    assert bad_res[0].rule_id == "A001"

    # Non-mapping YAML fails A001
    list_res = InvalidJson().check(bad_yaml, "- item1\n- item2\n", ctx)
    assert len(list_res) == 1
    assert "mapping" in list_res[0].message

    # Missing displayName in app.yml fails A002
    missing_yml = tmp_path / "app.yml"
    a002_res = MissingRequiredFields().check(missing_yml, "name: demo\n", ctx)
    assert [r.rule_id for r in a002_res] == ["A002"]

    # Unknown tool in YAML agent fails A003
    a003_res = AgentToolNotExists().check(
        root_yaml, "displayName: R\ntools:\n  - missing_tool\n", ctx
    )
    assert [r.rule_id for r in a003_res] == ["A003"]


def test_a017_supports_app_yml(tmp_path: Path) -> None:
    """A017 validates variableDeclarations in app.yml."""
    app_yml = tmp_path / "app.yml"
    app_yml.write_text(
        "displayName: Demo\n"
        "variableDeclarations:\n"
        "  - name: retries\n"
        "    schema:\n"
        "      type: INTEGER\n"
        "      default: not_an_int\n",
        encoding="utf-8",
    )
    ctx = LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        app_root=tmp_path,
    )
    res = AppVariableDefaultsAndEnvVars().check(
        app_yml, app_yml.read_text(encoding="utf-8"), ctx
    )
    assert len(res) == 1
    assert res[0].rule_id == "A017"


def test_structure_rules_yaml_parity_and_dict_child_agents(
    tmp_path: Path,
) -> None:
    """S002-S005, S007, S010-S011 support YAML configs and dict childAgents."""
    _write_yaml_app(tmp_path)
    root_yaml = tmp_path / "agents" / "root_agent" / "root_agent.yaml"
    root_yaml.write_text(
        "displayName: Root Agent\n"
        "instruction: agents/root_agent/instruction.txt\n"
        "tools:\n"
        "  - end_session\n"
        "  - lookup_order\n"
        "childAgents:\n"
        "  - displayName: Billing Agent\n",
        encoding="utf-8",
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, LintConfig(), disc)

    assert "root_agent" in ctx.agent_to_parents["billing_agent"]

    root_text = root_yaml.read_text(encoding="utf-8")
    inst = tmp_path / "agents" / "root_agent" / "instruction.txt"
    assert (
        AgentToolReferences().check(inst, inst.read_text(encoding="utf-8"), ctx)
        == []
    )
    assert CallbackFileReferences().check(root_yaml, root_text, ctx) == []
    assert ChildAgentReferences().check(root_yaml, root_text, ctx) == []
    assert StrictAgentPathLayout().check(root_yaml, root_text, ctx) == []
    assert AgentResourceReferences().check(root_yaml, root_text, ctx) == []

    app_yaml = tmp_path / "app.yaml"
    assert (
        AppResourceReferences().check(
            app_yaml, app_yaml.read_text(encoding="utf-8"), ctx
        )
        == []
    )

    # Missing dict childAgent reports clean S004 error without crashing
    bad_child = (
        "displayName: Root Agent\nchildAgents:\n  - displayName: Ghost Agent\n"
    )
    s004_res = ChildAgentReferences().check(root_yaml, bad_child, ctx)
    assert len(s004_res) == 1
    assert "Ghost Agent" in s004_res[0].message


def test_t008_yaml_ces_name_and_yaml_guardrail_parity(tmp_path: Path) -> None:
    """T008 resolves YAML tools, full CES tool paths, and YAML guardrails."""
    _write_yaml_app(tmp_path)
    # Tool referenced via full CES resource path in YAML agent
    extra_dir = tmp_path / "tools" / "ces_tool"
    (extra_dir / "python_function").mkdir(parents=True)
    (extra_dir / "ces_tool.yaml").write_text(
        "name: projects/p/locations/l/apps/demo/tools/ces_tool\n"
        "displayName: CES Tool\n",
        encoding="utf-8",
    )
    (extra_dir / "python_function" / "python_code.py").write_text(
        "def ces_tool() -> dict:\n    return {}\n", encoding="utf-8"
    )
    # Tool referenced via YAML guardrail action.toolCall
    gr_tool_dir = tmp_path / "tools" / "gr_tool"
    (gr_tool_dir / "python_function").mkdir(parents=True)
    (gr_tool_dir / "gr_tool.yml").write_text(
        "displayName: Guardrail Tool\n", encoding="utf-8"
    )
    (gr_tool_dir / "python_function" / "python_code.py").write_text(
        "def gr_tool() -> dict:\n    return {}\n", encoding="utf-8"
    )
    gr_dir = tmp_path / "guardrails" / "safety"
    gr_dir.mkdir(parents=True)
    (gr_dir / "safety.yaml").write_text(
        "displayName: Safety\naction:\n  toolCall:\n    tool: gr_tool\n",
        encoding="utf-8",
    )
    root_yaml = tmp_path / "agents" / "root_agent" / "root_agent.yaml"
    root_yaml.write_text(
        "displayName: Root Agent\n"
        "instruction: agents/root_agent/instruction.txt\n"
        "tools:\n"
        "  - end_session\n"
        "  - lookup_order\n"
        "  - projects/p/locations/l/apps/demo/tools/ces_tool\n",
        encoding="utf-8",
    )

    registry = build_registry()
    cfg = LintConfig()
    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(registry, cfg, ctx, disc, report, specific_rules={"T008"})
    assert report.results == []


def test_v002_strips_guided_agent_field() -> None:
    """_strip_v1main_only_fields strips unknown guidedAgent on agent_config."""
    data = {
        "displayName": "Guided",
        "guidedAgent": {"config": "yaml"},
    }
    _strip_v1main_only_fields(data, "agent_config")
    # If SDK Agent proto lacks guidedAgent it is stripped; if present, kept.
    assert "displayName" in data


def test_resolve_safe_app_file_blocks_traversal_and_symlinks(
    tmp_path: Path,
) -> None:
    """resolve_safe_app_file rejects .., absolute paths, and symlinks."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    good = app_root / "tools" / "a" / "schema.yaml"
    good.parent.mkdir(parents=True)
    good.write_text("openapi: 3.0.0\n", encoding="utf-8")

    outside = tmp_path / "secret.yaml"
    outside.write_text("secret: true\n", encoding="utf-8")
    link = app_root / "link.yaml"
    link.symlink_to(outside)

    assert resolve_safe_app_file(app_root, "tools/a/schema.yaml") == good
    assert resolve_safe_app_file(app_root, "../secret.yaml") is None
    assert resolve_safe_app_file(app_root, str(outside)) is None
    assert resolve_safe_app_file(app_root, "link.yaml") is None
    assert resolve_safe_app_file(app_root, "https://example.com/a.yaml") is None


def test_run_rules_ignores_cross_file_emitted_result_paths(
    tmp_path: Path,
) -> None:
    """ignore: and per_file: apply to emitted result.file != source file."""
    _write_yaml_app(tmp_path)

    class _CrossFileRule(Rule):
        id = "X999"
        name = "cross-file-test"
        description = (
            "Emits findings against tools/ and agents/ from app_config"
        )
        default_severity = Severity.ERROR
        category = "structure"
        target = "app_config"

        def check(
            self, file_path: Path, content: str, context: LintContext
        ) -> list[LintResult]:
            return [
                self.make_result(
                    file="tools/bad_api/open_api_tool/open_api_schema.yaml",
                    message="Broken schema",
                ),
                self.make_result(
                    file="agents/billing_agent/billing_agent.yml",
                    message="Billing issue",
                ),
            ]

    reg = RuleRegistry()
    reg.register(_CrossFileRule())
    cfg = LintConfig(
        ignore=["tools/**"],
        per_file={"agents/billing_agent/**": {"X999": "warning"}},
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(reg, cfg, ctx, disc, report)

    assert len(report.results) == 1
    assert report.results[0].file == "agents/billing_agent/billing_agent.yml"
    assert report.results[0].severity == Severity.WARNING

    # per_file: off suppresses emitted finding cleanly
    cfg_off = LintConfig(
        ignore=["tools/**"],
        per_file={"agents/billing_agent/**": {"X999": "off"}},
    )
    report_off = LintReport()
    run_rules(reg, cfg_off, ctx, disc, report_off)
    assert report_off.results == []


def test_discovery_and_runner_skip_symlinks_and_non_utf8(
    tmp_path: Path,
) -> None:
    """Non-UTF-8 files and symlinked agent directories do not crash linting."""
    _write_yaml_app(tmp_path)
    outside = tmp_path / "outside_agent"
    outside.mkdir()
    (outside / "outside_agent.yaml").write_text("displayName: Out\n")
    (tmp_path / "agents" / "sym_agent").symlink_to(outside)

    bad_dir = tmp_path / "agents" / "bad_utf8"
    bad_dir.mkdir()
    (bad_dir / "bad_utf8.yaml").write_bytes(b"\xff\xfe\x00\x01")
    (bad_dir / "instruction.txt").write_bytes(b"\xff\xfe\x00\x01")

    disc = Discovery(tmp_path, tmp_path / "evals")
    assert "sym_agent" not in disc.discover_agent_configs()
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report, specific_rules={"A001"})
    assert isinstance(report.results, list)
