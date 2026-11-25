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

"""Offline pytest suite for cxas-self-steering-agent scripts and sample."""

from __future__ import annotations

import ast
import base64
import importlib.util
import json
import pathlib
import shutil
import stat
import sys
import types
from typing import Any, Optional

import pytest

from cxas_scrapi.utils import callback_libs as cl
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintReport,
    build_context,
    build_registry,
    run_rules,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SKILL_DIR = _REPO_ROOT / ".agents" / "skills" / "cxas-self-steering-agent"
_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_SAMPLE_DIR = _SKILL_DIR / "resources" / "sample-agent"

_CORE_TOOLS = (
    "open_playbook",
    "load_task",
    "graceful_end_session",
    "execute_live_agent_handover",
    "set_next_agent_actions",
    "end_session",
)
_CORE_VARIABLES = (
    "current_playbook",
    "current_task",
    "next_agent_actions",
    "played_greeting",
    "pending_handoff_reason",
)


def _load_script(name: str) -> types.ModuleType:
    """Loads a skill helper script with a unique module name."""
    module_name = f"self_steering_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    script_path = _SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _copy_sample(tmp_path: pathlib.Path, name: str = "app") -> pathlib.Path:
    """Copies the bundled starter app into `tmp_path` with write access."""
    app_dir = tmp_path / name
    shutil.copytree(
        _SAMPLE_DIR,
        app_dir,
        ignore=shutil.ignore_patterns("__init__.py", "__pycache__"),
    )
    for item in (app_dir, *app_dir.rglob("*")):
        mode = item.stat().st_mode
        if item.is_dir():
            item.chmod(mode | stat.S_IRWXU)
        else:
            item.chmod(mode | stat.S_IRUSR | stat.S_IWUSR)
    sync_tool_descriptions = _load_script("sync_tool_descriptions")
    sync_tool_descriptions.sync(app_dir, write=True)
    return app_dir


def _run_cxas_lint(app_dir: pathlib.Path) -> LintReport:
    """Runs the cxas_scrapi linter offline against `app_dir`."""
    config = LintConfig.load(app_dir)
    discovery = Discovery(app_dir / config.app_dir, app_dir / config.evals_dir)
    assert discovery.app_root == app_dir
    context = build_context(app_dir, config, discovery)
    registry = build_registry()
    report = LintReport()
    run_rules(registry, config, context, discovery, report)
    return report


def _load_root_callback(app_dir: pathlib.Path) -> Any:
    """Compiles and returns the root agent's before_model_callback."""
    app_cfg = json.loads((app_dir / "app.json").read_text(encoding="utf-8"))
    root_agent = app_cfg["rootAgent"]
    agent_cfg = json.loads(
        (app_dir / "agents" / root_agent / f"{root_agent}.json").read_text(
            encoding="utf-8"
        )
    )
    cb_rel = agent_cfg["beforeModelCallbacks"][0]["pythonCode"]
    cb_path = app_dir / cb_rel
    namespace: dict[str, Any] = {
        name: getattr(cl, name) for name in dir(cl) if not name.startswith("_")
    }
    namespace.update(Optional=Optional, Any=Any, json=json)
    exec(  # noqa: S102
        compile(cb_path.read_text(encoding="utf-8"), str(cb_path), "exec"),
        namespace,
    )
    return namespace["before_model_callback"]


def _exec_tool_with_isolated_subtools(
    app_dir: pathlib.Path,
    tool_name: str,
    session_state: dict[str, Any],
    kwargs: dict[str, Any],
) -> Any:
    """Runs a tool whose nested `tools.<name>(...)` calls get isolated state.

    Mirrors the isolated tool sandbox runtime, where nested sub-tool calls
    receive a separate empty state dictionary rather than a reference to the
    outer tool's `context.state`.
    """

    class _IsolatedToolsProxy:
        def __getattr__(self, subtool_name: str) -> Any:
            def _call(
                args: dict[str, Any] | None = None, **sub_kwargs: Any
            ) -> Any:
                sub_path = (
                    app_dir
                    / "tools"
                    / subtool_name
                    / "python_function"
                    / "python_code.py"
                )
                sub_ns: dict[str, Any] = {
                    "context": types.SimpleNamespace(
                        state={}, session_id="isolated-subtool"
                    ),
                    "tools": self,
                    "__name__": subtool_name,
                }
                exec(  # noqa: S102
                    compile(
                        sub_path.read_text(encoding="utf-8"),
                        str(sub_path),
                        "exec",
                    ),
                    sub_ns,
                )
                value = sub_ns[subtool_name](**(args or {}), **sub_kwargs)
                return types.SimpleNamespace(json=lambda: {"result": value})

            return _call

    tool_path = (
        app_dir / "tools" / tool_name / "python_function" / "python_code.py"
    )
    namespace: dict[str, Any] = {
        "context": types.SimpleNamespace(
            state=session_state, session_id="outer-tool"
        ),
        "tools": _IsolatedToolsProxy(),
        "__name__": tool_name,
    }
    exec(  # noqa: S102
        compile(tool_path.read_text(encoding="utf-8"), str(tool_path), "exec"),
        namespace,
    )
    return namespace[tool_name](**kwargs)


def _read_instruction(
    app_dir: pathlib.Path, agent: str = "Support_Agent"
) -> str:
    return (app_dir / "agents" / agent / "instruction.txt").read_text(
        encoding="utf-8"
    )


def _write_instruction(
    app_dir: pathlib.Path, text: str, agent: str = "Support_Agent"
) -> None:
    (app_dir / "agents" / agent / "instruction.txt").write_text(
        text, encoding="utf-8"
    )


# ── Clean Starter & Linter Parity ────────────────────────────────────────


def test_clean_starter_passes_all_checks_and_cxas_lint() -> None:
    """Bundled sample passes visibility, doc sync, env placeholder, and lint."""
    check_tool_visibility = _load_script("check_tool_visibility")
    sync_tool_descriptions = _load_script("sync_tool_descriptions")

    assert check_tool_visibility.check(_SAMPLE_DIR) == []
    assert sync_tool_descriptions.sync(_SAMPLE_DIR, write=False) == []

    env_cfg = json.loads(
        (_SAMPLE_DIR / "environment.json").read_text(encoding="utf-8")
    )
    assert (
        env_cfg["app"]["loggingSettings"]["bigqueryExportSettings"]["project"]
        == "$env_var"
    )

    report = _run_cxas_lint(_SAMPLE_DIR)
    assert report.errors == [], [str(r) for r in report.errors]
    assert report.warnings == [], [str(r) for r in report.warnings]
    assert report.results == [], [str(r) for r in report.results]


# ── Local Runner Walkthrough & #604 State / Teardown Latch ───────────────


def test_runner_walkthrough_and_end_session_latch(
    tmp_path: pathlib.Path,
) -> None:
    """Walks greeting -> playbook -> task -> handover -> latched teardown."""
    run_locally = _load_script("run_locally")
    app_dir = _copy_sample(tmp_path)
    runner = run_locally.Runner(app_dir)

    # First callback plays the greeting and marks played_greeting=True.
    resp1, _ = runner.run_callback()
    assert resp1 is not None
    assert runner.state["played_greeting"] is True
    assert any("says:" in line for line in run_locally._describe(resp1))

    # Second callback exposes only start-state tools to the model.
    resp2, req2 = runner.run_callback()
    assert resp2 is None
    excluded_start = set(req2.config.excluded_tools or [])
    assert {"load_task", "end_session", "set_next_agent_actions"} <= (
        excluded_start
    )
    assert not (
        {
            "open_playbook",
            "graceful_end_session",
            "execute_live_agent_handover",
        }
        & excluded_start
    )

    # Open the live_agent playbook and verify load_task becomes visible.
    res_pb = runner.call_tool(
        "open_playbook",
        {
            "playbook_name": "live_agent",
            "rationale": "Customer asked for a representative.",
        },
    )
    assert res_pb["status"] == "ok"
    assert runner.state["current_playbook"] == "live_agent"
    assert runner.state["current_task"] == ""

    resp_pb, req_pb = runner.run_callback()
    assert resp_pb is None
    assert "load_task" not in set(req_pb.config.excluded_tools or [])

    # Load live_agent_escalation task and execute live agent handover.
    res_task = runner.call_tool(
        "load_task",
        {
            "task_name": "live_agent_escalation",
            "rationale": "Escalate the customer to live support.",
        },
    )
    assert res_task["status"] == "ok"
    assert runner.state["current_task"] == "live_agent_escalation"

    res_ho = runner.call_tool(
        "execute_live_agent_handover",
        {"reason": "customer_requested_agent"},
    )
    assert res_ho["status"] == "transferring"
    assert res_ho["reason"] == "customer_requested_agent"
    assert runner.state["next_agent_actions"]["end_session"] == {
        "reason": "customer_requested_agent",
        "escalated": True,
    }

    # First callback pass after handover emits customized_response + end_session
    # and latches {"end_session": ...} in state (#604).
    resp3, _ = runner.run_callback()
    assert resp3 is not None
    assert resp3.content is not None
    parts3 = resp3.content.parts or []
    assert any(p.inline_data is not None for p in parts3)
    assert any(p.has_function_call("end_session") for p in parts3)
    described3 = "\n".join(run_locally._describe(resp3))
    assert "end_session" in described3
    assert "customer_requested_agent" in described3
    assert runner.state["next_agent_actions"] == {
        "end_session": {
            "reason": "customer_requested_agent",
            "escalated": True,
        }
    }

    # Second callback pass re-emits end_session without waking the model or
    # repeating customized_response (#604).
    resp4, _ = runner.run_callback()
    assert resp4 is not None
    assert resp4.content is not None
    parts4 = resp4.content.parts or []
    assert not any(p.inline_data is not None for p in parts4)
    end_calls = [
        p.function_call
        for p in parts4
        if p.has_function_call("end_session") and p.function_call is not None
    ]
    assert len(end_calls) == 1
    assert end_calls[0].args == {
        "reason": "customer_requested_agent",
        "session_escalated": True,
    }
    assert runner.state["next_agent_actions"] == {
        "end_session": {
            "reason": "customer_requested_agent",
            "escalated": True,
        }
    }


@pytest.mark.parametrize(
    ("tool_name", "kwargs", "expected_escalated"),
    [
        (
            "graceful_end_session",
            {"goodbye_message": "Thanks for contacting us!"},
            False,
        ),
        (
            "execute_live_agent_handover",
            {
                "reason": "customer_requested_agent",
                "pre_transfer_message": "Connecting you with a specialist.",
            },
            True,
        ),
    ],
)
def test_isolated_subtool_sandbox_preserves_next_agent_actions(
    tool_name: str,
    kwargs: dict[str, Any],
    expected_escalated: bool,
) -> None:
    """Tools write next_agent_actions directly so isolated subtools survive."""
    run_locally = _load_script("run_locally")
    app_cfg = json.loads((_SAMPLE_DIR / "app.json").read_text(encoding="utf-8"))
    state = {**run_locally._defaults(app_cfg), "played_greeting": True}

    _exec_tool_with_isolated_subtools(_SAMPLE_DIR, tool_name, state, kwargs)

    queued = state.get("next_agent_actions")
    assert isinstance(queued, dict), (
        f"{tool_name} dropped next_agent_actions under isolated sub-tool state"
    )
    assert "customized_response" in queued
    assert queued["customized_response"]["disable_barge_in"] is True
    assert queued.get("end_session") is not None
    assert queued["end_session"]["escalated"] is expected_escalated


@pytest.mark.parametrize(
    ("tool_name", "kwargs", "expected_escalated"),
    [
        (
            "graceful_end_session",
            {"goodbye_message": "Thanks for calling!"},
            False,
        ),
        (
            "execute_live_agent_handover",
            {
                "reason": "customer_requested_agent",
                "pre_transfer_message": "Transferring you now.",
            },
            True,
        ),
    ],
)
def test_callback_latches_end_session_without_repeating_response(
    tool_name: str,
    kwargs: dict[str, Any],
    expected_escalated: bool,
) -> None:
    """Streaming mid-goodbye turns re-emit end_session without waking LLM."""
    run_locally = _load_script("run_locally")
    app_cfg = json.loads((_SAMPLE_DIR / "app.json").read_text(encoding="utf-8"))
    state = {**run_locally._defaults(app_cfg), "played_greeting": True}
    runner = run_locally.Runner(_SAMPLE_DIR, state)

    runner.call_tool(tool_name, kwargs)
    first_response, _ = runner.run_callback()
    assert first_response is not None
    assert first_response.content is not None
    first_parts = first_response.content.parts or []
    assert any(p.inline_data is not None for p in first_parts)
    assert any(p.has_function_call("end_session") for p in first_parts)

    second_response, _ = runner.run_callback()
    assert second_response is not None
    assert second_response.content is not None
    second_parts = second_response.content.parts or []
    assert not any(p.inline_data is not None for p in second_parts)
    end_calls = [
        p.function_call
        for p in second_parts
        if p.has_function_call("end_session") and p.function_call is not None
    ]
    assert len(end_calls) == 1
    assert end_calls[0].args is not None
    assert end_calls[0].args["session_escalated"] is expected_escalated


def test_callback_pops_non_terminal_customized_response_cleanly() -> None:
    """Non-ending customized_response plays once and wakes LLM next turn."""
    run_locally = _load_script("run_locally")
    app_cfg = json.loads((_SAMPLE_DIR / "app.json").read_text(encoding="utf-8"))
    state = {**run_locally._defaults(app_cfg), "played_greeting": True}
    runner = run_locally.Runner(_SAMPLE_DIR, state)

    runner.call_tool(
        "set_next_agent_actions",
        {
            "customized_response": {
                "content": "Please hold while I verify your request.",
                "disable_barge_in": True,
            }
        },
    )
    first_response, _ = runner.run_callback()
    assert first_response is not None
    assert first_response.content is not None
    first_parts = first_response.content.parts or []
    assert len(first_parts) == 1
    assert first_parts[0].inline_data is not None
    assert not any(p.has_function_call("end_session") for p in first_parts)
    payload = json.loads(
        base64.b64decode(first_parts[0].inline_data.data or b"")
    )
    assert "verify your request" in payload["content"].lower()
    assert "next_agent_actions" not in runner.state

    second_response, request = runner.run_callback()
    assert second_response is None
    hidden = set(request.config.excluded_tools or [])
    assert "open_playbook" not in hidden


# ── Guardrail childAgents Support (#605) ─────────────────────────────────


def test_guardrail_child_agents_hides_transfer_tools_across_declarations() -> (
    None
):
    """Callback iterates all ToolDeclarations and hides transfer tools (#605)."""
    cb = _load_root_callback(_SAMPLE_DIR)
    base_instruction = (
        "<role>\n"
        "You are a support agent.\n"
        "</role>\n"
        "<anytime_actions>\n"
        "Call {@TOOL: graceful_end_session} when done.\n"
        "</anytime_actions>\n"
        "<playbook_list>\n"
        "Call {@TOOL: open_playbook} to open a playbook.\n"
        "</playbook_list>"
    )
    injected_child_agent_block = (
        "\n\nYou have a list of other agents to transfer to:\n\n"
        "Agent name: Guardrail_Handler_Agent\n"
        "Agent description: Handles safety escalations.\n\n"
        "If another agent is better for answering the question according "
        "to its description, call `transfer_to_agent` or `transferToAgent` "
        "to transfer the question to that agent.\n"
        "**NOTE**: the only available agent for `transfer_to_agent` is "
        "`Guardrail_Handler_Agent`.\n"
    )
    trailing_block = "\n<postscript>\nStay concise.\n</postscript>"
    full_instruction = (
        base_instruction + injected_child_agent_block + trailing_block
    )
    request = cl.LlmRequest(
        contents=[cl.Content(parts=[cl.Part(text="Hi")], role="user")],
        config=cl.GenerateContentConfig(
            systemInstruction=cl.Content(
                parts=[cl.Part(text=full_instruction)]
            ),
            tools=[
                cl.ToolDeclaration(
                    functionDeclarations=[
                        cl.FunctionDeclaration(name="open_playbook"),
                        cl.FunctionDeclaration(name="graceful_end_session"),
                        cl.FunctionDeclaration(name="load_task"),
                        cl.FunctionDeclaration(name="end_session"),
                    ]
                ),
                cl.ToolDeclaration(
                    functionDeclarations=[
                        cl.FunctionDeclaration(name="transfer_to_agent"),
                        cl.FunctionDeclaration(name="transferToAgent"),
                        cl.FunctionDeclaration(name="unmentioned_helper"),
                    ]
                ),
            ],
        ),
    )
    ctx = cl.CallbackContext(state={"played_greeting": True})

    response = cb(ctx, request)
    assert response is None
    assert request.config is not None
    excluded = set(request.config.excluded_tools or [])

    assert "transfer_to_agent" in excluded
    assert "transferToAgent" in excluded
    assert "unmentioned_helper" in excluded
    assert "load_task" in excluded
    assert "end_session" in excluded
    assert "open_playbook" not in excluded
    assert "graceful_end_session" not in excluded

    assert request.config.system_instruction is not None
    assert request.config.system_instruction.parts is not None
    assert request.config.system_instruction.parts[0].text == full_instruction


# ── scaffold_from_sample ─────────────────────────────────────────────────


@pytest.mark.parametrize("project_id", ["$env_var", "custom-gcp-project"])
def test_scaffold_from_sample_creates_clean_agent(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    project_id: str,
) -> None:
    """scaffold_from_sample copies starter, renames agent, and passes checks."""
    scaffold_from_sample = _load_script("scaffold_from_sample")
    check_tool_visibility = _load_script("check_tool_visibility")
    sync_tool_descriptions = _load_script("sync_tool_descriptions")

    target = tmp_path / "billing-agent"
    app_id = "00000000-1111-2222-3333-444444444444"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scaffold_from_sample.py",
            "--target-dir",
            str(target),
            "--app-id",
            app_id,
            "--project-id",
            project_id,
            "--display-name",
            "Billing Agent",
            "--agent-name",
            "Billing_Agent",
        ],
    )
    scaffold_from_sample.main()

    app = json.loads((target / "app.json").read_text(encoding="utf-8"))
    assert app["name"] == app_id
    assert app["displayName"] == "Billing Agent"
    assert app["rootAgent"] == "Billing_Agent"
    declared_vars = [v["name"] for v in app["variableDeclarations"]]
    assert declared_vars == list(_CORE_VARIABLES)

    env_cfg = json.loads(
        (target / "environment.json").read_text(encoding="utf-8")
    )
    assert (
        env_cfg["app"]["loggingSettings"]["bigqueryExportSettings"]["project"]
        == project_id
    )

    assert not (target / "agents" / "Support_Agent").exists()
    agent_json = target / "agents" / "Billing_Agent" / "Billing_Agent.json"
    agent = json.loads(agent_json.read_text(encoding="utf-8"))
    assert agent["name"] == "Billing_Agent"
    assert agent["displayName"] == "Billing_Agent"
    assert agent["instruction"] == "agents/Billing_Agent/instruction.txt"
    assert agent["beforeModelCallbacks"][0]["pythonCode"] == (
        "agents/Billing_Agent/before_model_callbacks/"
        "before_model_callbacks_01/python_code.py"
    )
    assert sorted(agent["tools"]) == sorted(_CORE_TOOLS)

    tool_dirs = sorted(
        p.name for p in (target / "tools").iterdir() if p.is_dir()
    )
    assert tool_dirs == sorted(t for t in _CORE_TOOLS if t != "end_session")

    assert check_tool_visibility.check(target) == []
    assert sync_tool_descriptions.sync(target, write=False) == []
    report = _run_cxas_lint(target)
    assert report.errors == [], [str(r) for r in report.errors]
    assert report.warnings == [], [str(r) for r in report.warnings]

    # Re-running without --overwrite fails; --overwrite succeeds.
    with pytest.raises(SystemExit):
        scaffold_from_sample.main()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "scaffold_from_sample.py",
            "--target-dir",
            str(target),
            "--app-id",
            app_id,
            "--project-id",
            project_id,
            "--agent-name",
            "Billing_Agent",
            "--overwrite",
        ],
    )
    scaffold_from_sample.main()
    assert (target / "agents" / "Billing_Agent" / "instruction.txt").exists()


# ── Instruction & Template Checks (check_tool_visibility) ────────────────


def test_template_syntax_and_antipatterns_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """Catches single braces, not-in, subscripts, comments, and undeclared."""
    check_tool_visibility = _load_script("check_tool_visibility")
    app_dir = _copy_sample(tmp_path)

    app_path = app_dir / "app.json"
    app = json.loads(app_path.read_text(encoding="utf-8"))
    app["variableDeclarations"].append(
        {"name": "items", "schema": {"type": "ARRAY"}}
    )
    app_path.write_text(json.dumps(app, indent=2) + "\n", encoding="utf-8")

    orig = _read_instruction(app_dir)
    _write_instruction(
        app_dir,
        orig
        + "\nActive playbook: {current_playbook}\n"
        + "{% if played_greeting %}hi{% endif %}\n"
        + "{{% if current_playbook not in ['live_agent'] %}}x{{% endif %}}\n"
        + "{{ items[0] }}\n"
        + "<session_snapshot>{{current_playbook}}</session_snapshot>\n"
        + "{{# hello end_session #}}\n"
        + "{# plain comment #}\n"
        + "Unknown: {{ mystery_undeclared_var }}\n",
    )

    problems = check_tool_visibility.check(app_dir)
    assert any("single-brace '{current_playbook}'" in p for p in problems), (
        problems
    )
    assert any("single-brace block" in p for p in problems), problems
    assert any("`not in`" in p for p in problems), problems
    assert any("subscript" in p for p in problems), problems
    assert any("<session_snapshot>" in p for p in problems), problems
    assert any("end_session" in p and "Jinja comment" in p for p in problems), (
        problems
    )
    assert any("Jinja comment is not stripped" in p for p in problems), problems
    assert any(
        "variable 'mystery_undeclared_var' is used in the instruction" in p
        for p in problems
    ), problems


def test_non_bare_output_expressions_flagged(tmp_path: pathlib.Path) -> None:
    """Flags ternary and filter output expressions left literal by CES."""
    check_tool_visibility = _load_script("check_tool_visibility")
    app_dir = _copy_sample(tmp_path)
    orig = _read_instruction(app_dir)
    _write_instruction(
        app_dir,
        orig
        + "\nStatus: {{ 'yes' if played_greeting else 'no' }}\n"
        + "Fallback: {{ current_playbook | default('none') }}\n",
    )
    problems = check_tool_visibility.check(app_dir)
    assert any("'yes' if played_greeting else 'no'" in p for p in problems), (
        problems
    )
    assert any("current_playbook | default('none')" in p for p in problems), (
        problems
    )


def test_prose_state_check_and_negative_control(
    tmp_path: pathlib.Path,
) -> None:
    """Flags prose state checks on declared vars while ignoring pronouns."""
    check_tool_visibility = _load_script("check_tool_visibility")
    app_dir = _copy_sample(tmp_path)
    app_path = app_dir / "app.json"
    app = json.loads(app_path.read_text(encoding="utf-8"))
    app["variableDeclarations"].extend(
        [
            {
                "name": "verified",
                "description": "Whether caller identity is verified.",
                "schema": {"type": "BOOLEAN", "default": False},
            },
            {
                "name": "order",
                "description": "Order details.",
                "schema": {
                    "type": "OBJECT",
                    "properties": {"status": {"type": "STRING"}},
                    "default": {"status": ""},
                },
            },
        ]
    )
    app_path.write_text(json.dumps(app, indent=2) + "\n", encoding="utf-8")

    orig = _read_instruction(app_dir)
    _write_instruction(
        app_dir,
        orig + "\n- If it is true, continue normally.\n",
    )
    assert check_tool_visibility.check(app_dir) == []

    _write_instruction(
        app_dir,
        orig
        + "\n- If verified is false, stop.\n"
        + "- When order.status == 'Delivered', continue.\n"
        + "- If it is true, continue normally.\n",
    )
    problems = check_tool_visibility.check(app_dir)
    assert any(
        "prose state check on variable 'verified'" in p for p in problems
    ), problems
    assert any(
        "prose state check on variable 'order.status'" in p for p in problems
    ), problems
    assert not any("'it'" in p for p in problems), problems


def test_hidden_playbook_and_task_lists_flagged(
    tmp_path: pathlib.Path,
) -> None:
    """Catches hiding <playbook_list> at start or <task_list> inside task."""
    check_tool_visibility = _load_script("check_tool_visibility")

    # Hidden <playbook_list> at start state.
    app_pb = _copy_sample(tmp_path, "hidden_pb")
    orig_pb = _read_instruction(app_pb)
    _write_instruction(
        app_pb,
        orig_pb.replace(
            "<playbook_list>",
            "{{% if current_playbook %}}\n<playbook_list>",
        ).replace(
            "</playbook_list>",
            "</playbook_list>\n{{% endif %}}",
        ),
    )
    pb_problems = check_tool_visibility.check(app_pb)
    assert any(
        "<playbook_list> isn't rendered in 'start'" in p for p in pb_problems
    ), pb_problems
    assert any(
        "playbook 'live_agent' isn't in the playbook list the model sees at"
        " the start" in p
        for p in pb_problems
    ), pb_problems

    # Hidden <task_list> when a task is loaded (#598).
    app_task = _copy_sample(tmp_path, "hidden_task")
    orig_task = _read_instruction(app_task)
    updated_task = orig_task.replace(
        "{{% if current_playbook %}}\n<task_list>",
        "{{% if current_playbook and not current_task %}}\n<task_list>",
    )
    assert updated_task != orig_task
    _write_instruction(app_task, updated_task)
    task_problems = check_tool_visibility.check(app_task)
    assert any(
        "<task_list> isn't rendered in 'live_agent / live_agent_escalation'"
        in p
        for p in task_problems
    ), task_problems


def test_router_mismatch_missing_tool_ref_and_jinja_syntax_error(
    tmp_path: pathlib.Path,
) -> None:
    """Flags router list mismatches, unknown @TOOL refs, and Jinja syntax."""
    check_tool_visibility = _load_script("check_tool_visibility")

    # 1. Missing {@TOOL: ...} ref and missing handoff reason in instruction.
    app_ref = _copy_sample(tmp_path, "missing_ref")
    orig = _read_instruction(app_ref)
    _write_instruction(
        app_ref,
        orig + "\nCall {@TOOL: unknown_helper_tool}.\n",
    )
    ref_problems = check_tool_visibility.check(app_ref)
    assert any(
        "instruction references {@TOOL: unknown_helper_tool}" in p
        for p in ref_problems
    ), ref_problems

    # 2. Router mismatch: extra playbook in VALID_PLAYBOOKS.
    app_router = _copy_sample(tmp_path, "router_mismatch")
    op_path = (
        app_router
        / "tools"
        / "open_playbook"
        / "python_function"
        / "python_code.py"
    )
    op_path.write_text(
        op_path.read_text(encoding="utf-8").replace(
            'VALID_PLAYBOOKS = ("live_agent",)',
            'VALID_PLAYBOOKS = ("live_agent", "billing")',
        ),
        encoding="utf-8",
    )
    mismatch_problems = check_tool_visibility.check(app_router)
    assert any("VALID_PLAYBOOKS" in p for p in mismatch_problems), (
        mismatch_problems
    )

    # 3. Unclosed Jinja block returns readable syntax error instead of raising.
    app_syntax = _copy_sample(tmp_path, "jinja_syntax")
    _write_instruction(
        app_syntax,
        orig + "\n{{% if current_playbook %}}\nUnclosed\n",
    )
    syntax_problems = check_tool_visibility.check(app_syntax)
    assert any("Jinja syntax error on line" in p for p in syntax_problems), (
        syntax_problems
    )


def test_hidden_tool_in_default_and_ast_synthesized_branch(
    tmp_path: pathlib.Path,
) -> None:
    """Catches set_next_agent_actions in default and AST-synthesized branch."""
    check_tool_visibility = _load_script("check_tool_visibility")

    # Subcase A: hidden tool in default <current_step>.
    app_default = _copy_sample(tmp_path, "hidden_default")
    base_text = _read_instruction(app_default)
    updated_default = base_text.replace(
        "Call {@TOOL: execute_live_agent_handover}",
        "Call {@TOOL: execute_live_agent_handover} and"
        " {@TOOL: set_next_agent_actions}",
    )
    assert updated_default != base_text
    _write_instruction(app_default, updated_default)
    problems_default = check_tool_visibility.check(app_default)
    assert any("set_next_agent_actions" in p for p in problems_default), (
        problems_default
    )

    # Subcase B: hidden tool inside {{% if not step_two_ready %}} ... {{% else %}}
    # caught automatically via AST branch synthesis without extra_states.
    app_branch = _copy_sample(tmp_path, "hidden_branch")
    app_path = app_branch / "app.json"
    app = json.loads(app_path.read_text(encoding="utf-8"))
    app["variableDeclarations"].append(
        {
            "name": "step_two_ready",
            "description": "True when step two is unlocked.",
            "schema": {"type": "BOOLEAN", "default": False},
        }
    )
    app_path.write_text(json.dumps(app, indent=2) + "\n", encoding="utf-8")

    updated_branch = base_text.replace(
        "<current_step>\n- Call {@TOOL: execute_live_agent_handover} right away"
        " without replying first. Pass a short `reason` and a one-sentence"
        " `pre_transfer_message` letting the customer know you are connecting"
        " them.\n</current_step>",
        "<current_step>\n{{% if not step_two_ready %}}\n- Call {@TOOL:"
        " execute_live_agent_handover} right away without replying first.\n"
        "{{% else %}}\n- Call {@TOOL: set_next_agent_actions} directly.\n"
        "{{% endif %}}\n</current_step>",
    )
    assert updated_branch != base_text
    _write_instruction(app_branch, updated_branch)
    problems_branch = check_tool_visibility.check(app_branch)
    assert any(
        "set_next_agent_actions" in p and "step_two_ready=True" in p
        for p in problems_branch
    ), problems_branch


def test_dotted_object_defaults_undeclared_props_and_ast_synthesis(
    tmp_path: pathlib.Path,
) -> None:
    """Verifies OBJECT _empty, _nested_property_problems, and dotted AST."""
    check_tool_visibility = _load_script("check_tool_visibility")

    # 1. _empty / _defaults for nested OBJECT schemas without top-level default.
    object_schema = {
        "type": "OBJECT",
        "properties": {
            "order_id": {"type": "STRING"},
            "ready": {"type": "BOOLEAN"},
            "meta": {
                "type": "OBJECT",
                "properties": {
                    "attempts": {"type": "INTEGER"},
                    "tags": {"type": "ARRAY"},
                },
            },
        },
    }
    assert check_tool_visibility._empty(object_schema) == {
        "order_id": "",
        "ready": False,
        "meta": {"attempts": 0, "tags": []},
    }

    # 2. _nested_property_problems catches undeclared dotted property.
    app_prop = _copy_sample(tmp_path, "nested_prop")
    app_path = app_prop / "app.json"
    app_cfg = json.loads(app_path.read_text(encoding="utf-8"))
    app_cfg["variableDeclarations"].append(
        {"name": "order", "schema": object_schema}
    )
    app_path.write_text(json.dumps(app_cfg, indent=2) + "\n", encoding="utf-8")

    orig = _read_instruction(app_prop)
    _write_instruction(
        app_prop,
        orig + "\n{{% if order.ready %}}ready{{% endif %}}\n",
    )
    assert check_tool_visibility.check(app_prop) == []

    _write_instruction(
        app_prop,
        orig + "\n{{% if order.missing_field %}}oops{{% endif %}}\n",
    )
    prop_problems = check_tool_visibility.check(app_prop)
    assert any(
        "order.missing_field isn't a declared property of 'order'" in p
        for p in prop_problems
    ), prop_problems

    # 3. Dotted AST branch synthesis triggers on order.ready and
    # return_details.mode == 'pickup' automatically without --state.
    app_synth = _copy_sample(tmp_path, "dotted_synth")
    synth_app_path = app_synth / "app.json"
    synth_cfg = json.loads(synth_app_path.read_text(encoding="utf-8"))
    synth_cfg["variableDeclarations"].extend(
        [
            {"name": "order", "schema": object_schema},
            {
                "name": "return_details",
                "schema": {
                    "type": "OBJECT",
                    "properties": {"mode": {"type": "STRING"}},
                },
            },
        ]
    )
    synth_app_path.write_text(
        json.dumps(synth_cfg, indent=2) + "\n", encoding="utf-8"
    )

    step_block = (
        "<current_step>\n- Call {@TOOL: execute_live_agent_handover} right away"
        " without replying first. Pass a short `reason` and a one-sentence"
        " `pre_transfer_message` letting the customer know you are connecting"
        " them.\n</current_step>"
    )
    assert step_block in orig

    # Case 3a: hidden tool inside `if order.ready`.
    _write_instruction(
        app_synth,
        orig.replace(
            step_block,
            "<current_step>\n"
            "{{% if order.ready %}}\n"
            "- Ready: call {@TOOL: set_next_agent_actions} (`reason`).\n"
            "{{% elif return_details.mode == 'pickup' %}}\n"
            "- Pickup: call {@TOOL: execute_live_agent_handover} (`reason`).\n"
            "{{% else %}}\n"
            "- Other: call {@TOOL: execute_live_agent_handover} (`reason`).\n"
            "{{% endif %}}\n"
            "</current_step>",
        ),
    )
    synth_if = check_tool_visibility.check(app_synth)
    assert any(
        "set_next_agent_actions is visible in:" in p
        and "live_agent / live_agent_escalation" in p
        and "ready=True" in p
        for p in synth_if
    ), synth_if

    # Case 3b: hidden tool inside `elif return_details.mode == 'pickup'`.
    _write_instruction(
        app_synth,
        orig.replace(
            step_block,
            "<current_step>\n"
            "{{% if order.ready %}}\n"
            "- Ready: call {@TOOL: execute_live_agent_handover} (`reason`).\n"
            "{{% elif return_details.mode == 'pickup' %}}\n"
            "- Pickup: call {@TOOL: set_next_agent_actions} (`reason`).\n"
            "{{% else %}}\n"
            "- Other: call {@TOOL: execute_live_agent_handover} (`reason`).\n"
            "{{% endif %}}\n"
            "</current_step>",
        ),
    )
    synth_elif = check_tool_visibility.check(app_synth)
    assert any(
        "set_next_agent_actions is visible in:" in p
        and "live_agent / live_agent_escalation" in p
        and "mode=pickup" in p
        for p in synth_elif
    ), synth_elif


def test_tool_name_static_router_state_and_leftover_problems(
    tmp_path: pathlib.Path,
) -> None:
    """Covers #598 tool name, static start, router state, and leftover checks."""
    check_tool_visibility = _load_script("check_tool_visibility")
    sync_tool_descriptions = _load_script("sync_tool_descriptions")

    # 1. _tool_name_problems: outside {@TOOL: ...}, inside <role>/<constraints>,
    # and verbatim repeat wording.
    app_names = _copy_sample(tmp_path, "tool_names")
    orig = _read_instruction(app_names)
    _write_instruction(
        app_names,
        orig.replace(
            "<role>\n",
            "<role>\nUse graceful_end_session if needed.\n",
        ).replace(
            "<constraints>\n",
            "<constraints>\nCheck {@TOOL: open_playbook} rules.\n",
        )
        + "\nRepeat the result exactly as returned.\n",
    )
    name_problems = check_tool_visibility.check(app_names)
    assert any(
        "'graceful_end_session' is named outside {@TOOL: ...}" in p
        for p in name_problems
    ), name_problems
    assert any(
        "'graceful_end_session' is named in <role>" in p for p in name_problems
    ), name_problems
    assert any(
        "'open_playbook' is named in <constraints>" in p for p in name_problems
    ), name_problems
    assert any("repeat tool output" in p for p in name_problems), name_problems

    # 2. _static_tool_problems: non-anytime tool visible at start.
    app_static = _copy_sample(tmp_path, "static_start")
    _write_instruction(
        app_static,
        orig + "\nCall {@TOOL: load_task} at any time.\n",
    )
    static_problems = check_tool_visibility.check(app_static)
    assert any(
        "tool 'load_task' is visible before any playbook is open" in p
        for p in static_problems
    ), static_problems

    # 3. _router_state_problems: open_playbook/load_task writing domain state
    # or reading rationale.
    app_router = _copy_sample(tmp_path, "router_state")
    op_path = (
        app_router
        / "tools"
        / "open_playbook"
        / "python_function"
        / "python_code.py"
    )
    op_path.write_text(
        op_path.read_text(encoding="utf-8").replace(
            'context.state["current_task"] = ""',
            'context.state["current_task"] = ""\n'
            '  context.state["account_id"] = rationale',
        ),
        encoding="utf-8",
    )
    lt_path = (
        app_router
        / "tools"
        / "load_task"
        / "python_function"
        / "python_code.py"
    )
    lt_path.write_text(
        lt_path.read_text(encoding="utf-8").replace(
            'context.state["current_task"] = name',
            'context.state.update({"current_task": name, "notes": "x"})',
        ),
        encoding="utf-8",
    )
    router_problems = check_tool_visibility.check(app_router)
    assert any(
        "router tool 'open_playbook' writes state 'account_id'" in p
        for p in router_problems
    ), router_problems
    assert any(
        "router tool 'open_playbook' reads `rationale`" in p
        for p in router_problems
    ), router_problems
    assert any(
        "router tool 'load_task' calls state.update()" in p
        for p in router_problems
    ), router_problems

    # 4. _leftover_problems: ignores tools invoked via Part.from_function_call,
    # flags truly unused registered tools.
    app_leftover = _copy_sample(tmp_path, "leftover")
    for extra_tool in ("callback_helper_tool", "unused_orphan_tool"):
        fn_dir = app_leftover / "tools" / extra_tool / "python_function"
        fn_dir.mkdir(parents=True)
        (fn_dir / "python_code.py").write_text(
            "from typing import Any\n\n"
            f"def {extra_tool}() -> dict[str, Any]:\n"
            '  """Internal helper tool."""\n'
            '  return {"status": "ok"}\n',
            encoding="utf-8",
        )
    sync_tool_descriptions.sync(app_leftover, write=True)

    agent_path = (
        app_leftover / "agents" / "Support_Agent" / "Support_Agent.json"
    )
    agent_cfg = json.loads(agent_path.read_text(encoding="utf-8"))
    agent_cfg["tools"].extend(["callback_helper_tool", "unused_orphan_tool"])
    agent_path.write_text(
        json.dumps(agent_cfg, indent=2) + "\n", encoding="utf-8"
    )

    cb_path = (
        app_leftover
        / "agents"
        / "Support_Agent"
        / "before_model_callbacks"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb_path.write_text(
        cb_path.read_text(encoding="utf-8")
        + "\n\ndef _emit_helper() -> Part:\n"
        + '  return Part.from_function_call(name="callback_helper_tool",'
        + " args={})\n",
        encoding="utf-8",
    )

    leftover_problems = check_tool_visibility.check(app_leftover)
    assert not any("callback_helper_tool" in p for p in leftover_problems), (
        leftover_problems
    )
    assert any(
        "tool 'unused_orphan_tool' is registered" in p
        for p in leftover_problems
    ), leftover_problems


# ── Tool Code & Docstring Checks ─────────────────────────────────────────


def test_returned_dict_keys_and_spread_checks(
    tmp_path: pathlib.Path,
) -> None:
    """Ignores internal helper dicts; flags prose keys and spread returns."""
    check_tool_visibility = _load_script("check_tool_visibility")

    # Unit check on _returned_dict_keys directly.
    helper_tree = ast.parse(
        "def fn():\n"
        '  internal = {"message": "ignored"}\n'
        "  return len(internal)\n"
    )
    assert check_tool_visibility._returned_dict_keys(helper_tree.body[0]) == (
        set()
    )

    app_dir = _copy_sample(tmp_path)
    tool_code = (
        app_dir
        / "tools"
        / "execute_live_agent_handover"
        / "python_function"
        / "python_code.py"
    )
    original = tool_code.read_text(encoding="utf-8")

    # 1. Internal non-returned helper dict with "message" is NOT flagged.
    tool_code.write_text(
        original
        + "\n\ndef _internal_helper() -> int:\n"
        + '  payload = {"message": "internal log payload"}\n'
        + "  return len(payload)\n",
        encoding="utf-8",
    )
    assert check_tool_visibility.check(app_dir) == []

    # 2. Returned dict literal with "message" IS flagged.
    updated_lit = original.replace(
        'return {"status": "transferring", "reason": handoff_reason}',
        'return {"status": "ok", "message": "Done"}',
    )
    assert updated_lit != original
    tool_code.write_text(updated_lit, encoding="utf-8")
    problems_lit = check_tool_visibility.check(app_dir)
    assert any(
        "execute_live_agent_handover" in p and "'message'" in p
        for p in problems_lit
    ), problems_lit

    # 3. Returned dict(status="ok", phrase=...) IS flagged.
    updated_call = original.replace(
        'return {"status": "transferring", "reason": handoff_reason}',
        'return dict(status="ok", phrase="Please hold")',
    )
    assert updated_call != original
    tool_code.write_text(updated_call, encoding="utf-8")
    problems_call = check_tool_visibility.check(app_dir)
    assert any(
        "execute_live_agent_handover" in p and "'phrase'" in p
        for p in problems_call
    ), problems_call

    # 4. Returned text_to_say IS flagged.
    updated_tts = original.replace(
        'return {"status": "transferring", "reason": handoff_reason}',
        'out = {"status": "ok"}\n  out["text_to_say"] = "Say this"\n  return out',
    )
    assert updated_tts != original
    tool_code.write_text(updated_tts, encoding="utf-8")
    problems_tts = check_tool_visibility.check(app_dir)
    assert any(
        "execute_live_agent_handover" in p and "'text_to_say'" in p
        for p in problems_tts
    ), problems_tts

    # 5. Returned **spread dict IS flagged.
    updated_spread = original.replace(
        'return {"status": "transferring", "reason": handoff_reason}',
        'extra = {"x": 1}\n  return {**extra, "status": "ok"}',
    )
    assert updated_spread != original
    tool_code.write_text(updated_spread, encoding="utf-8")
    problems_spread = check_tool_visibility.check(app_dir)
    assert any(
        "execute_live_agent_handover" in p and "spreads a dict" in p
        for p in problems_spread
    ), problems_spread


def test_sample_code_after_model_callbacks_and_missing_handover_reason(
    tmp_path: pathlib.Path,
) -> None:
    """Flags NQAA123456 in prompt/docstrings, afterModelCallbacks, no reason."""
    check_tool_visibility = _load_script("check_tool_visibility")

    # Sample confirmation code in instruction.txt.
    app_instr = _copy_sample(tmp_path, "code_instr")
    _write_instruction(
        app_instr,
        _read_instruction(app_instr)
        + "\nProvide the confirmation code (such as NQAA123456) to the"
        " customer.\n",
    )
    problems_instr = check_tool_visibility.check(app_instr)
    assert any("NQAA123456" in p for p in problems_instr), problems_instr

    # Sample confirmation code in a tool docstring.
    app_doc = _copy_sample(tmp_path, "code_doc")
    graceful_code = (
        app_doc
        / "tools"
        / "graceful_end_session"
        / "python_function"
        / "python_code.py"
    )
    orig_doc = graceful_code.read_text(encoding="utf-8")
    updated_doc = orig_doc.replace(
        '"""Says goodbye and ends the conversation.',
        '"""Says goodbye and ends the conversation (such as NQAA123456).',
    )
    assert updated_doc != orig_doc
    graceful_code.write_text(updated_doc, encoding="utf-8")
    problems_doc = check_tool_visibility.check(app_doc)
    assert any("NQAA123456" in p for p in problems_doc), problems_doc

    # afterModelCallbacks banned.
    app_cb = _copy_sample(tmp_path, "after_cb")
    agent_path = app_cb / "agents" / "Support_Agent" / "Support_Agent.json"
    agent = json.loads(agent_path.read_text(encoding="utf-8"))
    agent["afterModelCallbacks"] = [{"pythonCode": "dummy.py"}]
    agent_path.write_text(json.dumps(agent, indent=2) + "\n", encoding="utf-8")
    problems_cb = check_tool_visibility.check(app_cb)
    assert any("afterModelCallbacks" in p for p in problems_cb), problems_cb

    # execute_live_agent_handover missing reason parameter.
    app_reason = _copy_sample(tmp_path, "missing_reason")
    handover_code = (
        app_reason
        / "tools"
        / "execute_live_agent_handover"
        / "python_function"
        / "python_code.py"
    )
    handover_code.write_text(
        "from typing import Any\n\n"
        "def execute_live_agent_handover() -> dict[str, Any]:\n"
        '  """Transfers the customer to a representative."""\n'
        '  return {"status": "transferring"}\n',
        encoding="utf-8",
    )
    problems_reason = check_tool_visibility.check(app_reason)
    assert any(
        "execute_live_agent_handover is missing a handoff reason" in p
        for p in problems_reason
    ), problems_reason


# ── sync_tool_descriptions ───────────────────────────────────────────────


def test_sync_tool_descriptions_reports_and_repairs(
    tmp_path: pathlib.Path,
) -> None:
    """Reports missing/stale tool JSONs and repairs them with write=True."""
    sync_tool_descriptions = _load_script("sync_tool_descriptions")
    app_dir = _copy_sample(tmp_path)

    open_spec_path = app_dir / "tools" / "open_playbook" / "open_playbook.json"
    open_spec_path.unlink()

    graceful_spec_path = (
        app_dir / "tools" / "graceful_end_session" / "graceful_end_session.json"
    )
    graceful_spec = json.loads(graceful_spec_path.read_text(encoding="utf-8"))
    graceful_spec["pythonFunction"]["description"] = "Stale description."
    graceful_spec_path.write_text(
        json.dumps(graceful_spec, indent=2) + "\n", encoding="utf-8"
    )

    assert sync_tool_descriptions.sync(app_dir, write=False) == [
        "graceful_end_session",
        "open_playbook",
    ]
    assert sync_tool_descriptions.sync(app_dir, check=True) == [
        "graceful_end_session",
        "open_playbook",
    ]
    assert not open_spec_path.exists()

    updated = sync_tool_descriptions.sync(app_dir, write=True)
    assert updated == ["graceful_end_session", "open_playbook"]
    assert open_spec_path.exists()

    created_spec = json.loads(open_spec_path.read_text(encoding="utf-8"))
    assert created_spec["name"] == "open_playbook"
    assert created_spec["executionType"] == "SYNCHRONOUS"
    assert (
        created_spec["pythonFunction"]["pythonCode"]
        == "tools/open_playbook/python_function/python_code.py"
    )
    assert "Opens the playbook" in created_spec["pythonFunction"]["description"]
    assert sync_tool_descriptions.sync(app_dir, write=False) == []


# ── run_locally Details ──────────────────────────────────────────────────


def test_run_locally_object_defaults_literal_exprs_end_session_and_annotations(
    tmp_path: pathlib.Path,
) -> None:
    """Tests OBJECT _empty defaults, literal tags, end_session, and Literal."""
    run_locally = _load_script("run_locally")
    app_dir = _copy_sample(tmp_path)

    app_path = app_dir / "app.json"
    app_cfg = json.loads(app_path.read_text(encoding="utf-8"))
    app_cfg["variableDeclarations"].append(
        {
            "name": "case_record",
            "schema": {
                "type": "OBJECT",
                "properties": {
                    "case_id": {"type": "STRING"},
                    "attempts": {"type": "INTEGER"},
                    "confirmed": {"type": "BOOLEAN"},
                    "tags": {"type": "ARRAY"},
                    "meta": {
                        "type": "OBJECT",
                        "properties": {"source": {"type": "STRING"}},
                    },
                },
            },
        }
    )
    app_path.write_text(json.dumps(app_cfg, indent=2) + "\n", encoding="utf-8")

    cb_path = (
        app_dir
        / "agents"
        / "Support_Agent"
        / "before_model_callbacks"
        / "before_model_callbacks_01"
        / "python_code.py"
    )
    cb_path.write_text(
        "ModeType = Literal['greeting', 'handoff', 'normal']\n"
        "StateDict = TypedDict('StateDict', {'x': Union[str, int]})\n"
        + cb_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    _write_instruction(
        app_dir,
        _read_instruction(app_dir)
        + "\nLiteral ternary: {{ 'x' if played_greeting else 'y' }}\n"
        + "Literal filter: {{ current_playbook | default('none') }}\n",
    )

    runner = run_locally.Runner(app_dir)
    assert runner.state["case_record"] == {
        "case_id": "",
        "attempts": 0,
        "confirmed": False,
        "tags": [],
        "meta": {"source": ""},
    }

    runner.state["played_greeting"] = True
    resp_model, req_model = runner.run_callback()
    assert resp_model is None
    assert req_model.config is not None
    assert req_model.config.system_instruction is not None
    assert req_model.config.system_instruction.parts is not None
    rendered_text = req_model.config.system_instruction.parts[0].text or ""
    assert "{{ 'x' if played_greeting else 'y' }}" in rendered_text
    assert "{{ current_playbook | default('none') }}" in rendered_text

    runner.state["next_agent_actions"] = {
        "end_session": {
            "reason": "verification_failed",
            "escalated": True,
        }
    }
    resp_end, _ = runner.run_callback()
    assert resp_end is not None
    lines = run_locally._describe(resp_end)
    assert any(
        "end_session" in line and "verification_failed" in line
        for line in lines
    ), lines
    assert resp_end.content is not None
    end_parts = resp_end.content.parts or []
    assert len(end_parts) == 1
    assert end_parts[0].function_call is not None
    assert end_parts[0].function_call.args == {
        "reason": "verification_failed",
        "session_escalated": True,
    }

    # Verify execute_live_agent_handover consumes pending_handoff_reason.
    runner.state["next_agent_actions"] = {}
    runner.state["pending_handoff_reason"] = "verification_failed"
    res_ho = runner.call_tool("execute_live_agent_handover", {})
    assert res_ho["reason"] == "verification_failed"
    assert runner.state["pending_handoff_reason"] == ""
    resp_pending, _ = runner.run_callback()
    assert resp_pending is not None
    pending_desc = "\n".join(run_locally._describe(resp_pending))
    assert "end_session" in pending_desc
    assert "verification_failed" in pending_desc
