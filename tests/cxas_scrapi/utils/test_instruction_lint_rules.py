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

"""Unit tests for instruction lint rules (I012, I015)."""

import json
from pathlib import Path

from cxas_scrapi.utils.lint_rules.instructions import (
    BannedLegacyXmlTags,
    UnusedToolInConfig,
)
from cxas_scrapi.utils.linter import LintContext


def test_i012_recognizes_tools_used_in_global_instruction_callbacks_and_dags(
    tmp_path: Path,
) -> None:
    ctx = LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evaluations",
        app_root=tmp_path,
    )
    (tmp_path / "global_instruction.txt").write_text(
        "Use {@TOOL: update_language} when the caller switches languages."
    )
    agent_dir = tmp_path / "agents" / "order_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "order_agent.json").write_text(
        json.dumps(
            {
                "name": "order_agent",
                "displayName": "order_agent",
                "tools": [
                    "update_language",
                    "hide_only_tool",
                    "dag_slot_setter",
                    "truly_unused_tool",
                ],
            }
        )
    )
    inst = agent_dir / "instruction.txt"
    inst.write_text("Follow the active step instructions.")

    cb_dir = agent_dir / "before_model_callbacks" / "cb_01"
    cb_dir.mkdir(parents=True)
    (cb_dir / "python_code.py").write_text(
        "# tools.truly_unused_tool({})\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    llm_request.config.hide_tool('hide_only_tool')\n"
        "    tools.truly_unused_tool_extended({})\n"
        "    tools.order_status_dag({})\n"
    )

    dag_dir = tmp_path / "tools" / "order_status_dag" / "python_function"
    dag_dir.mkdir(parents=True)
    (dag_dir / "python_code.py").write_text(
        "def order_status_dag():\n"
        "    return {'prompt': 'Call {@TOOL: dag_slot_setter} now.'}\n"
    )

    results = UnusedToolInConfig().check(inst, inst.read_text(), ctx)
    assert len(results) == 1
    assert "truly_unused_tool" in results[0].message


def test_i015_deduplicates_context_and_ignores_runtime_envelope_examples(
    tmp_path: Path,
) -> None:
    ctx = LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evaluations",
        app_root=tmp_path,
    )
    inst = tmp_path / "agents" / "root_agent" / "instruction.txt"
    inst.parent.mkdir(parents=True)
    inst.write_text(
        "Wait for the `<context>function completed</context>` message.\n"
        'If input starts with "<context>user pressed", parse digits.\n'
        "[user]: <context>user pressed 1980 on keypad.</context>\n"
        "<Context>\n"
        "Legacy context block\n"
        "</Context>\n"
    )

    results = BannedLegacyXmlTags().check(inst, inst.read_text(), ctx)
    assert len(results) == 2  # noqa: PLR2004
    assert results[0].line == 4  # noqa: PLR2004
    assert "<Context>" in results[0].message
    assert results[1].line == 6  # noqa: PLR2004
    assert "</Context>" in results[1].message
