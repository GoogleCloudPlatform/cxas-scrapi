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

"""Unit tests for the S021 guardrail Jinja syntax lint rule."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from cxas_scrapi.utils.lint_rules.structure import GuardrailJinjaSyntax
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    build_registry,
    run_rules,
)


def _context(app_root: Path) -> LintContext:
    return LintContext(
        project_root=app_root,
        app_dir=app_root,
        evals_dir=app_root / "evals",
        app_root=app_root,
    )


def _write_guardrail(app_root: Path, policy_key: str, prompt: str) -> Path:
    gr_dir = app_root / "guardrails" / "safety"
    gr_dir.mkdir(parents=True, exist_ok=True)
    (gr_dir / "safety.json").write_text(
        json.dumps({"displayName": "safety", policy_key: {"prompt": prompt}})
    )
    return gr_dir


@pytest.mark.parametrize(
    "prompt", ["Hello {user_name}!", "Hello @@brand.name@@!"]
)
@pytest.mark.parametrize("policy_key", ["llmPolicy", "llm_policy"])
def test_s021_non_jinja_placeholders_pass(
    tmp_path: Path, policy_key: str, prompt: str
) -> None:
    gr_dir = _write_guardrail(tmp_path, policy_key, prompt)
    assert GuardrailJinjaSyntax().check(gr_dir, "", _context(tmp_path)) == []


@pytest.mark.parametrize(
    "prompt", ["Hello {{ name }}!", "{% if vip %}VIP{% endif %}"]
)
@pytest.mark.parametrize("policy_key", ["llmPolicy", "llm_policy"])
def test_s021_jinja_tokens_flagged(
    tmp_path: Path, policy_key: str, prompt: str
) -> None:
    gr_dir = _write_guardrail(tmp_path, policy_key, prompt)
    results = GuardrailJinjaSyntax().check(gr_dir, "", _context(tmp_path))
    assert [r.rule_id for r in results] == ["S021"]
    assert results[0].file == "guardrails/safety/safety.json"


def test_s021_guardrail_without_llm_policy_ignored(tmp_path: Path) -> None:
    gr_dir = tmp_path / "guardrails" / "filter"
    gr_dir.mkdir(parents=True)
    (gr_dir / "filter.json").write_text(
        json.dumps({"contentFilter": {"bannedContents": ["{{x}}"]}})
    )
    assert GuardrailJinjaSyntax().check(gr_dir, "", _context(tmp_path)) == []


def test_s021_null_policy_or_non_dict_json_does_not_crash(
    tmp_path: Path,
) -> None:
    gr_dir = tmp_path / "guardrails" / "broken"
    gr_dir.mkdir(parents=True)
    gr_file = gr_dir / "broken.json"
    for raw in ("[]", '{"llmPolicy": null}', '{"llm_policy": {"prompt": 123}}'):
        gr_file.write_text(raw)
        assert (
            GuardrailJinjaSyntax().check(gr_dir, "", _context(tmp_path)) == []
        )


def test_s021_dispatched_via_registry(tmp_path: Path) -> None:
    (tmp_path / "app.json").write_text(json.dumps({"displayName": "app"}))
    _write_guardrail(tmp_path, "llmPolicy", "Hello {{ name }}!")
    report = LintReport()
    run_rules(
        build_registry(),
        LintConfig(),
        _context(tmp_path),
        Discovery(tmp_path, tmp_path / "evals"),
        report,
        specific_rules={"S021"},
    )
    assert [r.rule_id for r in report.results] == ["S021"]


@pytest.mark.parametrize(
    "prompt",
    ["{% for x in items %}{{ x }}{% endfor %}", "{# internal note #}"],
)
def test_s021_flags_for_loops_and_jinja_comments(
    tmp_path: Path, prompt: str
) -> None:
    gr_dir = _write_guardrail(tmp_path, "llmPolicy", prompt)
    res = GuardrailJinjaSyntax().check(gr_dir, "", _context(tmp_path))
    assert [r.rule_id for r in res] == ["S021"]


@pytest.mark.parametrize(
    "payload",
    [
        {"llmPromptSecurity": {"customPolicy": {"prompt": "Hi {{ user }}"}}},
        {
            "llm_prompt_security": {
                "custom_policy": {"prompt": "{% if x %}y{% endif %}"}
            }
        },
        {"customPolicy": {"prompt": "{# comment #}"}},
    ],
)
@pytest.mark.parametrize("ext", [".json", ".yaml", ".yml"])
def test_s021_detects_jinja_in_custom_policy_and_yaml_guardrails(
    tmp_path: Path, payload: dict[str, Any], ext: str
) -> None:
    gr_dir = tmp_path / "guardrails" / "sec"
    gr_dir.mkdir(parents=True)
    cfg = gr_dir / f"sec{ext}"
    text = (
        json.dumps(payload)
        if ext == ".json"
        else yaml.safe_dump(payload, sort_keys=False)
    )
    cfg.write_text(text, encoding="utf-8")
    res = GuardrailJinjaSyntax().check(gr_dir, "", _context(tmp_path))
    assert [r.rule_id for r in res] == ["S021"]
    assert res[0].file == f"guardrails/sec/sec{ext}"
