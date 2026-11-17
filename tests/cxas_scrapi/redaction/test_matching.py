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

import pytest

from cxas_scrapi.redaction import matching
from cxas_scrapi.redaction.policy import Category, MatchMode


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("card 4111111111111111 ok", ["4111111111111111"]),
        ("card 4111 1111 1111 1111", ["4111111111111111"]),
        ("card 4111-1111-1111-1111", ["4111111111111111"]),
        ("four one one one, one one one one", ["41111111"]),
        ("pin is seven double five niner", ["7559"]),
        ("triple seven two", ["7772"]),
        ("4111, uh, wait, 1111", ["41111111"]),
        ("call 988, or dial 911", ["988", "911"]),
        ("one two three oh", ["1230"]),
        ("4111 {pause} 1111 <break time='1s'/> 1111", ["411111111111"]),
    ],
)
def test_digit_runs_folds_spoken_and_spaced_forms(
    text: str, expected: list[str]
) -> None:
    assert [r.digits for r in matching.digit_runs(text)] == expected


def test_homophones_only_count_next_to_digits() -> None:
    assert matching.digit_runs("I want to pay for it") == []
    assert [r.digits for r in matching.digit_runs("one two for five")] == [
        "1245"
    ]


def test_digit_run_spans_cover_source_text() -> None:
    text = "my card is 4111 1111 1111 1111 thanks"
    (run,) = matching.digit_runs(text)
    assert text[run.start : run.end] == "4111 1111 1111 1111"


def test_find_value_digits_short_values_need_whole_run() -> None:
    assert matching.find_value("text 988 now", "988", MatchMode.DIGITS)
    assert not matching.find_value("year 1988", "988", MatchMode.DIGITS)


def test_find_value_digits_long_values_match_inside_runs() -> None:
    hits = matching.find_value(
        "ref 99 4111 1111 1111 1111", "4111111111111111", MatchMode.DIGITS
    )
    assert len(hits) == 1


def test_find_value_exact_is_case_insensitive() -> None:
    hits = matching.find_value("Member h123", "H123", MatchMode.EXACT)
    assert [h.text for h in hits] == ["h123"]


def test_find_value_exact_respects_token_boundaries() -> None:
    assert not matching.find_value("member H12345678", "123", MatchMode.EXACT)
    assert matching.find_value("cvv 123.", "123", MatchMode.EXACT)
    assert matching.find_value("copay 20%!", "20%", MatchMode.EXACT)
    assert matching.find_value("pay $45.10 now", "$45.10", MatchMode.EXACT)


def test_find_partial_respects_allow_last() -> None:
    card = "4111111111112222"
    assert not matching.find_partial("ending in 2222", card, allow_last=4)
    assert matching.find_partial("ending in 2222", card, allow_last=0)
    frag = matching.find_partial("starts 411111", card, allow_last=4)
    assert [f.text for f in frag] == ["411111"]


def test_find_category_uses_values_and_patterns() -> None:
    cat = Category(
        id="c",
        action="must_redact",
        values=["abc"],
        patterns=[r"\bZ\d{3}\b"],
    )
    found = matching.find_category("abc and Z123", cat)
    assert sorted(m.text for m in found) == ["Z123", "abc"]


def test_spoken_variants_round_trip_to_same_digits() -> None:
    variants = matching.spoken_variants("4111 1111 1111 1111")
    assert (
        "four one one one one one one one one one one one one one one one"
        in (variants)
    )
    assert any("triple one" in v for v in variants)
    for v in variants:
        assert matching.only_digits(v) == "4111111111111111", v


def test_spoken_variants_non_digit_value_passthrough() -> None:
    assert matching.spoken_variants("abc") == ["abc"]
