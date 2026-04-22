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

"""Unit tests for PlaybookTreeVisualizer."""

from rich.console import Console
from rich.tree import Tree

from cxas_scrapi.migration.playbook_visualizer import PlaybookTreeVisualizer


def _render(tree: Tree) -> str:
    console = Console(force_terminal=False, width=200, record=True)
    console.print(tree)
    return console.export_text()


MINIMAL_PB = {"displayName": "My Playbook"}

FULL_PB = {
    "displayName": "Full Playbook",
    "goal": "Help the user complete their request",
    "inputParameterDefinitions": [
        {
            "name": "user_id",
            "typeSchema": {"inlineSchema": {"type": "STRING"}},
        }
    ],
    "outputParameterDefinitions": [
        {
            "name": "result",
            "typeSchema": {"inlineSchema": {"type": "BOOLEAN"}},
        }
    ],
    "instruction": {
        "steps": [
            {"text": "Greet the user."},
            {
                "text": "Transfer to ${FLOW:Billing Flow}.",
                "steps": [{"text": "If billing, use ${TOOL:BillingTool}."}],
            },
        ]
    },
    "codeBlock": {"code": "def helper():\n    return True\n"},
}


class TestPlaybookTreeVisualizer:
    def test_build_tree_returns_tree_instance(self):
        tree = PlaybookTreeVisualizer(MINIMAL_PB).build_tree()
        assert isinstance(tree, Tree)

    def test_root_contains_playbook_name(self):
        rendered = _render(PlaybookTreeVisualizer(MINIMAL_PB).build_tree())
        assert "My Playbook" in rendered

    def test_goal_rendered(self):
        rendered = _render(PlaybookTreeVisualizer(FULL_PB).build_tree())
        assert "Help the user complete their request" in rendered

    def test_input_parameter_rendered(self):
        rendered = _render(PlaybookTreeVisualizer(FULL_PB).build_tree())
        assert "user_id" in rendered

    def test_output_parameter_rendered(self):
        rendered = _render(PlaybookTreeVisualizer(FULL_PB).build_tree())
        assert "result" in rendered

    def test_parameter_types_rendered(self):
        rendered = _render(PlaybookTreeVisualizer(FULL_PB).build_tree())
        assert "STRING" in rendered
        assert "BOOLEAN" in rendered

    def test_instruction_step_text_rendered(self):
        rendered = _render(PlaybookTreeVisualizer(FULL_PB).build_tree())
        assert "Greet the user" in rendered

    def test_nested_step_rendered(self):
        rendered = _render(PlaybookTreeVisualizer(FULL_PB).build_tree())
        assert "If billing" in rendered

    def test_flow_reference_in_step(self):
        rendered = _render(PlaybookTreeVisualizer(FULL_PB).build_tree())
        assert "Billing Flow" in rendered

    def test_tool_reference_in_step(self):
        rendered = _render(PlaybookTreeVisualizer(FULL_PB).build_tree())
        assert "BillingTool" in rendered

    def test_code_block_rendered(self):
        rendered = _render(PlaybookTreeVisualizer(FULL_PB).build_tree())
        assert "helper" in rendered

    def test_no_goal_skips_goal_node(self):
        pb = {"displayName": "No Goal PB", "instruction": {"steps": []}}
        rendered = _render(PlaybookTreeVisualizer(pb).build_tree())
        # The section label is "Goal:" — ensure it is absent when no goal key
        assert "Goal:" not in rendered

    def test_no_code_block_skips_code_node(self):
        pb = {"displayName": "No Code PB"}
        rendered = _render(PlaybookTreeVisualizer(pb).build_tree())
        assert "Code Block" not in rendered

    def test_empty_code_block_skips_code_node(self):
        pb = {"displayName": "Empty Code PB", "codeBlock": {"code": ""}}
        rendered = _render(PlaybookTreeVisualizer(pb).build_tree())
        assert "Code Block" not in rendered

    def test_playbook_with_no_params_skips_params_node(self):
        pb = {"displayName": "No Params PB"}
        rendered = _render(PlaybookTreeVisualizer(pb).build_tree())
        assert "Parameters" not in rendered

    def test_session_param_reference_highlighted(self):
        pb = {
            "displayName": "Session PB",
            "instruction": {
                "steps": [{"text": "Check $session.params.flag value."}]
            },
        }
        rendered = _render(PlaybookTreeVisualizer(pb).build_tree())
        assert "flag" in rendered
