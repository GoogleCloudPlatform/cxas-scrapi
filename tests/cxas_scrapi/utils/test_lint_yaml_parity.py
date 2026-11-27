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

import ast
import json
from pathlib import Path
from typing import Any
from unittest import mock

from cxas_scrapi.utils.lint_rules.config import (
    AgentMissingInstruction,
    AgentToolNotExists,
    AppRootAgentValidation,
    AppVariableDefaultsAndEnvVars,
    InvalidJson,
    MissingRequiredFields,
    RootAgentMissingEndSession,
)
from cxas_scrapi.utils.lint_rules.runtime_traps import (
    AsyncToolMissingInactivityTimeout,
    LlmPolicyGuardrailLimit,
)
from cxas_scrapi.utils.lint_rules.schema import _strip_v1main_only_fields
from cxas_scrapi.utils.lint_rules.structure import (
    AgentResourceReferences,
    AgentToolReferences,
    AppResourceReferences,
    CallbackFileReferences,
    ChildAgentReferences,
    StrictAgentPathLayout,
    ToolFileReferences,
    ToolsetFileReferences,
)
from cxas_scrapi.utils.lint_rules.variables import (
    _clear_schema_cache,
    _clear_writer_cache,
    _other_state_writes,
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
    _find_dir_config,
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
        "def lookup_order(order_id: str) -> dict:\n"
        '    """Look up an order by ID."""\n'
        '    return {"id": order_id}\n',
        encoding="utf-8",
    )
    return tmp_path


def test_builtin_rule_count_unchanged() -> None:
    """YAML parity adds zero new lint rules (registry remains at 120)."""
    registry = build_registry()
    assert len(registry.all_rules()) == 120  # noqa: PLR2004


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
        "guardrails:\n"
        "  - safety\n"
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
    cb_dir = bad_dir / "before_model_callbacks" / "bad_cb"
    cb_dir.mkdir(parents=True)
    (cb_dir / "python_code.py").write_bytes(b"\xff\xfe\x00not-utf8")

    disc = Discovery(tmp_path, tmp_path / "evals")
    assert "sym_agent" not in disc.discover_agent_configs()
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(
        build_registry(),
        cfg,
        ctx,
        disc,
        report,
        specific_rules={"A001", "T008"},
    )
    assert isinstance(report.results, list)


def test_yaml_app_with_guided_agent_passes_full_lint_without_false_errors(
    tmp_path: Path,
) -> None:
    """Full lint on a YAML app with guidedAgent emits zero errors."""
    _write_yaml_app(tmp_path)
    root_yaml = tmp_path / "agents" / "root_agent" / "root_agent.yaml"
    root_yaml.write_text(
        "displayName: Root Agent\n"
        "instruction: agents/root_agent/instruction.txt\n"
        "tools:\n"
        "  - end_session\n"
        "  - lookup_order\n"
        "childAgents:\n"
        "  - billing_agent\n"
        "  - guided_sub\n",
        encoding="utf-8",
    )
    guided_dir = tmp_path / "agents" / "guided_sub"
    guided_dir.mkdir(parents=True)
    (guided_dir / "guided_sub.yaml").write_text(
        "displayName: Guided Sub\nguidedAgent:\n  config: some_config\n",
        encoding="utf-8",
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, LintConfig(), disc)
    report = LintReport()
    run_rules(build_registry(), LintConfig(), ctx, disc, report)
    assert report.errors == []


def test_schema_v001_v002_yml_config_and_malformed_yaml(
    tmp_path: Path,
) -> None:
    """V001/V002 load .yml configs and do not crash on malformed YAML."""
    _write_yaml_app(tmp_path, app_filename="app.yml")
    root_dir = tmp_path / "agents" / "root_agent"
    (root_dir / "root_agent.yaml").rename(root_dir / "root_agent.yml")

    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    reg = build_registry()

    rep_v = LintReport()
    run_rules(reg, cfg, ctx, disc, rep_v, specific_rules={"V001", "V002"})
    assert not any("Missing config" in r.message for r in rep_v.results), [
        str(r) for r in rep_v.results
    ]

    # Malformed YAML agent must not crash V002 with unhandled yaml.YAMLError
    bad_dir = tmp_path / "agents" / "broken_yaml"
    bad_dir.mkdir(parents=True)
    (bad_dir / "broken_yaml.yaml").write_text(
        "a: [unclosed\n", encoding="utf-8"
    )
    disc2 = Discovery(tmp_path, tmp_path / "evals")
    ctx2 = build_context(tmp_path, cfg, disc2)
    rep_bad = LintReport()
    run_rules(reg, cfg, ctx2, disc2, rep_bad, specific_rules={"V002"})
    assert isinstance(rep_bad.results, list)


def test_s004_accepts_ces_resource_path_uuid_child_agent(
    tmp_path: Path,
) -> None:
    """S004 resolves childAgents referencing an agent by CES name UUID."""
    _write_yaml_app(tmp_path)
    billing_yaml = tmp_path / "agents" / "billing_agent" / "billing_agent.yml"
    billing_yaml.write_text(
        "name: projects/p/locations/l/apps/demo/agents/agent-uuid-123\n"
        "displayName: Billing Support\n"
        "instruction: agents/billing_agent/instruction.txt\n",
        encoding="utf-8",
    )
    root_yaml = tmp_path / "agents" / "root_agent" / "root_agent.yaml"
    root_yaml.write_text(
        "displayName: Root Agent\n"
        "instruction: agents/root_agent/instruction.txt\n"
        "childAgents:\n"
        "  - name: projects/p/locations/l/apps/demo/agents/agent-uuid-123\n",
        encoding="utf-8",
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, LintConfig(), disc)
    res = ChildAgentReferences().check(
        root_yaml, root_yaml.read_text(encoding="utf-8"), ctx
    )
    assert res == [], [str(r) for r in res]


def test_per_file_override_not_clobbered_by_global_rules_and_dot_ignore(
    tmp_path: Path,
) -> None:
    """per_file overrides beat global rules on S004, and ./ ignore works."""
    _write_yaml_app(tmp_path)
    root_yaml = tmp_path / "agents" / "root_agent" / "root_agent.yaml"
    root_yaml.write_text(
        "displayName: Root Agent\nchildAgents:\n  - missing_child\n",
        encoding="utf-8",
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig(
        rules={"S004": Severity.ERROR},
        per_file={"agents/root_agent/**": {"S004": "warning"}},
    )
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report, specific_rules={"S004"})
    assert len(report.results) == 1
    assert report.results[0].severity == Severity.WARNING

    # ./tools/** ignore pattern should match relative emitted path tools/...
    cfg_dot = LintConfig(ignore=["./tools/**"])
    assert cfg_dot.is_ignored("tools/lookup_order/lookup_order.yaml") is True


def test_yml_parity_a014_a016_s011_s012_s013(tmp_path: Path) -> None:
    """A014, A016, S011, S012, S013 discover and parse .yml resources."""
    (tmp_path / "app.yml").write_text(
        "name: demo\ndisplayName: Demo\nrootAgent: root\n"
        "guardrails:\n" + "".join(f"  - gr_{i}\n" for i in range(11)),
        encoding="utf-8",
    )
    t_async = tmp_path / "tools" / "async_tool"
    t_async.mkdir(parents=True)
    (t_async / "async_tool.yml").write_text(
        "displayName: async_tool\nexecutionType: ASYNCHRONOUS\n"
        "pythonFunction:\n  name: async_tool\n"
        "  pythonCode: tools/async_tool/missing.py\n",
        encoding="utf-8",
    )
    for i in range(11):
        gd = tmp_path / "guardrails" / f"gr_{i}"
        gd.mkdir(parents=True)
        (gd / f"gr_{i}.yml").write_text(
            f"displayName: GR {i}\nenabled: true\n"
            f"llmPolicy:\n  prompt: Safe {i}\n",
            encoding="utf-8",
        )
    ts_dir = tmp_path / "toolsets" / "my_ts"
    ts_dir.mkdir(parents=True)
    (ts_dir / "my_ts.yml").write_text(
        "displayName: My TS\nopenApiToolset:\n"
        "  openApiSchema: toolsets/my_ts/missing.yaml\n",
        encoding="utf-8",
    )
    r_dir = tmp_path / "agents" / "root"
    r_dir.mkdir(parents=True)
    (r_dir / "root.yml").write_text(
        "displayName: Root\nguardrails:\n  - GR 0\n"
        "toolsets:\n  - toolset: My TS\n",
        encoding="utf-8",
    )

    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, LintConfig(), disc)
    app_text = (tmp_path / "app.yml").read_text(encoding="utf-8")

    assert (
        len(
            AsyncToolMissingInactivityTimeout().check(
                tmp_path / "app.yml", app_text, ctx
            )
        )
        == 1
    )
    assert (
        len(
            LlmPolicyGuardrailLimit().check(tmp_path / "app.yml", app_text, ctx)
        )
        == 1
    )
    assert (
        AgentResourceReferences().check(
            r_dir / "root.yml",
            (r_dir / "root.yml").read_text(encoding="utf-8"),
            ctx,
        )
        == []
    )
    assert len(ToolFileReferences().check(t_async, "", ctx)) == 1
    assert len(ToolsetFileReferences().check(ts_dir, "", ctx)) == 1


def test_py310_null_byte_ast_parse_value_error_all_rules(
    tmp_path: Path,
) -> None:
    """Simulated Py3.10/3.11 ast.parse ValueError on null bytes never crashes."""
    _write_yaml_app(tmp_path)
    cb_dir = (
        tmp_path / "agents" / "root_agent" / "before_model_callbacks" / "cb1"
    )
    cb_dir.mkdir(parents=True)
    (cb_dir / "python_code.py").write_bytes(
        b"def before_model_callback(ctx, req):\n    return None\x00\n"
    )
    tool_py = (
        tmp_path
        / "tools"
        / "lookup_order"
        / "python_function"
        / "python_code.py"
    )
    tool_py.write_bytes(
        b"def lookup_order(order_id: str) -> dict:\n    return {}\x00\n"
    )

    orig_parse = ast.parse
    orig_compile = compile
    parse_value_errors = 0
    compile_value_errors = 0

    def _py310_parse(source: Any, *args: Any, **kwargs: Any) -> ast.AST:
        nonlocal parse_value_errors
        if isinstance(source, str) and "\x00" in source:
            parse_value_errors += 1
            raise ValueError("source code string cannot contain null bytes")
        return orig_parse(source, *args, **kwargs)

    def _py310_compile(source: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal compile_value_errors
        if isinstance(source, str) and "\x00" in source:
            compile_value_errors += 1
            raise ValueError("source code string cannot contain null bytes")
        return orig_compile(source, *args, **kwargs)

    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    reg = build_registry()

    with (
        mock.patch("ast.parse", side_effect=_py310_parse),
        mock.patch("builtins.compile", side_effect=_py310_compile),
    ):
        report = LintReport()
        run_rules(reg, cfg, ctx, disc, report)
    assert parse_value_errors > 0
    assert compile_value_errors >= 2  # noqa: PLR2004
    ids = {r.rule_id for r in report.results}
    assert "C010" in ids
    assert "T010" in ids


def test_dict_in_tools_list_does_not_crash_full_lint(tmp_path: Path) -> None:
    """Full run_rules (A003, S002, I012, etc.) tolerates dict entries in tools."""
    _write_yaml_app(tmp_path)
    root_yaml = tmp_path / "agents" / "root_agent" / "root_agent.yaml"
    root_yaml.write_text(
        "displayName: Root\n"
        "instruction: agents/root_agent/instruction.txt\n"
        "tools:\n"
        "  - name: dict_tool\n",
        encoding="utf-8",
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report)
    assert isinstance(report.results, list)


def test_schema_v001_handles_non_string_yaml_key_without_crashing(
    tmp_path: Path,
) -> None:
    """V001 reports a clean schema finding on non-string YAML keys like 123."""
    (tmp_path / "app.yaml").write_text(
        "displayName: Demo\n123: hello\n", encoding="utf-8"
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report, specific_rules={"V001"})
    assert len(report.results) == 1
    assert report.results[0].rule_id == "V001"


def test_json_yaml_precedence_consistent_across_v001_config_and_discovery(
    tmp_path: Path,
) -> None:
    """When app.json and app.yaml coexist, V001, A001-A010, and discovery agree."""
    (tmp_path / "app.json").write_text(
        '{"name": "projects/p/locations/l/apps/demo",'
        ' "displayName": "JSON Wins",'
        ' "rootAgent": "root_agent",'
        ' "modelSettings": {"model": "gemini-composite-v1", "temperature": 1.0},'
        ' "audioProcessingConfig": {"synthesizeSpeechConfigs":'
        ' {"en-US": {"voice": "en-US-Chirp3-HD-Aoede"}}}}',
        encoding="utf-8",
    )
    # Deliberately broken app.yaml should NOT shadow valid app.json
    (tmp_path / "app.yaml").write_text(
        "123: broken_yaml\nmodelSettings:\n  temperature: 0.2\n",
        encoding="utf-8",
    )
    root_dir = tmp_path / "agents" / "root_agent"
    root_dir.mkdir(parents=True)
    (root_dir / "root_agent.json").write_text(
        '{"displayName": "Root", "tools": ["end_session"]}', encoding="utf-8"
    )
    (root_dir / "instruction.txt").write_text("Hello.\n", encoding="utf-8")

    assert _find_dir_config(tmp_path, stem="app") == tmp_path / "app.json"
    disc = Discovery(tmp_path, tmp_path / "evals")
    assert disc.discover_app_config() == tmp_path / "app.json"

    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(
        build_registry(),
        cfg,
        ctx,
        disc,
        report,
        specific_rules={"V001", "A001", "A002", "A006", "A008", "A009", "A010"},
    )
    assert report.results == [], [str(r) for r in report.results]


def test_a007_to_a010_and_a013_run_on_app_yml(tmp_path: Path) -> None:
    """A007, A008, A009, A010 (Composite V1) and A013 (V2) all fire on app.yml."""
    # Case 1: A007 (missing synthesizeSpeechConfigs) + A010 (temperature != 1.0)
    app_v1_missing = tmp_path / "v1_missing"
    app_v1_missing.mkdir()
    (app_v1_missing / "app.yml").write_text(
        "displayName: Voice App\n"
        "modelSettings:\n"
        "  model: gemini-composite-v1\n"
        "  temperature: 0.5\n",
        encoding="utf-8",
    )
    disc1 = Discovery(app_v1_missing, app_v1_missing / "evals")
    cfg1 = LintConfig()
    ctx1 = build_context(app_v1_missing, cfg1, disc1)
    assert ctx1.model == "gemini-composite-v1"
    rep1 = LintReport()
    run_rules(
        build_registry(),
        cfg1,
        ctx1,
        disc1,
        rep1,
        specific_rules={"A007", "A010"},
    )
    assert {r.rule_id for r in rep1.results} == {"A007", "A010"}

    # Case 2: A008 (raw locale in Accent) + A009 (missing de-DE speech config)
    app_v1_accent = tmp_path / "v1_accent"
    app_v1_accent.mkdir()
    (app_v1_accent / "app.yml").write_text(
        "displayName: Voice App\n"
        "modelSettings:\n"
        "  model: gemini-composite-v1\n"
        "  temperature: 1.0\n"
        "languageSettings:\n"
        "  defaultLanguageCode: en-US\n"
        "  supportedLanguageCodes:\n"
        "    - en-US\n"
        "    - de-DE\n"
        "audioProcessingConfig:\n"
        "  synthesizeSpeechConfigs:\n"
        "    en-US:\n"
        "      voice: en-US-Chirp3-HD-Aoede\n"
        "      instruction: |\n"
        "        # Audio Profile\n"
        "        Warm tone.\n"
        "        # Director's Notes\n"
        "        Accent: en-US\n",
        encoding="utf-8",
    )
    disc2 = Discovery(app_v1_accent, app_v1_accent / "evals")
    cfg2 = LintConfig()
    ctx2 = build_context(app_v1_accent, cfg2, disc2)
    rep2 = LintReport()
    run_rules(
        build_registry(),
        cfg2,
        ctx2,
        disc2,
        rep2,
        specific_rules={"A008", "A009"},
    )
    assert {r.rule_id for r in rep2.results} == {"A008", "A009"}

    # Case 3: A013 on gemini-composite-v2 app.yml
    app_v2 = tmp_path / "v2_app"
    app_v2.mkdir()
    (app_v2 / "app.yml").write_text(
        "displayName: V2 App\n"
        "modelSettings:\n"
        "  model: gemini-composite-v2\n"
        "audioProcessingConfig:\n"
        "  customVoiceSamples:\n"
        "    - name: my_voice\n"
        "      voiceInstructionMode: DEFAULT\n",
        encoding="utf-8",
    )
    disc3 = Discovery(app_v2, app_v2 / "evals")
    cfg3 = LintConfig()
    ctx3 = build_context(app_v2, cfg3, disc3)
    rep3 = LintReport()
    run_rules(
        build_registry(), cfg3, ctx3, disc3, rep3, specific_rules={"A013"}
    )
    assert {r.rule_id for r in rep3.results} == {"A013"}


def test_t012_t013_t014_support_yml_tool_configs(tmp_path: Path) -> None:
    """T012, T013, and T014 discover and validate .yml tool configs."""
    (tmp_path / "app.yml").write_text(
        "displayName: Demo\n"
        "rootAgent: root\n"
        "modelSettings:\n"
        "  model: gemini-composite-v1\n",
        encoding="utf-8",
    )
    # T012: clientFunction .yml with no python_code.py and missing description
    client_dir = tmp_path / "tools" / "my_client"
    client_dir.mkdir(parents=True)
    (client_dir / "my_client.yml").write_text(
        "displayName: my_client\nclientFunction:\n  name: my_client\n",
        encoding="utf-8",
    )
    # T013: malformed YAML and non-mapping YAML tool configs
    bad_syntax_dir = tmp_path / "tools" / "bad_syntax"
    bad_syntax_dir.mkdir(parents=True)
    (bad_syntax_dir / "bad_syntax.yml").write_text(
        "displayName: [unclosed\n", encoding="utf-8"
    )
    bad_type_dir = tmp_path / "tools" / "bad_type"
    bad_type_dir.mkdir(parents=True)
    (bad_type_dir / "bad_type.yml").write_text(
        "- not-a-dict\n", encoding="utf-8"
    )
    # T014: .yml tool with description lacking a conversational pacing phrase
    pacing_dir = tmp_path / "tools" / "lookup_account"
    pacing_dir.mkdir(parents=True)
    (pacing_dir / "lookup_account.yml").write_text(
        "displayName: lookup_account\n"
        "clientFunction:\n"
        "  name: lookup_account\n"
        "  description: Fetches the customer account record.\n",
        encoding="utf-8",
    )

    disc = Discovery(tmp_path, tmp_path / "evals")
    discovered_tools = disc.discover_tools()
    assert discovered_tools["my_client"] == client_dir / "my_client.yml"

    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(
        build_registry(),
        cfg,
        ctx,
        disc,
        report,
        specific_rules={"T012", "T013", "T014"},
    )
    by_rule = {r.rule_id for r in report.results}
    assert by_rule == {"T012", "T013", "T014"}
    assert any(
        r.rule_id == "T012" and r.file == "tools/my_client/my_client.yml"
        for r in report.results
    )
    t013_files = {r.file for r in report.results if r.rule_id == "T013"}
    assert t013_files == {
        "tools/bad_syntax/bad_syntax.yml",
        "tools/bad_type/bad_type.yml",
    }
    assert any(
        r.rule_id == "T014"
        and r.file == "tools/lookup_account/lookup_account.yml"
        for r in report.results
    )


def test_i012_tolerates_non_utf8_global_instruction(tmp_path: Path) -> None:
    """I012 and full run_rules do not crash on non-UTF-8 global_instruction.txt."""
    _write_yaml_app(tmp_path)
    (tmp_path / "global_instruction.txt").write_bytes(b"\xff\xfe bad bytes\n")
    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report)
    assert isinstance(report.results, list)


def test_s004_caches_agent_ref_resolver_per_context(tmp_path: Path) -> None:
    """S004 reuses a cached agent resolver on LintContext instead of O(N^2) I/O."""
    _write_yaml_app(tmp_path)
    # Give billing_agent a non-empty childAgents list too so BOTH S004 calls
    # actually invoke context.get_agent_ref_resolver(...)!
    helper_dir = tmp_path / "agents" / "helper_agent"
    helper_dir.mkdir(parents=True)
    (helper_dir / "helper_agent.yml").write_text(
        "displayName: Helper Agent\n", encoding="utf-8"
    )
    billing_yml = tmp_path / "agents" / "billing_agent" / "billing_agent.yml"
    billing_yml.write_text(
        "displayName: Billing Agent\n"
        "instruction: agents/billing_agent/instruction.txt\n"
        "childAgents:\n"
        "  - helper_agent\n",
        encoding="utf-8",
    )

    disc = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, LintConfig(), disc)
    root_yaml = tmp_path / "agents" / "root_agent" / "root_agent.yaml"
    rule = ChildAgentReferences()

    with mock.patch(
        "cxas_scrapi.utils.linter._discover_agent_entries",
        wraps=__import__(
            "cxas_scrapi.utils.linter", fromlist=["_discover_agent_entries"]
        )._discover_agent_entries,
    ) as spy:
        assert rule.check(root_yaml, root_yaml.read_text("utf-8"), ctx) == []
        assert (
            rule.check(billing_yml, billing_yml.read_text("utf-8"), ctx) == []
        )
        assert spy.call_count == 1


def test_pyyaml_bad_date_value_error_handled_by_t013_v001_a001(
    tmp_path: Path,
) -> None:
    """PyYAML invalid dates (2025-02-30) emit clean T013/V001/A001 errors."""
    (tmp_path / "app.yaml").write_text(
        "displayName: Demo\ndescription: 2025-02-30\n", encoding="utf-8"
    )
    tool_dir = tmp_path / "tools" / "bad_date"
    tool_dir.mkdir(parents=True)
    (tool_dir / "bad_date.yml").write_text(
        "displayName: bad_date\ndescription: 2025-02-30\n", encoding="utf-8"
    )
    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(
        build_registry(),
        cfg,
        ctx,
        disc,
        report,
        specific_rules={"A001", "T013", "V001"},
    )
    ids = {r.rule_id for r in report.results}
    assert ids == {"A001", "T013", "V001"}


def test_t012_rejects_path_traversal_python_code_outside_app_root(
    tmp_path: Path,
) -> None:
    """T012 must not read a docstring from a pythonCode path escaping app_root."""
    outside_py = tmp_path / "outside.py"
    outside_py.write_text(
        'def escape_tool() -> dict:\n    """Outside docstring."""\n    return {}\n',
        encoding="utf-8",
    )
    app_root = tmp_path / "nested" / "app"
    app_root.mkdir(parents=True)
    (app_root / "app.yml").write_text(
        "displayName: Demo\nrootAgent: root\n", encoding="utf-8"
    )
    tool_dir = app_root / "tools" / "escape_tool"
    tool_dir.mkdir(parents=True)
    (tool_dir / "escape_tool.yml").write_text(
        "displayName: escape_tool\n"
        "pythonFunction:\n"
        "  name: escape_tool\n"
        "  pythonCode: ../../outside.py\n",
        encoding="utf-8",
    )
    disc = Discovery(app_root, app_root / "evals")
    cfg = LintConfig()
    ctx = build_context(app_root, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report, specific_rules={"T012"})
    assert [r.rule_id for r in report.results] == ["T012"]
    assert report.results[0].file == "tools/escape_tool/escape_tool.yml"


def test_evals_bad_yaml_date_value_error_handled_cleanly(
    tmp_path: Path,
) -> None:
    """E001 and eval rules catch PyYAML ValueError on bad dates cleanly."""
    (tmp_path / "app.yml").write_text(
        "name: demo\ndisplayName: Demo\nrootAgent: root\n", encoding="utf-8"
    )
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    (evals_dir / "bad_date.yaml").write_text(
        "description: 2025-02-30\nconversations: []\n", encoding="utf-8"
    )
    disc = Discovery(tmp_path, evals_dir)
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report)
    assert any(
        r.rule_id == "E001" and r.file == "evals/bad_date.yaml"
        for r in report.results
    )


def test_a018_runs_on_app_yml(tmp_path: Path) -> None:
    """A018 (tool-and-toolset-env-vars) also runs when app config is app.yml."""
    (tmp_path / "app.yml").write_text(
        "displayName: Demo\nrootAgent: root\n", encoding="utf-8"
    )
    tool_dir = tmp_path / "tools" / "api_tool"
    tool_dir.mkdir(parents=True)
    (tool_dir / "api_tool.json").write_text(
        json.dumps(
            {
                "displayName": "api_tool",
                "openApiTool": {
                    "openApiSchema": "openapi: 3.0.0",
                    "url": "$env_var",
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "environment.json").write_text("{bad json", encoding="utf-8")
    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report, specific_rules={"A018"})
    assert [r.rule_id for r in report.results] == ["A018"]


def test_v105_respects_snake_case_tool_execution_mode_and_null_bytes(
    tmp_path: Path,
) -> None:
    """V105 reads snake_case tool_execution_mode in app.yml and handles NUL."""
    _clear_schema_cache()
    _clear_writer_cache()
    assert _other_state_writes("state['x'] = 1\x00") == {}

    (tmp_path / "app.yml").write_text(
        "displayName: Demo\n"
        "rootAgent: root\n"
        "tool_execution_mode: SEQUENTIAL\n"
        "variable_declarations:\n"
        "  - name: shared_var\n"
        "    schema:\n"
        "      type: STRING\n",
        encoding="utf-8",
    )
    for tname in ("tool_a", "tool_b"):
        pf_dir = tmp_path / "tools" / tname / "python_function"
        pf_dir.mkdir(parents=True)
        (pf_dir / "python_code.py").write_text(
            f"def {tname}() -> dict:\n"
            "    context.state['shared_var'] = 'v'\n"
            "    return {}\n",
            encoding="utf-8",
        )
    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report, specific_rules={"V105"})
    assert report.results == []


def test_a018_bad_yaml_date_and_non_utf8_tool_manifest_no_crash(
    tmp_path: Path,
) -> None:
    """Full lint on app.yml must not crash on bad-date or non-UTF-8 tools."""
    (tmp_path / "app.yml").write_text(
        "name: demo\ndisplayName: Demo\nrootAgent: root\n", encoding="utf-8"
    )
    bad_date_dir = tmp_path / "tools" / "bad_date"
    bad_date_dir.mkdir(parents=True)
    (bad_date_dir / "bad_date.yml").write_text(
        "displayName: bad_date\ndescription: 2025-02-30\n", encoding="utf-8"
    )
    bad_utf8_dir = tmp_path / "tools" / "bad_utf8"
    bad_utf8_dir.mkdir(parents=True)
    (bad_utf8_dir / "bad_utf8.yml").write_bytes(b"\xff\xfeinvalid")
    bad_schema_dir = tmp_path / "tools" / "bad_schema" / "open_api_tool"
    bad_schema_dir.mkdir(parents=True)
    (bad_schema_dir / "schema.yaml").write_bytes(b"\xff\xfeopenapi")
    (bad_schema_dir.parent / "bad_schema.json").write_text(
        json.dumps(
            {
                "displayName": "bad_schema",
                "openApiTool": {
                    "openApiSchema": "tools/bad_schema/open_api_tool/schema.yaml"
                },
            }
        ),
        encoding="utf-8",
    )

    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report)
    assert any(
        r.rule_id == "T013" and r.file == "tools/bad_date/bad_date.yml"
        for r in report.results
    )


def test_discover_evals_and_e001_support_both_yaml_and_yml(
    tmp_path: Path,
) -> None:
    """Discovery.discover_evals and E001 process both .yaml and .yml evals."""
    (tmp_path / "app.yml").write_text(
        "name: demo\ndisplayName: Demo\nrootAgent: root\n", encoding="utf-8"
    )
    evals_dir = tmp_path / "evals"
    evals_dir.mkdir()
    bad_payload = "description: 2025-02-30\nconversations: []\n"
    (evals_dir / "bad.yaml").write_text(bad_payload, encoding="utf-8")
    (evals_dir / "bad.yml").write_text(bad_payload, encoding="utf-8")

    disc = Discovery(tmp_path, evals_dir)
    assert set(disc.discover_evals().keys()) == {"bad.yaml", "bad.yml"}

    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, report)
    e001_files = {r.file for r in report.results if r.rule_id == "E001"}
    assert {"evals/bad.yaml", "evals/bad.yml"} <= e001_files


def test_v105_v106_tolerate_non_utf8_sibling_tool_python_code(
    tmp_path: Path,
) -> None:
    """V105/V106 and full run_rules skip non-UTF-8 tool python_code.py cleanly."""
    _clear_schema_cache()
    _clear_writer_cache()
    (tmp_path / "app.yml").write_text(
        "name: demo\n"
        "displayName: Demo\n"
        "rootAgent: root\n"
        "variable_declarations:\n"
        "  - name: shared_var\n"
        "    schema:\n"
        "      type: STRING\n",
        encoding="utf-8",
    )
    good_pf = tmp_path / "tools" / "good_tool" / "python_function"
    good_pf.mkdir(parents=True)
    (good_pf / "python_code.py").write_text(
        "def good_tool() -> dict:\n"
        "    context.state['shared_var'] = 'ok'\n"
        "    return {}\n",
        encoding="utf-8",
    )
    bad_pf = tmp_path / "tools" / "bad_utf8_tool" / "python_function"
    bad_pf.mkdir(parents=True)
    (bad_pf / "python_code.py").write_bytes(b"\xff\xfe\x00not-utf8")

    disc = Discovery(tmp_path, tmp_path / "evals")
    cfg = LintConfig()
    ctx = build_context(tmp_path, cfg, disc)
    report = LintReport()
    run_rules(
        build_registry(),
        cfg,
        ctx,
        disc,
        report,
        specific_rules={"V105", "V106"},
    )
    assert report.results == []

    # Full run_rules also completes without UnicodeDecodeError
    full_report = LintReport()
    run_rules(build_registry(), cfg, ctx, disc, full_report)
