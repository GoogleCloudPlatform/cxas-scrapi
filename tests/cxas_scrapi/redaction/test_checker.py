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

import typing

from cxas_scrapi.redaction.checker import FindingKind, check_pairs
from cxas_scrapi.redaction.extract import (
    AlignedLeaf,
    Leaf,
    align,
    conversation_to_leaves,
)
from cxas_scrapi.redaction.policy import RedactionPolicy, Surface


def _pair(
    raw: typing.Any,
    stored: typing.Any,
    surface: Surface = Surface.USER_TEXT,
    key: str = "",
) -> AlignedLeaf:
    path = f"args.{key}" if key else "text"
    r = Leaf(0, 0, 0, "user", surface, path, key, raw)
    s = Leaf(0, 0, 0, "user", surface, path, key, stored)
    return AlignedLeaf(r, s)


def _kinds(report: typing.Any) -> list[str]:
    return sorted(f.kind.value for f in report.findings)


def test_full_flow_against_fake_redaction(
    policy: RedactionPolicy,
    raw_conversation: dict[str, typing.Any],
    persisted_conversation: dict[str, typing.Any],
) -> None:
    pairs = align(
        conversation_to_leaves(raw_conversation),
        conversation_to_leaves(persisted_conversation),
    )
    report = check_pairs(pairs, policy, source="s1", mode="live")
    stats = report.stats

    assert stats["payment_card"].occurrences == 2
    assert stats["payment_card"].redacted == 2
    assert stats["security_code"].redacted == 1
    assert stats["crisis_line"].over_redacted == 2
    assert stats["operational"].over_redacted == 1
    assert stats["percent"].preserved == 1
    assert report.count(FindingKind.OVER_REDACTION) == 3
    assert report.count(FindingKind.TYPE_CHANGED) == 1
    # "ending in 1111" is redacted by the broad rule but is no category.
    assert report.count(FindingKind.UNEXPECTED_REDACTION) == 1
    assert report.info_type_counts["CREDIT_CARD_NUMBER"] >= 1
    assert not report.passed


def test_leak_detected_for_spoken_card(policy: RedactionPolicy) -> None:
    spoken = (
        "card four one one one one one one one one one one one one one one one"
    )
    report = check_pairs([_pair(spoken, spoken)], policy, "s", "live")
    assert _kinds(report) == ["leak"]
    assert report.stats["payment_card"].leaked == 1
    assert report.stats["payment_card"].redaction_recall == 0.0


def test_partial_leak_and_allowed_last_four(policy: RedactionPolicy) -> None:
    raw = "card 4111 1111 1111 1111"
    partial = check_pairs(
        [_pair(raw, "card 4111 1111 [X] [X]")], policy, "s", "live"
    )
    assert _kinds(partial) == ["partial_leak"]

    last_four = check_pairs(
        [_pair(raw, "card [X] [X] [X] 1111")], policy, "s", "live"
    )
    assert last_four.findings == []
    assert last_four.stats["payment_card"].redacted == 1


def test_key_based_categories(policy: RedactionPolicy) -> None:
    leaked = check_pairs(
        [_pair("123", "123", Surface.TOOL_ARGS, "cvv")], policy, "s", "live"
    )
    assert _kinds(leaked) == ["leak"]
    ok = check_pairs(
        [_pair("123", "[CVV]", Surface.TOOL_ARGS, "cvv")], policy, "s", "live"
    )
    assert ok.findings == []
    assert ok.stats["security_code"].redacted == 1


def test_surfaces_filter(policy: RedactionPolicy) -> None:
    narrowed = policy.model_copy(update={"surfaces": [Surface.AGENT_TEXT]})
    spoken = "988"
    report = check_pairs([_pair(spoken, "[X]")], narrowed, "s", "live")
    assert report.leaves_checked == 0
    assert report.findings == []


def test_missing_stored_leaf_is_skipped(policy: RedactionPolicy) -> None:
    raw = Leaf(0, 0, 0, "user", Surface.USER_TEXT, "text", "", "988")
    report = check_pairs([AlignedLeaf(raw, None)], policy, "s", "live")
    assert report.leaves_checked == 0


def test_not_exercised_and_thresholds(policy: RedactionPolicy) -> None:
    report = check_pairs(
        [_pair("hello", "hello")],
        policy,
        "s",
        "live",
        require=["payment_card"],
    )
    assert _kinds(report) == ["not_exercised"]
    assert report.passed

    over = check_pairs([_pair("call 988", "call [X]")], policy, "s", "live")
    assert not over.passed
    lenient = policy.model_copy(deep=True)
    lenient.thresholds.max_over_redactions = 1
    assert check_pairs(
        [_pair("call 988", "call [X]")], lenient, "s", "live"
    ).passed


def test_report_merge_and_to_dict(policy: RedactionPolicy) -> None:
    a = check_pairs([_pair("call 988", "call [X]")], policy, "a", "live")
    b = check_pairs([_pair("call 911", "call 911")], policy, "b", "live")
    a.merge(b)
    d = a.to_dict()
    assert d["summary"]["sources"] == 2
    assert d["summary"]["over_redaction"] == 1
    assert d["categories"]["crisis_line"]["occurrences"] == 2
    assert d["categories"]["crisis_line"]["preservation_rate"] == 0.5
    assert d["findings"][0]["turn"] == 1
    assert d["info_types"] == {"X": 1}
    assert d["passed"] is False
