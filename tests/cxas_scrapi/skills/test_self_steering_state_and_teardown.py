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

"""Offline unit tests for self-steering sample action state and teardown."""

from __future__ import annotations

import base64
import importlib.util
import json
import pathlib
import types
from typing import Any

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SKILL_DIR = _REPO_ROOT / ".agents" / "skills" / "cxas-self-steering-agent"
_SAMPLE_DIR = _SKILL_DIR / "resources" / "sample-agent"
_RUN_LOCALLY_PATH = _SKILL_DIR / "scripts" / "run_locally.py"


def _load_run_locally() -> Any:
    spec = importlib.util.spec_from_file_location(
        "self_steering_run_locally", _RUN_LOCALLY_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _exec_tool_with_isolated_subtools(
    tool_name: str,
    session_state: dict[str, Any],
    kwargs: dict[str, Any],
) -> Any:
    """Runs a sample tool with isolated state on nested sub-tool calls.

    Mirrors the isolated tool sandbox runtime, where nested sub-tool
    invocations receive a separate empty state dictionary rather than a live
    reference to the caller's `context.state`.
    """

    class _IsolatedToolsProxy:
        def __getattr__(self, subtool_name: str) -> Any:
            def _call(
                args: dict[str, Any] | None = None, **sub_kwargs: Any
            ) -> Any:
                sub_path = (
                    _SAMPLE_DIR
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
        _SAMPLE_DIR / "tools" / tool_name / "python_function" / "python_code.py"
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


@pytest.mark.parametrize(
    ("tool_name", "kwargs", "expect_end_session", "expected_escalated"),
    [
        ("play_sms_legal_disclosure", {}, False, False),
        (
            "graceful_end_session",
            {"goodbye_message": "Thanks for shopping with us!"},
            True,
            False,
        ),
        (
            "execute_live_agent_handover",
            {
                "reason": "customer_requested_agent",
                "pre_transfer_message": "Connecting you with a specialist.",
            },
            True,
            True,
        ),
    ],
)
def test_action_tools_write_next_agent_actions_directly(
    tool_name: str,
    kwargs: dict[str, Any],
    expect_end_session: bool,
    expected_escalated: bool,
) -> None:
    """Action tools persist `next_agent_actions` with isolated sub-tools."""
    run_locally = _load_run_locally()
    app_cfg = json.loads((_SAMPLE_DIR / "app.json").read_text(encoding="utf-8"))
    state = {**run_locally._defaults(app_cfg), "played_greeting": True}

    _exec_tool_with_isolated_subtools(tool_name, state, kwargs)

    queued = state.get("next_agent_actions")
    assert isinstance(queued, dict), (
        f"{tool_name} dropped next_agent_actions under isolated sub-tool state"
    )
    assert "customized_response" in queued
    assert queued["customized_response"]["disable_barge_in"] is True

    if expect_end_session:
        assert queued.get("end_session") is not None
        assert queued["end_session"]["escalated"] is expected_escalated
    else:
        assert "end_session" not in queued


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
def test_callback_requeues_end_session_without_repeating_goodbye(
    tool_name: str,
    kwargs: dict[str, Any],
    expected_escalated: bool,
) -> None:
    """Streaming mid-goodbye turns re-emit end_session without waking LLM."""
    run_locally = _load_run_locally()
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

    # Simulate speech or keypad input arriving during streaming voice audio
    # before the call closes, triggering a second callback pass.
    second_response, _ = runner.run_callback()
    assert second_response is not None, (
        "Second callback pass returned None and woke model after end_session"
    )
    assert second_response.content is not None
    second_parts = second_response.content.parts or []
    assert not any(p.inline_data is not None for p in second_parts)
    end_calls = [
        p.function_call
        for p in second_parts
        if p.has_function_call("end_session") and p.function_call is not None
    ]
    assert len(end_calls) == 1
    assert end_calls[0].args["session_escalated"] is expected_escalated


def test_callback_clears_non_terminal_customized_response_after_one_pass() -> (
    None
):
    """Non-ending disclosures play once and let the next turn call the LLM."""
    run_locally = _load_run_locally()
    app_cfg = json.loads((_SAMPLE_DIR / "app.json").read_text(encoding="utf-8"))
    state = {**run_locally._defaults(app_cfg), "played_greeting": True}
    runner = run_locally.Runner(_SAMPLE_DIR, state)

    runner.call_tool("play_sms_legal_disclosure", {})
    first_response, _ = runner.run_callback()
    assert first_response is not None
    assert first_response.content is not None
    first_parts = first_response.content.parts or []
    assert len(first_parts) == 1
    assert first_parts[0].inline_data is not None
    payload = json.loads(
        base64.b64decode(first_parts[0].inline_data.data or b"")
    )
    assert "messaging" in payload["content"].lower()

    second_response, request = runner.run_callback()
    assert second_response is None
    hidden = set(request.config.excluded_tools or [])
    assert "open_playbook" not in hidden
