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

"""Unit tests for ``cxas_scrapi.runtime`` helpers."""

import json
from pathlib import Path

import pytest

from cxas_scrapi.runtime import (
    MissingContentBlock,
    adapt_presentation,
    build_scoped_datastore_filter,
    check_disabled_agent,
    escape_search_filter_literal,
    get_verbatim_content_block,
    resolve_content_block_or_handoff,
)


def test_fuzz_search_filter() -> None:
    with open(
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "search_filter_fuzz.jsonl"
    ) as f:
        for line in f:
            case = json.loads(line)
            if "error" in case:
                with pytest.raises(ValueError, match="Invalid character"):
                    escape_search_filter_literal(case["input"])
            else:
                assert (
                    escape_search_filter_literal(case["input"])
                    == case["expected"]
                )


def test_build_scoped_datastore_filter() -> None:
    state = {"context": {"brand_id": "B", "country_code": "US", "lang": "en"}}
    assert (
        build_scoped_datastore_filter(state)
        == 'brand: ANY("B") AND country: ANY("US") AND language: ANY("en")'
    )


def test_content_blocks() -> None:
    cat = {"B": {"US": {"en": {"k": "val"}}}}
    assert get_verbatim_content_block(cat, "B", "US", "en", "k") == "val"
    with pytest.raises(MissingContentBlock):
        get_verbatim_content_block(cat, "B", "UK", "en", "k")

    state = {"context": {"brand_id": "B", "country_code": "UK", "lang": "en"}}
    ans = resolve_content_block_or_handoff(
        state, cat, "k", fallback_utterance="fail"
    )
    assert ans == "fail"
    assert state["context"]["handoff_reason"] == "missing_statutory_copy"


def test_adapt_presentation() -> None:
    state = {"context": {"channel_class": "DIGITAL_APP"}}
    res = adapt_presentation(state, "hello", {"w": 1})
    assert res["channel_mode"] == "digital"
    assert res["widget_json"] == '{"w": 1}'

    state_voice = {"context": {"channel_class": "VOICE_TELEPHONY"}}
    res2 = adapt_presentation(state_voice, "hello [link](http://x) **bold**")
    assert res2["channel_mode"] == "voice"
    assert res2["text"] == "hello link bold"


def test_check_disabled_agent() -> None:
    state = {"context": {"disabled_agents": ["a1"]}}
    res = check_disabled_agent(state, "a1", fallback_message="out")
    assert res["short_circuit"]
    assert check_disabled_agent(state, "a2", fallback_message="out") is None

    assert (
        check_disabled_agent(
            {"context": {"disabled_agents": '["a1", "a2"]'}},
            "a2",
            fallback_message="out",
        )
        is not None
    )
    assert (
        check_disabled_agent(
            {"context": {"disabled_agents": "a1, a3"}},
            "a3",
            fallback_message="out",
        )
        is not None
    )


def test_runtime_helpers_handle_none_context_and_null_catalog_nodes() -> None:
    state: dict[str, object] = {"context": None}
    ans = resolve_content_block_or_handoff(
        state, {"B": None}, "k", fallback_utterance="fallback"
    )
    assert ans == "fallback"
    assert state["context"] == {"handoff_reason": "missing_statutory_copy"}

    assert (
        check_disabled_agent({"context": None}, "a1", fallback_message="out")
        is None
    )
    res = adapt_presentation({"context": None}, "Hello **world**")
    assert res["channel_mode"] == "voice"
    assert res["text"] == "Hello world"

    with pytest.raises(ValueError, match="Missing required context keys"):
        build_scoped_datastore_filter({"context": None})
