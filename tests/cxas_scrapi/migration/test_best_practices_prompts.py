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

"""Regression tests ensuring migration prompts follow CXAS callback best practices."""

from pathlib import Path

from cxas_scrapi.migration import prompts


def _all_prompt_strings() -> str:
    chunks: list[str] = []
    for val in vars(prompts).values():
        if isinstance(val, str):
            chunks.append(val)
        elif isinstance(val, dict):
            chunks.extend(v for v in val.values() if isinstance(v, str))
        elif isinstance(val, type):
            for attr in vars(val).values():
                if isinstance(attr, str):
                    chunks.append(attr)
                elif isinstance(attr, dict):
                    chunks.extend(
                        v for v in attr.values() if isinstance(v, str)
                    )
    return "\n".join(chunks)


def test_migration_prompts_do_not_teach_partial_greeting_antipattern() -> None:
    text = _all_prompt_strings()
    assert "response.partial = True" not in text
    assert "partial=True" not in text
    assert "callback_context.state" in text
    assert "Part.from_customized_response" in text


def test_docs_and_foundry_callback_examples_avoid_partial_kwarg() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    callbacks_doc = (
        repo_root / "docs" / "design-guide" / "callbacks.md"
    ).read_text()
    callback_api = (
        repo_root
        / ".agents"
        / "skills"
        / "cxas-agent-foundry"
        / "references"
        / "callback-api.md"
    ).read_text()
    tool_doc = (
        repo_root / "docs" / "design-guide" / "tool-design.md"
    ).read_text()

    assert "partial=True)" not in callbacks_doc
    assert "partial=True)" not in callback_api
    assert "party_size: int, context)" not in tool_doc
    assert "guest_name: str, context)" not in tool_doc
