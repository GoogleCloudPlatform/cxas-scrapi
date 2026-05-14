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

import datetime
import json
from types import SimpleNamespace

from cxas_scrapi.utils.tracing import trace_report as tr

SAMPLE_DICT = {
    "name": "projects/p/locations/global/apps/a/conversations/c1",
    "display_name": "test conv",
    "source": "LIVE",
    "input_types": ["INPUT_TYPE_TEXT", "INPUT_TYPE_AUDIO"],
    "start_time": "2026-05-01T00:00:00",
    "end_time": "2026-05-01T00:05:00",
    "turns": [
        {
            "messages": [
                {
                    "role": "user",
                    "chunks": [{"text": "hello"}],
                },
                {
                    "role": "agent_a",
                    "chunks": [
                        {"text": "hi there"},
                        {
                            "tool_call": {
                                "display_name": "lookup",
                                "args": {"q": 1},
                            }
                        },
                        {
                            "tool_response": {
                                "display_name": "lookup",
                                "response": {"r": 2},
                            }
                        },
                        {
                            "agent_transfer": {"target_agent": "agent_b"},
                        },
                        {"payload": {"key": "v"}},
                    ],
                },
            ]
        },
        {
            "messages": [
                {
                    "role": "user",
                    "chunks": [{"transcript": "audio said this"}],
                },
                {
                    "role": "agent_b",
                    "chunks": [{"transcript": "audio agent reply"}],
                },
            ]
        },
    ],
}


def test_normalize_from_dict():
    n = tr.normalize(SAMPLE_DICT)
    assert n["conversation_id"] == "c1"
    assert n["channel"] == "MULTIMODAL"
    assert n["num_turns"] == 2
    kinds = [e["kind"] for e in n["entries"]]
    assert kinds == [
        "user",
        "agent",
        "tool_call",
        "tool_response",
        "agent_transfer",
        "custom_payload",
        "user",
        "agent",
    ]


def test_normalize_from_proto_like_object():
    class Proto:
        @staticmethod
        def to_dict(_):
            return SAMPLE_DICT

    n = tr.normalize(Proto())
    assert n["conversation_id"] == "c1"


def test_channel_label_paths():
    assert tr._channel_label([]) == "UNKNOWN"
    assert tr._channel_label(["INPUT_TYPE_TEXT"]) == "TEXT"
    assert tr._channel_label(["INPUT_TYPE_AUDIO"]) == "AUDIO"
    assert (
        tr._channel_label(["INPUT_TYPE_TEXT", "INPUT_TYPE_AUDIO"])
        == "MULTIMODAL"
    )
    assert tr._channel_label(["INPUT_TYPE_IMAGE"]) == "OTHER"


def test_input_type_name_object():
    obj = SimpleNamespace(name="INPUT_TYPE_TEXT")
    assert tr._input_type_name(obj) == "INPUT_TYPE_TEXT"
    assert tr._input_type_name("input_type_audio") == "INPUT_TYPE_AUDIO"


def test_to_iso_paths():
    assert tr._to_iso(None) is None
    assert tr._to_iso("already") == "already"
    assert tr._to_iso(datetime.datetime(2026, 5, 1)).startswith("2026-05-01")
    assert tr._to_iso(123) == "123"


def test_to_json_excludes_raw_by_default():
    n = tr.normalize(SAMPLE_DICT)
    out = json.loads(tr.to_json(n))
    assert "raw" not in out
    assert out["conversation_id"] == "c1"


def test_to_json_with_raw_and_extras():
    n = tr.normalize(SAMPLE_DICT)
    out = json.loads(tr.to_json(n, include_raw=True, extras={"foo": "bar"}))
    assert "raw" in out
    assert out["extras"] == {"foo": "bar"}


def test_to_text_includes_all_kinds():
    n = tr.normalize(SAMPLE_DICT)
    text = tr.to_text(n, extras={"x": 1})
    assert "USER:" in text
    assert "AGENT" in text
    assert "tool_call" in text
    assert "tool_response" in text
    assert "transfer" in text
    assert "custom_payload" in text
    assert "Extras" in text


def test_to_text_truncates_long_response():
    big = {"big": "x" * 500}
    n = tr.normalize(
        {
            "name": "n",
            "turns": [
                {
                    "messages": [
                        {
                            "role": "agent",
                            "chunks": [
                                {
                                    "tool_response": {
                                        "display_name": "t",
                                        "response": big,
                                    }
                                }
                            ],
                        }
                    ]
                }
            ],
        }
    )
    text = tr.to_text(n)
    assert "..." in text


def test_to_text_unknown_kind_branch():
    # Hit the fall-through `_entry_to_text` branch.
    assert tr._entry_to_text({"kind": "weird", "turn": 7}) == "[7]   weird"


def test_to_markdown_with_console_url_and_extras():
    n = tr.normalize(SAMPLE_DICT)
    md = tr.to_markdown(n, console_url="https://x/y", extras={"S": "body"})
    assert "Open in CES Console" in md
    assert "## S" in md


def test_to_markdown_with_dict_extras():
    n = tr.normalize(SAMPLE_DICT)
    md = tr.to_markdown(n, extras={"Stats": {"count": 5}})
    assert "```json" in md
    assert '"count": 5' in md


def test_to_markdown_unknown_kind_branch():
    assert tr._entry_to_markdown({"kind": "weird", "turn": 3}) == "- [3] weird"


def test_to_html_with_audio_and_extras_string_and_dict():
    n = tr.normalize(SAMPLE_DICT)
    html = tr.to_html(
        n,
        console_url="https://x/y",
        audio_path="audio.wav",
        extras={"AString": "raw text", "ADict": {"k": "v"}},
    )
    assert "<audio" in html
    assert "<title>" in html
    assert "AString" in html
    assert "ADict" in html
    assert "raw text" in html


def test_to_html_unknown_kind_branch():
    assert "weird" in tr._entry_to_html({"kind": "weird", "turn": 1})


def test_chunk_to_entry_returns_none_for_unknown_chunk():
    assert tr._chunk_to_entry({}, "user", 0) is None


def test_chunk_to_entry_user_via_text_role_lower():
    e = tr._chunk_to_entry({"text": "hi"}, "USER", 0)
    assert e["kind"] == "user"


def test_chunk_to_entry_transcript_user_role():
    e = tr._chunk_to_entry({"transcript": "spoken"}, "user", 0)
    assert e["kind"] == "user"
    assert e["text"] == "spoken"


def test_chunk_to_entry_agent_transfer_dict_value():
    e = tr._chunk_to_entry(
        {"agent_transfer": {"display_name": "agent_x"}}, "agent", 0
    )
    assert e["target"] == "agent_x"


def test_chunk_to_entry_variable_chunks():
    a = tr._chunk_to_entry({"default_variables": {"foo": "bar"}}, "user", 0)
    assert a["kind"] == "variable_default"
    assert a["variables"] == {"foo": "bar"}
    b = tr._chunk_to_entry({"updated_variables": {"x": 1}}, "agent_x", 1)
    assert b["kind"] == "variable_update"
    assert b["variables"] == {"x": 1}


SPAN_TURN = {
    "messages": [
        {"role": "user", "chunks": [{"text": "hi"}]},
        {
            "role": "agent",
            "chunks": [
                {"updated_variables": {"step": "greet"}},
                {"text": "hello"},
            ],
        },
    ],
    "root_span": {
        "name": "root",
        "start_time": "2026-05-01T00:00:00.000Z",
        "end_time": "2026-05-01T00:00:01.500Z",
        "duration": "1.500s",
        "attributes": {"perceived latency (ms)": 1450.0},
        "child_spans": [
            {
                "name": "Callback",
                "start_time": "2026-05-01T00:00:00.100Z",
                "end_time": "2026-05-01T00:00:00.200Z",
                "duration": "0.1s",
                "attributes": {"agent": "a", "stage": "BeforeAgent"},
                "child_spans": [],
            },
            {
                "name": "LLM",
                "start_time": "2026-05-01T00:00:00.200Z",
                "end_time": "2026-05-01T00:00:01.300Z",
                "duration": "1.1s",
                "attributes": {
                    "agent": "a",
                    "model": "gemini",
                    "input token count": 100,
                    "output token count": 25,
                    "thought token count": 0,
                    "time to first chunk (ms)": 500,
                },
                "child_spans": [],
            },
            {
                "name": "Tool",
                "start_time": "2026-05-01T00:00:01.300Z",
                "end_time": "2026-05-01T00:00:01.400Z",
                "duration": "0.1s",
                "attributes": {
                    "name": "lookup",
                    "args": {"q": "test"},
                    "response": {"r": 1},
                },
                "child_spans": [],
            },
        ],
    },
}


def test_normalize_with_spans_and_metrics():
    n = tr.normalize({"name": "p/c1", "turns": [SPAN_TURN]})
    assert n["num_turns"] == 1
    assert n["totals"]["tokens"]["input"] == 100
    assert n["totals"]["tokens"]["output"] == 25
    assert n["totals"]["tokens"]["total"] == 125
    tm = n["turn_metrics"][0]
    assert tm["perceived_latency_ms"] == 1450.0
    assert tm["duration_ms"] == 1500.0
    span_names = [s["name"] for s in tm["spans"]]
    assert span_names == ["Callback", "LLM", "Tool"]
    llm = tm["spans"][1]
    assert llm["tokens"]["ttfc_ms"] == 500
    tool = tm["spans"][2]
    assert tool["tool"] == "lookup"
    assert tool["tool_args"] == {"q": "test"}
    assert tool["tool_response"] == {"r": 1}


def test_to_text_includes_span_lines_and_variable_kinds():
    n = tr.normalize({"name": "p/c1", "turns": [SPAN_TURN]})
    out = tr.to_text(n)
    assert "-- Turn 0 --" in out
    assert "Callback[BeforeAgent]" in out
    assert "LLM gemini" in out
    assert "Tool lookup" in out
    assert "var_update" in out
    assert "tokens(in/out/think/total)=100/25/0/125" in out


def test_to_markdown_with_spans_and_totals():
    n = tr.normalize({"name": "p/c1", "turns": [SPAN_TURN]})
    md = tr.to_markdown(n)
    assert "Total span time" in md
    assert "Tokens (in / out / think / total)" in md
    assert "### Turn 0" in md
    assert "Execution spans" in md
    # Variable update entry rendered
    assert "Variable update" in md


def test_to_html_with_spans_and_totals():
    n = tr.normalize({"name": "p/c1", "turns": [SPAN_TURN]})
    html = tr.to_html(n)
    assert "total span time" in html
    assert "Execution spans" in html
    assert "Variable update" in html


def test_fmt_ms_paths():
    assert tr._fmt_ms(None) == "?"
    assert tr._fmt_ms(123.456) == "123.5ms"


def test_to_int_paths():
    assert tr._to_int(None) is None
    assert tr._to_int(3) == 3
    assert tr._to_int("4") == 4
    assert tr._to_int("not-int") is None


def test_to_int_or_float_paths():
    assert tr._to_int_or_float(None) is None
    assert tr._to_int_or_float(3) == 3
    assert tr._to_int_or_float(3.5) == 3.5
    assert tr._to_int_or_float("4") == 4
    assert tr._to_int_or_float("4.5") == 4.5
    assert tr._to_int_or_float("nope") is None


def test_duration_ms_paths():
    assert tr._duration_ms(None, None) is None
    assert tr._duration_ms(None, None, "1.25s") == 1250.0
    assert tr._duration_ms(None, None, "bad-s") is None
    assert (
        tr._duration_ms("2026-05-01T00:00:00", "2026-05-01T00:00:00.500")
        == 500.0
    )


def test_to_dt_paths():
    assert tr._to_dt(None) is None
    assert tr._to_dt("invalid") is None
    dt = datetime.datetime(2026, 5, 1)
    assert tr._to_dt(dt) is dt
    assert tr._to_dt(123) is None


def test_attr_handles_non_dict_attributes():
    assert tr._attr({"attributes": "not-a-dict"}, "k") is None
    assert tr._attr({"attributes": {"k": 4}}, "k") == 4


def test_span_renderers_unknown_name():
    s = {"name": "Other", "depth": 0, "duration_ms": 10.0}
    assert "Other" in tr._span_to_text(s)
    md = tr._span_to_markdown(s)
    assert "Other" in md
