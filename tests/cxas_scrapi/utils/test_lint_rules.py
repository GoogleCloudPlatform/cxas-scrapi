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

"""Tests for individual lint rules."""

from unittest.mock import patch

import pytest

from cxas_scrapi.utils.linter import LintContext


@pytest.fixture
def context(tmp_path):
    """Minimal LintContext for rule testing."""
    return LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        all_agent_names={"root_agent", "billing_agent"},
        all_agent_display_names={"root agent", "billing agent"},
        all_tool_names={"get_balance", "transfer_funds"},
        all_tool_dirs={},
    )


# ── Instruction Rules ────────────────────────────────────────────────────


def test_i001_missing_tags(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.instructions import RequiredXmlStructure  # noqa: PLC0415,I001

    rule = RequiredXmlStructure()
    f = tmp_path / "instruction.txt"
    f.write_text("Just some text without tags.")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 3  # noqa: PLR2004
    tags = {r.message for r in results}
    assert any("<role>" in t for t in tags)
    assert any("<persona>" in t for t in tags)
    assert any("<taskflow>" in t for t in tags)


def test_i001_all_tags_present(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.instructions import RequiredXmlStructure  # noqa: PLC0415,I001

    rule = RequiredXmlStructure()
    f = tmp_path / "instruction.txt"
    f.write_text(
        "<role>test</role><persona>test</persona><taskflow>test</taskflow>"
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_i002_taskflow_without_children(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.instructions import TaskflowChildren  # noqa: PLC0415,I001

    rule = TaskflowChildren()
    f = tmp_path / "instruction.txt"
    f.write_text("<taskflow>no children here</taskflow>")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "no <subtask>" in results[0].message


def test_i002_taskflow_with_subtask(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.instructions import TaskflowChildren  # noqa: PLC0415,I001

    rule = TaskflowChildren()
    f = tmp_path / "instruction.txt"
    f.write_text(
        "<taskflow><subtask name='greet'><step>hi</step></subtask></taskflow>"
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_i003_excessive_if_else(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.instructions import ExcessiveIfElse  # noqa: PLC0415,I001

    rule = ExcessiveIfElse()
    f = tmp_path / "instruction.txt"
    content = "\n".join(
        [
            "IF condition1 ELSE do something",
            "IF condition2 ELSE do another",
            "IF condition3 ELSE do third",
        ]
    )
    f.write_text(content)

    results = rule.check(f, content, context)
    assert len(results) == 1
    assert "3 IF/ELSE" in results[0].message


def test_i003_few_if_else_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.instructions import ExcessiveIfElse  # noqa: PLC0415,I001

    rule = ExcessiveIfElse()
    f = tmp_path / "instruction.txt"
    content = "IF something ELSE other\nIF another ELSE thing"
    f.write_text(content)

    results = rule.check(f, content, context)
    assert len(results) == 0


def test_i006_hardcoded_phone(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.instructions import HardcodedData  # noqa: PLC0415,I001

    rule = HardcodedData()
    f = tmp_path / "instruction.txt"
    content = "Call us at 555-123-4567 for support."
    f.write_text(content)

    results = rule.check(f, content, context)
    assert len(results) == 1
    assert "phone number" in results[0].message


def test_i006_no_hardcoded_data(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.instructions import HardcodedData  # noqa: PLC0415,I001

    rule = HardcodedData()
    f = tmp_path / "instruction.txt"
    content = "Use the phone number from the tool response."
    f.write_text(content)

    results = rule.check(f, content, context)
    assert len(results) == 0


def test_i008_invalid_agent_ref(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.instructions import InvalidAgentRef  # noqa: PLC0415,I001

    rule = InvalidAgentRef()
    f = tmp_path / "instruction.txt"
    content = "Transfer to {@AGENT: nonexistent_agent}."
    f.write_text(content)

    results = rule.check(f, content, context)
    assert len(results) == 1
    assert "nonexistent_agent" in results[0].message


def test_i008_valid_agent_ref(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.instructions import InvalidAgentRef  # noqa: PLC0415,I001

    rule = InvalidAgentRef()
    f = tmp_path / "instruction.txt"
    content = "Transfer to {@AGENT: billing_agent}."
    f.write_text(content)

    results = rule.check(f, content, context)
    assert len(results) == 0


def test_i014_no_date_anywhere(tmp_path, context):
    """No current_date in global or any instruction → flag."""
    from cxas_scrapi.utils.lint_rules.instructions import MissingCurrentDate  # noqa: PLC0415,I001

    rule = MissingCurrentDate()
    agent_dir = tmp_path / "agents" / "root_agent"
    agent_dir.mkdir(parents=True)
    f = agent_dir / "instruction.txt"
    content = "Just some instructions."
    f.write_text(content)

    results = rule.check(f, content, context)
    assert len(results) == 1
    assert "No current_date reference" in results[0].message


def test_i014_in_global_only(tmp_path, context):
    """current_date in global_instruction.txt → no flag on agent."""
    from cxas_scrapi.utils.lint_rules.instructions import MissingCurrentDate  # noqa: PLC0415,I001

    rule = MissingCurrentDate()
    (tmp_path / "global_instruction.txt").write_text("Today is {current_date}.")
    f = tmp_path / "instruction.txt"
    f.write_text("No date here.")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_i014_in_global_and_agent(tmp_path, context):
    """current_date in both global and agent → no flag."""
    from cxas_scrapi.utils.lint_rules.instructions import MissingCurrentDate  # noqa: PLC0415,I001

    rule = MissingCurrentDate()
    (tmp_path / "global_instruction.txt").write_text("Today is {current_date}.")
    f = tmp_path / "instruction.txt"
    f.write_text("Date: {current_date}.")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_i014_in_all_agents_not_global(tmp_path, context):
    """current_date in all agent instructions but not global → no flag."""
    from cxas_scrapi.utils.lint_rules.instructions import MissingCurrentDate  # noqa: PLC0415,I001

    rule = MissingCurrentDate()
    # Two agents, both have current_date
    for name in ("agent_a", "agent_b"):
        d = tmp_path / "agents" / name
        d.mkdir(parents=True)
        (d / "instruction.txt").write_text("Date: {current_date}.")
    # Global does not have it
    gi = tmp_path / "global_instruction.txt"
    gi.write_text("No date here.")

    results = rule.check(gi, gi.read_text(), context)
    assert len(results) == 0


def test_i014_in_some_agents_not_global(tmp_path, context):
    """current_date in one agent but not another, not global → flag both."""
    from cxas_scrapi.utils.lint_rules.instructions import MissingCurrentDate  # noqa: PLC0415,I001

    rule = MissingCurrentDate()
    # agent_a has it, agent_b does not
    a = tmp_path / "agents" / "agent_a"
    a.mkdir(parents=True)
    (a / "instruction.txt").write_text("Date: {current_date}.")
    b = tmp_path / "agents" / "agent_b"
    b.mkdir(parents=True)
    (b / "instruction.txt").write_text("No date here.")
    # Global does not have it
    gi = tmp_path / "global_instruction.txt"
    gi.write_text("No date here.")

    # global_instruction.txt should be flagged
    results_gi = rule.check(gi, gi.read_text(), context)
    assert len(results_gi) == 1

    # agent_b should be flagged
    f_b = b / "instruction.txt"
    results_b = rule.check(f_b, f_b.read_text(), context)
    assert len(results_b) == 1

    # agent_a should NOT be flagged (it has current_date)
    f_a = a / "instruction.txt"
    results_a = rule.check(f_a, f_a.read_text(), context)
    assert len(results_a) == 0


def test_i014_accepts_double_brace_syntax(tmp_path, context):
    """{{current_date}} syntax is also valid."""
    from cxas_scrapi.utils.lint_rules.instructions import MissingCurrentDate  # noqa: PLC0415,I001

    rule = MissingCurrentDate()
    f = tmp_path / "global_instruction.txt"
    f.write_text("Today is {{current_date}}.")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_i014_skips_non_instruction_files(tmp_path, context):
    """Rule only applies to instruction.txt and global_instruction.txt."""
    from cxas_scrapi.utils.lint_rules.instructions import MissingCurrentDate  # noqa: PLC0415,I001

    rule = MissingCurrentDate()
    f = tmp_path / "python_code.py"
    f.write_text("# no date here")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_i014_no_global_instruction_file(tmp_path, context):
    """No global_instruction.txt exists, agent missing date → flag."""
    from cxas_scrapi.utils.lint_rules.instructions import MissingCurrentDate  # noqa: PLC0415,I001

    rule = MissingCurrentDate()
    agent_dir = tmp_path / "agents" / "root_agent"
    agent_dir.mkdir(parents=True)
    f = agent_dir / "instruction.txt"
    f.write_text("No date here.")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1


def test_i014_no_agents_directory(tmp_path, context):
    """No agents/ dir, global missing date → flag global."""
    from cxas_scrapi.utils.lint_rules.instructions import MissingCurrentDate  # noqa: PLC0415,I001

    rule = MissingCurrentDate()
    f = tmp_path / "global_instruction.txt"
    f.write_text("No date here.")

    results = rule.check(f, f.read_text(), context)
    # No agents dir means _all_agent_instructions_have_date returns True
    # (vacuously), so global is not flagged
    assert len(results) == 0


# ── Callback Rules ───────────────────────────────────────────────────────


def test_c001_wrong_fn_name(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import WrongFunctionName  # noqa: PLC0415,I001

    rule = WrongFunctionName()
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "greet_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text("def wrong_name(ctx, req): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "before_model_callback" in results[0].message


def test_c001_correct_fn_name(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import WrongFunctionName  # noqa: PLC0415,I001

    rule = WrongFunctionName()
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "greet_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text("def before_model_callback(ctx, req): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_c002_wrong_arg_count(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import WrongArgCount  # noqa: PLC0415,I001

    rule = WrongArgCount()
    cb_dir = tmp_path / "agents" / "root" / "before_agent_callbacks" / "init_01"
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text("def before_agent_callback(ctx, extra_arg): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "Expected 1 args" in results[0].message


def test_c001_no_function(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import WrongFunctionName  # noqa: PLC0415,I001

    rule = WrongFunctionName()
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "greet_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text("# empty callback\nx = 1")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "No function definition" in results[0].message


def test_c001_unknown_cb_type(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import WrongFunctionName  # noqa: PLC0415,I001

    rule = WrongFunctionName()
    cb_dir = tmp_path / "agents" / "root" / "unknown_callbacks" / "greet_01"
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text("def my_func(): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_c002_correct_arg_count(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import WrongArgCount  # noqa: PLC0415,I001

    rule = WrongArgCount()
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "greet_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text(
        "def before_model_callback(callback_context, llm_request): pass"
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_c003_camelcase_detected(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import CamelCaseFunction  # noqa: PLC0415,I001

    rule = CamelCaseFunction()
    f = tmp_path / "python_code.py"
    f.write_text("def myFunction(x): pass\ndef anotherFunc(y): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 2  # noqa: PLR2004
    assert any("myFunction" in r.message for r in results)


def test_c003_snake_case_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import CamelCaseFunction  # noqa: PLC0415,I001

    rule = CamelCaseFunction()
    f = tmp_path / "python_code.py"
    f.write_text("def my_function(x): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_c004_returns_dict(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import ReturnsDictNotLlmResponse  # noqa: PLC0415,I001

    rule = ReturnsDictNotLlmResponse()
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "greet_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text("def cb(ctx, req):\n    return {'text': 'hi'}")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "LlmResponse" in results[0].message


def test_c004_non_model_callback_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import ReturnsDictNotLlmResponse  # noqa: PLC0415,I001

    rule = ReturnsDictNotLlmResponse()
    cb_dir = tmp_path / "agents" / "root" / "before_agent_callbacks" / "init_01"
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text("def cb(ctx):\n    return {'key': 'val'}")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_c005_hardcoded_phrases(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import HardcodedPhraseList  # noqa: PLC0415,I001

    rule = HardcodedPhraseList()
    f = tmp_path / "python_code.py"
    f.write_text(
        '# detect escalation\nif word in ["escalate", "manager", "supervisor"]:'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "Hardcoded phrase list" in results[0].message


def test_c005_no_detection_keywords(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import HardcodedPhraseList  # noqa: PLC0415,I001

    rule = HardcodedPhraseList()
    f = tmp_path / "python_code.py"
    f.write_text('configs = ["a", "b", "c"]\nif x in [1, 2, 3]:')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_c006_bare_except(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import BareExcept  # noqa: PLC0415,I001

    rule = BareExcept()
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "greet_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text("try:\n    x = 1\nexcept:\n    pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "Bare" in results[0].message


def test_c007_unknown_tool(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import ToolNamingConvention  # noqa: PLC0415,I001

    rule = ToolNamingConvention()
    f = tmp_path / "python_code.py"
    f.write_text("result = tools.unknown_tool(arg1)")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "unknown_tool" in results[0].message


def test_c007_known_tool_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import ToolNamingConvention  # noqa: PLC0415,I001

    rule = ToolNamingConvention()
    f = tmp_path / "python_code.py"
    f.write_text("result = tools.get_balance(account_id)")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_c008_missing_typing_import(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import MissingTypingImport  # noqa: PLC0415,I001

    rule = MissingTypingImport()
    f = tmp_path / "callback.py"
    f.write_text("def cb(ctx) -> Optional[str]:\n    return None")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "Optional" in results[0].message


def test_c008_has_typing_import_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import MissingTypingImport  # noqa: PLC0415,I001

    rule = MissingTypingImport()
    f = tmp_path / "callback.py"
    f.write_text(
        "from typing import Optional\n"
        "def cb(ctx) -> Optional[str]:\n"
        "    return None"
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_c008_non_py_skipped(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import MissingTypingImport  # noqa: PLC0415,I001

    rule = MissingTypingImport()
    f = tmp_path / "callback.txt"
    f.write_text("-> Optional[str]")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_c009_wrong_type_annotation(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import WrongCallbackSignature  # noqa: PLC0415,I001

    rule = WrongCallbackSignature()
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "greet_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text(
        "def before_model_callback(callback_context, llm_request):\n    pass"
    )

    results = rule.check(f, f.read_text(), context)
    # Missing type annotations on params + missing return type
    assert len(results) >= 1


def test_c009_correct_signature(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import WrongCallbackSignature  # noqa: PLC0415,I001

    rule = WrongCallbackSignature()
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "greet_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text(
        "def before_model_callback(callback_context: CallbackContext, "
        "llm_request: LlmRequest) -> Optional[LlmResponse]:\n    pass"
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_c009_before_tool_dict_str_any_no_false_positive(tmp_path, context):
    """A correctly typed before_tool_callback must not be flagged.

    Regression test for issue #56: the comma inside `dict[str, Any]` used
    to break the parameter splitter, producing a bogus
    "Parameter 'input' has type 'dict[str'" error.
    """
    from cxas_scrapi.utils.lint_rules.callbacks import WrongCallbackSignature  # noqa: PLC0415,I001

    rule = WrongCallbackSignature()
    cb_dir = (
        tmp_path
        / "agents"
        / "root"
        / "before_tool_callbacks"
        / "before_tool_callbacks_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text(
        "def before_tool_callback(\n"
        "    tool: Tool,\n"
        "    input: dict[str, Any],\n"
        "    callback_context: CallbackContext,\n"
        ") -> Optional[dict[str, Any]]:\n"
        "    return None\n"
    )

    results = rule.check(f, f.read_text(), context)
    assert results == [], [r.message for r in results]


def test_c009_after_tool_dict_str_any_no_false_positive(tmp_path, context):
    """A correctly typed after_tool_callback must not be flagged.

    Covers the `tool_response: dict[str, Any]` parameter from issue #56.
    """
    from cxas_scrapi.utils.lint_rules.callbacks import WrongCallbackSignature  # noqa: PLC0415,I001

    rule = WrongCallbackSignature()
    cb_dir = (
        tmp_path
        / "agents"
        / "root"
        / "after_tool_callbacks"
        / "after_tool_callbacks_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text(
        "def after_tool_callback(\n"
        "    tool: Tool,\n"
        "    input: dict[str, Any],\n"
        "    callback_context: CallbackContext,\n"
        "    tool_response: dict[str, Any],\n"
        ") -> Optional[dict[str, Any]]:\n"
        "    return None\n"
    )

    results = rule.check(f, f.read_text(), context)
    assert results == [], [r.message for r in results]


def test_c009_dict_str_any_no_space_no_false_positive(tmp_path, context):
    """`dict[str,Any]` (no space) is semantically equal to `dict[str, Any]`.

    Regression test for the whitespace-sensitive comparison from issue #56.
    """
    from cxas_scrapi.utils.lint_rules.callbacks import WrongCallbackSignature  # noqa: PLC0415,I001

    rule = WrongCallbackSignature()
    cb_dir = (
        tmp_path
        / "agents"
        / "root"
        / "before_tool_callbacks"
        / "before_tool_callbacks_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text(
        "def before_tool_callback("
        "tool: Tool, "
        "input: dict[str,Any], "
        "callback_context: CallbackContext"
        ") -> Optional[dict[str,Any]]:\n"
        "    return None\n"
    )

    results = rule.check(f, f.read_text(), context)
    assert results == [], [r.message for r in results]


def test_c009_genuinely_wrong_dict_type_still_caught(tmp_path, context):
    """Ensure the fix does not silence real type mismatches."""
    from cxas_scrapi.utils.lint_rules.callbacks import WrongCallbackSignature  # noqa: PLC0415,I001

    rule = WrongCallbackSignature()
    cb_dir = (
        tmp_path
        / "agents"
        / "root"
        / "before_tool_callbacks"
        / "before_tool_callbacks_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text(
        "def before_tool_callback("
        "tool: Tool, "
        "input: dict[int, Any], "
        "callback_context: CallbackContext"
        ") -> Optional[dict[str, Any]]:\n"
        "    return None\n"
    )

    results = rule.check(f, f.read_text(), context)
    messages = [r.message for r in results]
    assert any(
        "input" in m and "dict[int, Any]" in m and "dict[str, Any]" in m
        for m in messages
    ), messages


def test_c010_invalid_syntax(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import InvalidPythonSyntax  # noqa: PLC0415,I001

    rule = InvalidPythonSyntax()
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "greet_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text("def broken(:\n    pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "syntax" in results[0].message.lower()


def test_c010_valid_syntax(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.callbacks import InvalidPythonSyntax  # noqa: PLC0415,I001

    rule = InvalidPythonSyntax()
    cb_dir = (
        tmp_path / "agents" / "root" / "before_model_callbacks" / "greet_01"
    )
    cb_dir.mkdir(parents=True)
    f = cb_dir / "python_code.py"
    f.write_text("def before_model_callback(ctx, req):\n    return None")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


# ── Tool Rules ───────────────────────────────────────────────────────────


def test_t001_missing_agent_action(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import MissingAgentAction  # noqa: PLC0415,I001

    rule = MissingAgentAction()
    f = tmp_path / "python_code.py"
    f.write_text("def get_balance(account_id): return {'balance': 100}")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "agent_action" in results[0].message


def test_t001_has_agent_action(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import MissingAgentAction  # noqa: PLC0415,I001

    rule = MissingAgentAction()
    f = tmp_path / "python_code.py"
    f.write_text(
        'def get_balance(account_id): return {"agent_action": "error"}'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t002_missing_docstring(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import MissingDocstring  # noqa: PLC0415,I001

    rule = MissingDocstring()
    f = tmp_path / "python_code.py"
    f.write_text("def get_balance(account_id): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1


def test_t002_has_docstring(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import MissingDocstring  # noqa: PLC0415,I001

    rule = MissingDocstring()
    f = tmp_path / "python_code.py"
    f.write_text(
        'def get_balance(account_id):\n    """Get account balance."""\n    pass'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t003_missing_type_hints(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import MissingTypeHints  # noqa: PLC0415,I001

    rule = MissingTypeHints()
    f = tmp_path / "python_code.py"
    f.write_text("def get_balance(account_id, amount): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "type hints" in results[0].message


def test_t003_has_type_hints(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import MissingTypeHints  # noqa: PLC0415,I001

    rule = MissingTypeHints()
    f = tmp_path / "python_code.py"
    f.write_text("def get_balance(account_id: str) -> dict: pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t004_fn_name_mismatch(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import FunctionNameMismatch  # noqa: PLC0415,I001

    rule = FunctionNameMismatch()
    tool_dir = tmp_path / "get_balance" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text("def wrong_name(account_id): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "get_balance" in results[0].message


def test_t004_fn_name_matches(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import FunctionNameMismatch  # noqa: PLC0415,I001

    rule = FunctionNameMismatch()
    tool_dir = tmp_path / "get_balance" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text("def get_balance(account_id): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t004_no_function(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import FunctionNameMismatch  # noqa: PLC0415,I001

    rule = FunctionNameMismatch()
    tool_dir = tmp_path / "get_balance" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text("# no function here\nx = 1")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "No function definition" in results[0].message


def test_t005_high_cardinality(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import HighCardinalityArgs  # noqa: PLC0415,I001

    rule = HighCardinalityArgs()
    f = tmp_path / "python_code.py"
    f.write_text("def locate(latitude, longitude): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) >= 1
    assert any("coordinates" in r.message for r in results)


def test_t005_normal_args(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import HighCardinalityArgs  # noqa: PLC0415,I001

    rule = HighCardinalityArgs()
    f = tmp_path / "python_code.py"
    f.write_text("def get_balance(account_id): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t006_raw_response(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import ExcessiveReturnData  # noqa: PLC0415,I001

    rule = ExcessiveReturnData()
    f = tmp_path / "python_code.py"
    f.write_text("def tool():\n    return response.json()")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "raw API response" in results[0].message


def test_t006_json_loads(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import ExcessiveReturnData  # noqa: PLC0415,I001

    rule = ExcessiveReturnData()
    f = tmp_path / "python_code.py"
    f.write_text("def tool():\n    return json.loads(data)")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "parsed JSON" in results[0].message


def test_t006_filtered_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import ExcessiveReturnData  # noqa: PLC0415,I001

    rule = ExcessiveReturnData()
    f = tmp_path / "python_code.py"
    f.write_text("def tool():\n    return {'balance': data['balance']}")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t007_not_snake_case(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import ToolNameNotSnakeCase  # noqa: PLC0415,I001

    rule = ToolNameNotSnakeCase()
    tool_dir = tmp_path / "Get Balance" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text("def get_balance(): pass")
    json_path = tmp_path / "Get Balance" / "Get Balance.json"
    json_path.write_text(
        '{"name": "Get Balance", "displayName": "Get Balance"}'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 2  # noqa: PLR2004


def test_t007_snake_case_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import ToolNameNotSnakeCase  # noqa: PLC0415,I001

    rule = ToolNameNotSnakeCase()
    tool_dir = tmp_path / "get_balance" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text("def get_balance(): pass")
    json_path = tmp_path / "get_balance" / "get_balance.json"
    json_path.write_text(
        '{"name": "get_balance", "displayName": "get_balance"}'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t008_unreferenced(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import ToolDisplayNameUnreferenced  # noqa: PLC0415,I001

    rule = ToolDisplayNameUnreferenced()
    # Build app structure: tools/my_tool + agents/root_agent
    (tmp_path / "agents" / "root_agent").mkdir(parents=True)
    (tmp_path / "agents" / "root_agent" / "root_agent.json").write_text(
        '{"displayName": "root_agent", "tools": ["other_tool"]}'
    )
    tool_dir = tmp_path / "tools" / "my_tool" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text("def my_tool(): pass")
    (tmp_path / "tools" / "my_tool" / "my_tool.json").write_text(
        '{"name": "my_tool", "displayName": "my_tool"}'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "my_tool" in results[0].message


def test_t008_referenced_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import ToolDisplayNameUnreferenced  # noqa: PLC0415,I001

    rule = ToolDisplayNameUnreferenced()
    (tmp_path / "agents" / "root_agent").mkdir(parents=True)
    (tmp_path / "agents" / "root_agent" / "root_agent.json").write_text(
        '{"displayName": "root_agent", "tools": ["my_tool"]}'
    )
    tool_dir = tmp_path / "tools" / "my_tool" / "python_function"
    tool_dir.mkdir(parents=True)
    f = tool_dir / "python_code.py"
    f.write_text("def my_tool(): pass")
    (tmp_path / "tools" / "my_tool" / "my_tool.json").write_text(
        '{"name": "my_tool", "displayName": "my_tool"}'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t009_kwargs_detected(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import KwargsInSignature  # noqa: PLC0415,I001

    rule = KwargsInSignature()
    f = tmp_path / "python_code.py"
    f.write_text("def my_tool(**kwargs): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "**kwargs" in results[0].message


def test_t009_no_kwargs(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import KwargsInSignature  # noqa: PLC0415,I001

    rule = KwargsInSignature()
    f = tmp_path / "python_code.py"
    f.write_text("def my_tool(param1: str, param2: int): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t010_invalid_syntax(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import ToolInvalidPythonSyntax  # noqa: PLC0415,I001

    rule = ToolInvalidPythonSyntax()
    f = tmp_path / "python_code.py"
    f.write_text("def broken(:\n    pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "syntax" in results[0].message.lower()


def test_t010_valid_syntax(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import ToolInvalidPythonSyntax  # noqa: PLC0415,I001

    rule = ToolInvalidPythonSyntax()
    f = tmp_path / "python_code.py"
    f.write_text("def my_tool(x: str) -> dict:\n    return {}")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t011_none_default(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import NoneDefaultValue  # noqa: PLC0415,I001

    rule = NoneDefaultValue()
    f = tmp_path / "python_code.py"
    f.write_text("def my_tool(param1: str = None, param2: int = 0): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "param1" in results[0].message
    assert "None" in results[0].message


def test_t011_no_none_default(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import NoneDefaultValue  # noqa: PLC0415,I001

    rule = NoneDefaultValue()
    f = tmp_path / "python_code.py"
    f.write_text("def my_tool(param1: str = '', param2: int = 0): pass")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t008_json_tool_unreferenced(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import ToolDisplayNameUnreferenced  # noqa: PLC0415,I001

    rule = ToolDisplayNameUnreferenced()
    # Create an orphaned json widget tool config directly
    # (no python_function subdir)
    (tmp_path / "tools" / "custom_slider").mkdir(parents=True)
    f = tmp_path / "tools" / "custom_slider" / "custom_slider.json"
    f.write_text('{"name": "custom_slider", "displayName": "custom_slider"}')

    # Create agent that doesn't reference it
    (tmp_path / "agents" / "root_agent").mkdir(parents=True)
    (tmp_path / "agents" / "root_agent" / "root_agent.json").write_text(
        '{"displayName": "root_agent", "tools": ["other_tool"]}'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "custom_slider" in results[0].message


def test_t004_json_tool_skipped(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import FunctionNameMismatch  # noqa: PLC0415,I001

    rule = FunctionNameMismatch()
    # A widget tool json should be skipped without complaining
    # about missing Python functions
    f = tmp_path / "tools" / "custom_slider" / "custom_slider.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('{"displayName": "custom_slider", "widgetTool": {}}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t012_python_function_description(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import MissingToolDescriptionInJSON  # noqa: PLC0415,I001

    rule = MissingToolDescriptionInJSON()

    # Case 1: has pythonFunction description
    f = tmp_path / "tools" / "my_func" / "my_func.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        '{"displayName": "my_func", "pythonFunction": '
        '{"description": "A great tool"}}'
    )
    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0

    # Case 2: missing pythonFunction description
    f.write_text('{"displayName": "my_func", "pythonFunction": {}}')
    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "pythonFunction.description" in results[0].message


def test_t012_widget_tool_description(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import MissingToolDescriptionInJSON  # noqa: PLC0415,I001

    rule = MissingToolDescriptionInJSON()

    # Case 1: has widgetTool description
    f = tmp_path / "tools" / "my_widget" / "my_widget.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        '{"displayName": "my_widget", "widgetTool": '
        '{"description": "A cool slider widget"}}'
    )
    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0

    # Case 2: missing widgetTool description
    f.write_text('{"displayName": "my_widget", "widgetTool": {}}')
    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "widgetTool.description" in results[0].message


def test_t001_json_tool_skipped(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import MissingAgentAction  # noqa: PLC0415,I001

    rule = MissingAgentAction()
    f = tmp_path / "tools" / "custom_slider" / "custom_slider.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('{"displayName": "custom_slider", "widgetTool": {}}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_t010_json_tool_skipped(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.tools import ToolInvalidPythonSyntax  # noqa: PLC0415,I001

    rule = ToolInvalidPythonSyntax()
    f = tmp_path / "tools" / "custom_slider" / "custom_slider.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('{"displayName": "custom_slider", "widgetTool": {}}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


# ── Eval Rules ───────────────────────────────────────────────────────────


def test_e001_invalid_yaml(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import InvalidYaml  # noqa: PLC0415,I001

    rule = InvalidYaml()
    f = tmp_path / "test.yaml"
    f.write_text("invalid: yaml: [bad")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "Invalid YAML" in results[0].message


def test_e001_valid_yaml(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import InvalidYaml  # noqa: PLC0415,I001

    rule = InvalidYaml()
    f = tmp_path / "test.yaml"
    f.write_text("valid: true\nitems:\n  - one\n  - two")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_e002_golden_missing_conversations(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import MissingConversations  # noqa: PLC0415,I001

    rule = MissingConversations()
    goldens_dir = tmp_path / "goldens"
    goldens_dir.mkdir()
    f = goldens_dir / "test.yaml"
    f.write_text("name: test\nturns: []")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "conversations" in results[0].message


def test_e002_golden_has_conversations(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import MissingConversations  # noqa: PLC0415,I001

    rule = MissingConversations()
    goldens_dir = tmp_path / "goldens"
    goldens_dir.mkdir()
    f = goldens_dir / "test.yaml"
    f.write_text("conversations:\n  - conversation: test1")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_e002_non_golden_skipped(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import MissingConversations  # noqa: PLC0415,I001

    rule = MissingConversations()
    f = tmp_path / "test.yaml"
    f.write_text("name: test\nturns: []")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_e005_duplicate_keys(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import DuplicateYamlKeys  # noqa: PLC0415,I001

    rule = DuplicateYamlKeys()
    f = tmp_path / "test.yaml"
    content = "tool_calls:\n  - action: foo\ntool_calls:\n  - action: bar"
    f.write_text(content)

    results = rule.check(f, content, context)
    assert len(results) == 1
    assert "Duplicate" in results[0].message


def test_e006_golden_tool_calls_no_params(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import GoldenWithoutMocks  # noqa: PLC0415,I001

    rule = GoldenWithoutMocks()
    goldens_dir = tmp_path / "goldens"
    goldens_dir.mkdir()
    f = goldens_dir / "test.yaml"
    f.write_text(
        "conversations:\n"
        "  - conversation: test1\n"
        "    turns:\n"
        "      - user: hi\n"
        "        tool_calls:\n"
        "          - action: get_balance\n"
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "common_session_parameters" in results[0].message


def test_e006_golden_with_params_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import GoldenWithoutMocks  # noqa: PLC0415,I001

    rule = GoldenWithoutMocks()
    goldens_dir = tmp_path / "goldens"
    goldens_dir.mkdir()
    f = goldens_dir / "test.yaml"
    f.write_text(
        "common_session_parameters:\n"
        "  account_id: '123'\n"
        "conversations:\n"
        "  - conversation: test1\n"
        "    turns:\n"
        "      - user: hi\n"
        "        tool_calls:\n"
        "          - action: get_balance\n"
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_e007_agent_field_not_string(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import GoldenAgentFieldNotString  # noqa: PLC0415,I001

    rule = GoldenAgentFieldNotString()
    goldens_dir = tmp_path / "goldens"
    goldens_dir.mkdir()
    f = goldens_dir / "test.yaml"
    f.write_text(
        "conversations:\n"
        "  - conversation: test1\n"
        "    turns:\n"
        "      - user: hi\n"
        "        agent:\n"
        "          text: hello\n"
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "dict" in results[0].message


def test_e007_agent_field_string_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import GoldenAgentFieldNotString  # noqa: PLC0415,I001

    rule = GoldenAgentFieldNotString()
    goldens_dir = tmp_path / "goldens"
    goldens_dir.mkdir()
    f = goldens_dir / "test.yaml"
    f.write_text(
        "conversations:\n"
        "  - conversation: test1\n"
        "    turns:\n"
        "      - user: hi\n"
        "        agent: Hello there!\n"
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_e008_missing_agent_field(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import GoldenMissingAgentField  # noqa: PLC0415,I001

    rule = GoldenMissingAgentField()
    goldens_dir = tmp_path / "goldens"
    goldens_dir.mkdir()
    f = goldens_dir / "test.yaml"
    f.write_text(
        "conversations:\n"
        "  - conversation: test1\n"
        "    turns:\n"
        "      - user: hi\n"
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "no 'agent' field" in results[0].message


def test_e009_sim_missing_tags(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import SimMissingTags  # noqa: PLC0415,I001

    rule = SimMissingTags()
    sim_dir = tmp_path / "simulations"
    sim_dir.mkdir()
    f = sim_dir / "test.yaml"
    f.write_text("evals:\n  - name: test_sim\n    prompt: do something\n")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "tags" in results[0].message


def test_e009_sim_with_tags_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import SimMissingTags  # noqa: PLC0415,I001

    rule = SimMissingTags()
    sim_dir = tmp_path / "simulations"
    sim_dir.mkdir()
    f = sim_dir / "test.yaml"
    f.write_text('evals:\n  - name: test_sim\n    tags: ["P0"]\n')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_e010_wrong_key(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import ToolTestWrongKey  # noqa: PLC0415,I001

    rule = ToolTestWrongKey()
    f = tmp_path / "tool_tests.yaml"
    f.write_text("test_cases:\n  - tool: get_balance\n    input: {}")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "test_cases" in results[0].message


def test_e010_old_format(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import ToolTestWrongKey  # noqa: PLC0415,I001

    rule = ToolTestWrongKey()
    f = tmp_path / "tool_tests.yaml"
    f.write_text("tool_name: get_balance\ntest_cases:\n  - input: {}")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 2  # noqa: PLR2004


def test_e010_correct_key(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import ToolTestWrongKey  # noqa: PLC0415,I001

    rule = ToolTestWrongKey()
    f = tmp_path / "tool_tests.yaml"
    f.write_text("tests:\n  - tool: get_balance\n    input: {}")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_e011_invalid_match_type(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import InvalidMatchType  # noqa: PLC0415,I001

    rule = InvalidMatchType()
    goldens_dir = tmp_path / "goldens"
    goldens_dir.mkdir()
    f = goldens_dir / "test.yaml"
    f.write_text(
        "conversations:\n"
        "  - conversation: test1\n"
        "    turns:\n"
        "      - user: hi\n"
        "        tool_calls:\n"
        "          - action: get_balance\n"
        "            args:\n"
        "              amount:\n"
        "                $matchType: regex\n"
        '                value: ".*"\n'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "regex" in results[0].message
    assert "regexp" in results[0].fix_suggestion


def test_e011_valid_match_type(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.evals import InvalidMatchType  # noqa: PLC0415,I001

    rule = InvalidMatchType()
    goldens_dir = tmp_path / "goldens"
    goldens_dir.mkdir()
    f = goldens_dir / "test.yaml"
    f.write_text(
        "conversations:\n"
        "  - conversation: test1\n"
        "    turns:\n"
        "      - user: hi\n"
        "        tool_calls:\n"
        "          - action: get_balance\n"
        "            args:\n"
        "              amount:\n"
        "                $matchType: semantic\n"
        '                value: "any amount"\n'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


# ── Config Rules ─────────────────────────────────────────────────────────


def test_a001_invalid_json(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.config import InvalidJson  # noqa: PLC0415,I001

    rule = InvalidJson()
    f = tmp_path / "app.json"
    f.write_text("{invalid json")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "Invalid JSON" in results[0].message


def test_a001_valid_json(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.config import InvalidJson  # noqa: PLC0415,I001

    rule = InvalidJson()
    f = tmp_path / "app.json"
    f.write_text('{"name": "test"}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_a002_missing_required_fields(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.config import MissingRequiredFields  # noqa: PLC0415,I001

    rule = MissingRequiredFields()
    f = tmp_path / "app.json"
    f.write_text('{"description": "test"}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 2  # noqa: PLR2004
    fields = {r.message for r in results}
    assert any("name" in m for m in fields)
    assert any("displayName" in m for m in fields)


# ── Schema Rules ─────────────────────────────────────────────────────────


def test_v001_app_valid(tmp_path, context):
    from cxas_scrapi.utils.linter import build_registry  # noqa: PLC0415,I001

    registry = build_registry()
    rule = registry.get("V001")

    app_dir = tmp_path / "myapp"
    app_dir.mkdir()
    (app_dir / "app.yaml").write_text("displayName: MyApp")

    with patch("cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"):
        results = rule.check(app_dir, "", context)
        assert len(results) == 0


def test_v001_app_missing_config(tmp_path, context):
    from cxas_scrapi.utils.linter import build_registry  # noqa: PLC0415,I001

    registry = build_registry()
    rule = registry.get("V001")

    app_dir = tmp_path / "empty_app"
    app_dir.mkdir()

    results = rule.check(app_dir, "", context)
    assert len(results) == 1
    assert "Missing config" in results[0].message


def test_v002_agent_valid(tmp_path, context):
    from cxas_scrapi.utils.linter import build_registry  # noqa: PLC0415,I001

    registry = build_registry()
    rule = registry.get("V002")

    agent_dir = tmp_path / "agents" / "MyAgent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "MyAgent.yaml").write_text("displayName: MyAgent")
    (agent_dir / "instruction.txt").write_text("Be helpful.")

    with patch("cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"):
        results = rule.check(agent_dir, "", context)
        assert len(results) == 0


def test_v002_agent_missing_config(tmp_path, context):
    from cxas_scrapi.utils.linter import build_registry  # noqa: PLC0415,I001

    registry = build_registry()
    rule = registry.get("V002")

    agent_dir = tmp_path / "agents" / "MyAgent"
    agent_dir.mkdir(parents=True)

    results = rule.check(agent_dir, "", context)
    assert len(results) == 1
    assert "Missing config" in results[0].message


def test_v003_tool_valid(tmp_path, context):
    from cxas_scrapi.utils.linter import build_registry  # noqa: PLC0415,I001

    registry = build_registry()
    rule = registry.get("V003")

    tool_dir = tmp_path / "tools" / "MyTool"
    tool_dir.mkdir(parents=True)
    (tool_dir / "MyTool.yaml").write_text("displayName: MyTool")

    with patch("cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"):
        results = rule.check(tool_dir, "", context)
        assert len(results) == 0


def test_v005_guardrail_valid(tmp_path, context):
    from cxas_scrapi.utils.linter import build_registry  # noqa: PLC0415,I001

    registry = build_registry()
    rule = registry.get("V005")

    guardrail_dir = tmp_path / "guardrails" / "MyGuardrail"
    guardrail_dir.mkdir(parents=True)
    (guardrail_dir / "MyGuardrail.yaml").write_text("displayName: MyGuardrail")

    with patch("cxas_scrapi.utils.lint_rules.schema.json_format.ParseDict"):
        results = rule.check(guardrail_dir, "", context)
        assert len(results) == 0


def test_v006_evaluation_invalid_field(tmp_path, context):
    from cxas_scrapi.utils.linter import build_registry  # noqa: PLC0415,I001

    registry = build_registry()
    rule = registry.get("V006")

    eval_dir = tmp_path / "evaluations" / "MyEval"
    eval_dir.mkdir(parents=True)
    (eval_dir / "MyEval.yaml").write_text(
        "displayName: MyEval\nnon_existent_field: value"
    )

    results = rule.check(eval_dir, "", context)
    assert len(results) == 1
    msg = results[0].message
    assert "Proto schema" in msg or "validation failed" in msg


def test_schema_missing_referenced_file(tmp_path, context):
    from cxas_scrapi.utils.linter import build_registry  # noqa: PLC0415,I001

    registry = build_registry()
    rule = registry.get("V002")

    agent_dir = tmp_path / "agents" / "MyAgent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "MyAgent.yaml").write_text(
        "displayName: MyAgent\ninstruction: agents/MyAgent/nonexistent.txt"
    )

    results = rule.check(agent_dir, "", context)
    assert len(results) == 1
    msg = results[0].message
    assert "Missing referenced file" in msg or "not found" in msg


def test_schema_missing_required_field(tmp_path, context):
    from cxas_scrapi.utils.linter import build_registry  # noqa: PLC0415,I001

    registry = build_registry()
    rule = registry.get("V001")

    app_dir = tmp_path / "myapp"
    app_dir.mkdir()
    (app_dir / "app.yaml").write_text("description: no display name")

    with patch(
        "cxas_scrapi.utils.lint_rules.schema._get_required_fields",
        return_value=["display_name"],
    ):
        results = rule.check(app_dir, "", context)
        assert len(results) == 1
        msg = results[0].message
        assert "Missing required fields" in msg or "display_name" in msg


# ── Structure Rules ──────────────────────────────────────────────────────


def test_s002_tool_ref_not_in_agent(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import AgentToolReferences  # noqa: PLC0415,I001

    rule = AgentToolReferences()
    agent_dir = tmp_path / "root_agent"
    agent_dir.mkdir()
    (agent_dir / "root_agent.json").write_text(
        '{"displayName": "root_agent", "tools": ["get_balance"]}'
    )
    f = agent_dir / "instruction.txt"
    f.write_text("Use {@TOOL: unknown_tool} to do something.")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "unknown_tool" in results[0].message


def test_s002_tool_ref_in_agent_ok(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import AgentToolReferences  # noqa: PLC0415,I001

    rule = AgentToolReferences()
    agent_dir = tmp_path / "root_agent"
    agent_dir.mkdir()
    (agent_dir / "root_agent.json").write_text(
        '{"displayName": "root_agent", "tools": ["get_balance"]}'
    )
    f = agent_dir / "instruction.txt"
    f.write_text("Use {@TOOL: get_balance} to check.")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_s002_not_instruction_skipped(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import AgentToolReferences  # noqa: PLC0415,I001

    rule = AgentToolReferences()
    f = tmp_path / "config.json"
    f.write_text("{}")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_s003_callback_file_missing(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import CallbackFileReferences  # noqa: PLC0415,I001

    rule = CallbackFileReferences()
    f = tmp_path / "root_agent.json"
    f.write_text(
        '{"beforeModelCallbacks": [{"pythonCode": "callbacks/greet.py"}]}'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "greet.py" in results[0].message


def test_s003_not_json_skipped(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import CallbackFileReferences  # noqa: PLC0415,I001

    rule = CallbackFileReferences()
    f = tmp_path / "instruction.txt"
    f.write_text("just text")

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_s004_child_agent_missing(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import ChildAgentReferences  # noqa: PLC0415,I001

    rule = ChildAgentReferences()
    f = tmp_path / "root_agent.json"
    f.write_text('{"childAgents": ["nonexistent_agent"]}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "nonexistent_agent" in results[0].message


def test_s004_child_agent_exists(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import ChildAgentReferences  # noqa: PLC0415,I001

    rule = ChildAgentReferences()
    f = tmp_path / "root_agent.json"
    f.write_text('{"childAgents": ["billing_agent"]}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_s004_no_children(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import ChildAgentReferences  # noqa: PLC0415,I001

    rule = ChildAgentReferences()
    f = tmp_path / "root_agent.json"
    f.write_text('{"displayName": "root"}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_s004_child_agent_by_display_name(tmp_path, context):
    """Reference by display name (with space) should be accepted (S004)."""
    from cxas_scrapi.utils.lint_rules.structure import ChildAgentReferences  # noqa: PLC0415,I001

    rule = ChildAgentReferences()
    f = tmp_path / "root_agent.json"
    # 'billing agent' is the display name for directory 'billing_agent'
    f.write_text('{"childAgents": ["billing agent"]}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


# --- Rules A006, S005, S006 Tests ---


def test_a006_root_agent_snake_case(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.config import AppRootAgentValidation  # noqa: PLC0415,I001

    rule = AppRootAgentValidation()
    f = tmp_path / "app.json"
    f.write_text('{"root_agent": "my_root"}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "Found 'root_agent' in app.json" in results[0].message


def test_a006_root_agent_missing(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.config import AppRootAgentValidation  # noqa: PLC0415,I001

    rule = AppRootAgentValidation()
    f = tmp_path / "app.json"
    f.write_text('{"displayName": "Hello App"}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "Missing required field 'rootAgent'" in results[0].message


def test_a006_root_agent_not_string(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.config import AppRootAgentValidation  # noqa: PLC0415,I001

    rule = AppRootAgentValidation()
    f = tmp_path / "app.json"
    f.write_text('{"rootAgent": 123}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "must be a string" in results[0].message


def test_a006_root_agent_directory_missing(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.config import AppRootAgentValidation  # noqa: PLC0415,I001

    rule = AppRootAgentValidation()
    f = tmp_path / "app.json"
    f.write_text('{"rootAgent": "non_existent"}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "does not exist under the agents/ directory" in results[0].message


def test_a006_root_agent_valid(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.config import AppRootAgentValidation  # noqa: PLC0415,I001

    rule = AppRootAgentValidation()
    f = tmp_path / "app.json"
    f.write_text('{"rootAgent": "billing_agent"}')

    agent_dir = tmp_path / "agents" / "billing_agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "billing_agent.json").write_text("{}")
    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_s005_agent_paths_valid(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import StrictAgentPathLayout  # noqa: PLC0415,I001

    rule = StrictAgentPathLayout()
    f = tmp_path / "root_agent.json"
    f.write_text(
        '{"instruction": "agents/root_agent/instruction.txt", '
        '"beforeModelCallbacks": [{'
        '"pythonCode": "agents/root_agent/callbacks/my_cb.py"'
        "}]}"
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 0


def test_s005_agent_paths_invalid_instruction(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import StrictAgentPathLayout  # noqa: PLC0415,I001

    rule = StrictAgentPathLayout()
    f = tmp_path / "root_agent.json"
    f.write_text('{"instruction": "instruction.txt"}')

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "Agent instruction path" in results[0].message
    assert "agents/root_agent/" in results[0].message


def test_s005_agent_paths_invalid_callback(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import StrictAgentPathLayout  # noqa: PLC0415,I001

    rule = StrictAgentPathLayout()
    f = tmp_path / "root_agent.json"
    f.write_text(
        '{"beforeModelCallbacks": [{"pythonCode": "callbacks/my_cb.py"}]}'
    )

    results = rule.check(f, f.read_text(), context)
    assert len(results) == 1
    assert "Agent callback pythonCode path" in results[0].message
    assert "agents/root_agent/" in results[0].message


def test_s006_tool_paths_valid(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import StrictToolPathLayout  # noqa: PLC0415,I001

    rule = StrictToolPathLayout()
    tool_dir = tmp_path / "tools" / "get_balance"
    tool_dir.mkdir(parents=True, exist_ok=True)
    tool_json = tool_dir / "get_balance.json"
    tool_json.write_text(
        '{"pythonFunction": {'
        '"pythonCode": "tools/get_balance/python_function/python_code.py"'
        "}}"
    )

    results = rule.check(tool_dir, "", context)
    assert len(results) == 0


def test_s006_tool_paths_invalid(tmp_path, context):
    from cxas_scrapi.utils.lint_rules.structure import StrictToolPathLayout  # noqa: PLC0415,I001

    rule = StrictToolPathLayout()
    tool_dir = tmp_path / "tools" / "get_balance"
    tool_dir.mkdir(parents=True, exist_ok=True)
    tool_json = tool_dir / "get_balance.json"
    tool_json.write_text(
        '{"pythonFunction": {"pythonCode": "python_function/python_code.py"}}'
    )

    results = rule.check(tool_dir, "", context)
    assert len(results) == 1
    assert "Tool pythonCode path" in results[0].message
