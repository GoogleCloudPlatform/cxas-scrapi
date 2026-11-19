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

"""Offline unit tests for guardrail childAgents in self-steering sample."""

from __future__ import annotations

import json
import pathlib
from typing import Any, Optional

from cxas_scrapi.utils import callback_libs as cl

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SKILL_DIR = _REPO_ROOT / ".agents" / "skills" / "cxas-self-steering-agent"
_SAMPLE_DIR = _SKILL_DIR / "resources" / "sample-agent"
_CALLBACK_PATH = (
    _SAMPLE_DIR
    / "agents"
    / "Support_Agent"
    / "before_model_callbacks"
    / "before_model_callbacks_01"
    / "python_code.py"
)


def _load_callback() -> Any:
    namespace: dict[str, Any] = {
        name: getattr(cl, name) for name in dir(cl) if not name.startswith("_")
    }
    namespace.update(Optional=Optional, Any=Any, json=json)
    code = _CALLBACK_PATH.read_text(encoding="utf-8")
    exec(  # noqa: S102
        compile(code, str(_CALLBACK_PATH), "exec"),
        namespace,
    )
    return namespace["before_model_callback"]


def test_dynamic_tool_hiding_excludes_transfer_without_truncating_prompt() -> (
    None
):
    """Guardrail childAgents hides transfer tools and preserves prompt text."""
    cb = _load_callback()
    base_instruction = (
        "<role>\n"
        "You are an agent. Your internal name is Max for an online store.\n"
        "</role>\n"
        "<ending_the_conversation>\n"
        "Call {@TOOL: graceful_end_session} when done.\n"
        "</ending_the_conversation>\n"
        "<taskflow>\n"
        "Call {@TOOL: open_playbook} to open a playbook.\n"
        "</taskflow>"
    )
    injected_child_agent_block = (
        "\n\nYou have a list of other agents to transfer to:\n\n"
        "Agent name: Guardrail_Handler_Agent\n"
        "Agent description: Handles safety escalations.\n\n"
        "If another agent is better for answering the question according "
        "to its description, call `transfer_to_agent` function to transfer "
        "the question to that agent.\n"
        "**NOTE**: the only available agents for `transfer_to_agent` function "
        "are `Guardrail_Handler_Agent`.\n"
    )
    trailing_block = "\n<postscript>\nAlways stay polite.\n</postscript>"
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
                        cl.FunctionDeclaration(name="lookup_order"),
                        cl.FunctionDeclaration(name="end_session"),
                    ]
                ),
                cl.ToolDeclaration(
                    functionDeclarations=[
                        cl.FunctionDeclaration(name="transfer_to_agent"),
                        cl.FunctionDeclaration(name="transferToAgent"),
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
    assert "lookup_order" in excluded
    assert "end_session" in excluded
    assert "open_playbook" not in excluded
    assert "graceful_end_session" not in excluded

    assert request.config.system_instruction is not None
    assert request.config.system_instruction.parts is not None
    assert request.config.system_instruction.parts[0].text == full_instruction
