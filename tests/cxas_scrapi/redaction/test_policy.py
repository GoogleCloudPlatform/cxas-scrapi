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

import pathlib
import typing

import pydantic
import pytest
import yaml

from cxas_scrapi.redaction.policy import (
    DEFAULT_POLICY_FILENAME,
    Action,
    RedactionExpectations,
    RedactionPolicy,
    find_policy_file,
    load_policy,
    merge_redaction_expectations,
)

from .conftest import POLICY_DICT


def _write(path: pathlib.Path, data: typing.Any) -> pathlib.Path:
    path.write_text(yaml.safe_dump(data))
    return path


def test_load_policy(tmp_path: pathlib.Path) -> None:
    p = load_policy(_write(tmp_path / "p.yaml", POLICY_DICT))
    assert p.category("payment_card").action == Action.MUST_REDACT
    assert p.category("missing") is None
    assert p.marker_regex.search("x [CREDIT_CARD_NUMBER] y")
    assert p.marker_regex.search("x #### y")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.update(version=2), "unsupported policy version"),
        (
            lambda d: d["categories"].append(dict(d["categories"][0])),
            "duplicate category id",
        ),
        (
            lambda d: d["categories"][0].update(patterns=["("]),
            "invalid regex",
        ),
        (lambda d: d["categories"][0].update(id="bad id"), "may only contain"),
        (lambda d: d["categories"][0].update(action="nope"), "action"),
        (lambda d: d.update(redaction_markers=[]), "must not be empty"),
        (lambda d: d.update(unknown=1), "unknown"),
    ],
)
def test_policy_validation_errors(
    mutate: typing.Callable[[dict], None], message: str
) -> None:
    data = yaml.safe_load(yaml.safe_dump(POLICY_DICT))
    mutate(data)
    with pytest.raises(pydantic.ValidationError, match=message):
        RedactionPolicy.model_validate(data)


def test_load_policy_rejects_non_mapping(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="mapping"):
        load_policy(_write(tmp_path / "p.yaml", ["a"]))


def test_values_are_stringified(policy: RedactionPolicy) -> None:
    data = yaml.safe_load(yaml.safe_dump(POLICY_DICT))
    data["categories"][2]["values"] = [988, 911]
    p = RedactionPolicy.model_validate(data)
    assert p.category("crisis_line").values == ["988", "911"]


def test_with_values_merges_and_validates(policy: RedactionPolicy) -> None:
    merged = policy.with_values({"crisis_line": ["112"]})
    assert merged.category("crisis_line").values == ["988", "911", "112"]
    assert policy.category("crisis_line").values == ["988", "911"]
    with pytest.raises(ValueError, match="unknown categories: nope"):
        policy.with_values({"nope": ["1"]})


@pytest.mark.parametrize(
    ("raw", "expected_policy"),
    [(True, None), ("custom.yaml", "custom.yaml"), ({}, None)],
)
def test_expectations_parse(
    raw: typing.Any, expected_policy: str | None
) -> None:
    exp = RedactionExpectations.parse(raw)
    assert exp is not None
    assert exp.policy == expected_policy


def test_expectations_parse_disabled_and_invalid() -> None:
    assert RedactionExpectations.parse(None) is None
    assert RedactionExpectations.parse(False) is None
    with pytest.raises(ValueError, match="must be true"):
        RedactionExpectations.parse(3)


def test_expectations_resolve_policy(tmp_path: pathlib.Path) -> None:
    _write(tmp_path / DEFAULT_POLICY_FILENAME, POLICY_DICT)
    evals_dir = tmp_path / "evals" / "simulations"
    evals_dir.mkdir(parents=True)
    exp = RedactionExpectations.parse(
        {"values": {"security_code": [456]}, "require": ["payment_card"]}
    )
    policy = exp.resolve_policy(evals_dir)
    assert policy.category("security_code").values == ["456"]

    bad = RedactionExpectations.parse({"require": ["nope"]})
    with pytest.raises(ValueError, match="require references unknown"):
        bad.resolve_policy(evals_dir)


def test_find_policy_file(tmp_path: pathlib.Path) -> None:
    explicit = _write(tmp_path / "custom.yaml", POLICY_DICT)
    assert find_policy_file("custom.yaml", tmp_path) == explicit
    with pytest.raises(FileNotFoundError):
        find_policy_file("missing.yaml", tmp_path)

    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    default = _write(tmp_path / DEFAULT_POLICY_FILENAME, POLICY_DICT)
    assert find_policy_file(None, nested).resolve() == default.resolve()


def test_merge_redaction_expectations() -> None:
    assert merge_redaction_expectations(None, None) is None
    assert merge_redaction_expectations({"require": ["a"]}, False) is None
    assert merge_redaction_expectations(None, True) is True
    assert merge_redaction_expectations(True, True) is True
    assert merge_redaction_expectations("p.yaml", None) == "p.yaml"
    merged = merge_redaction_expectations(
        {"policy": "p.yaml", "values": {"a": ["1"]}, "require": ["a"]},
        {"values": {"a": ["1", "2"], "b": "3"}, "require": ["b"]},
    )
    assert merged == {
        "policy": "p.yaml",
        "values": {"a": ["1", "2"], "b": ["3"]},
        "require": ["a", "b"],
    }
