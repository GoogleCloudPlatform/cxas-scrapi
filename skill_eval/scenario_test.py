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

"""Tests for scenario abstraction."""

from absl.testing import absltest, parameterized

from skill_eval import scenario


class ScenarioTest(parameterized.TestCase):
    def test_from_yaml_valid(self):
        yaml_text = """
prompt: This is prompt.
rubric:
  - criteria: C1
    perfect: p
    good: g
    failed: f
setup:
  commands:
    - echo "hello"
"""
        s = scenario.Scenario.from_yaml("test_scenario", yaml_text)
        expected_text = """This is prompt.

## Rubric
- **Criteria**: C1
  - **Perfect**: p
  - **Good**: g
  - **Failed**: f"""
        expected = scenario.Scenario(
            name="test_scenario",
            text=expected_text,
            prompt="This is prompt.",
            rubric=[
                scenario.Rubric(
                    criteria="C1", perfect="p", good="g", failed="f"
                )
            ],
            setup=scenario.Setup(commands=('echo "hello"',)),
            assets=[],
        )
        self.assertEqual(s, expected)

    @parameterized.named_parameters(
        dict(
            testcase_name="missing_prompt",
            yaml_text="""
rubric:
  - criteria: C1
""",
            expected_regex="Missing 'prompt'",
        ),
        dict(
            testcase_name="invalid_prompt_type",
            yaml_text="""
prompt: 123
rubric:
  - criteria: C1
""",
            expected_regex="Invalid 'prompt'.*expected string",
        ),
        dict(
            testcase_name="empty_rubric",
            yaml_text="""
prompt: This is prompt.
rubric: []
""",
            expected_regex="Empty rubric",
        ),
        dict(
            testcase_name="invalid_assets_type",
            yaml_text="""
prompt: This is prompt.
rubric:
  - criteria: C1
    perfect: p
assets: "asset1.txt"
""",
            expected_regex="Invalid 'assets'.*expected list",
        ),
        dict(
            testcase_name="invalid_setup_type",
            yaml_text="""
prompt: This is prompt.
rubric:
  - criteria: C1
    perfect: p
setup: "not a dict"
""",
            expected_regex="Invalid 'setup'.*expected dict",
        ),
        dict(
            testcase_name="invalid_setup_commands_type",
            yaml_text="""
prompt: This is prompt.
rubric:
  - criteria: C1
    perfect: p
setup:
  commands: "not a list"
""",
            expected_regex="Invalid 'setup.commands'.*expected list",
        ),
        dict(
            testcase_name="invalid_setup_command_element_type",
            yaml_text="""
prompt: This is prompt.
rubric:
  - criteria: C1
    perfect: p
setup:
  commands:
    - 123
""",
            expected_regex="Invalid setup command at index 0.*expected string",
        ),
    )
    def test_from_yaml_invalid(self, yaml_text, expected_regex):
        with self.assertRaisesRegex(ValueError, expected_regex):
            scenario.Scenario.from_yaml("test", yaml_text)

    def test_from_yaml_edge_cases(self):
        yaml_text = """
prompt: This is prompt.
rubric:
  - criteria: "C1 with | pipe"
    perfect: |
      p with
      newline
assets: null
"""
        s = scenario.Scenario.from_yaml("test_edge", yaml_text)
        self.assertEqual(s.assets, [])
        expected_text = """This is prompt.

## Rubric
- **Criteria**: C1 with | pipe
  - **Perfect**: p with
                 newline
  - **Good**: Met only after nudging or with errors
  - **Failed**: Not met"""
        self.assertEqual(s.text, expected_text)


if __name__ == "__main__":
    absltest.main()
