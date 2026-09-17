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


"""Unit tests for the optional Naturalness Metric."""

import json
import typing

import pytest

from cxas_scrapi.evals.naturalness import (
    LATENCY_QUALITY,
    NaturalnessConfig,
    NaturalnessFactor,
    NaturalnessLabel,
    NaturalnessOutput,
    TurnNaturalness,
    _attach_latency_factors,
    _audio_part,
    _build_audio_contents,
    _latency_factor,
    evaluate_naturalness,
    extract_agent_turns,
    parse_naturalness_config,
)

TRACE = [
    "User: event: welcome",
    "Agent Text: Thank you for calling. How may I assist you today?",
    "User: I was charged twice and I am furious",
    (
        "Tool Call (Output): lookup_account with args {'id': 1}\n"
        "Agent Text: [warm] Oh no... I'm really sorry about that.\n"
        "Agent Text: [short pause] Let me pull it up, one sec."
    ),
    "User: fine. account is one two three",
    "Agent Text: Got it. Give me just a moment...",
]


class _FakeClient:
    """Minimal stand-in for GeminiGenerate that returns a canned output."""

    def __init__(self, output: typing.Any) -> None:
        self.output = output
        self.last_prompt: typing.Any = None
        self.call_count = 0

    def generate(
        self, prompt: typing.Any = None, **_: typing.Any
    ) -> typing.Any:
        self.last_prompt = prompt
        self.call_count += 1
        return self.output


def _turn(idx: int, scores: list[int]) -> TurnNaturalness:
    qualities = ["emotion", "pacing", "grammarStyle"]
    return TurnNaturalness(
        turn_index=idx,
        agent_utterance=f"utterance {idx}",
        # A deliberately over-generous holistic score: the factor mean is
        # what the metric must actually use.
        label=NaturalnessLabel.HUMAN_LIKE,
        score=5.0,
        justification="j",
        factors=[
            NaturalnessFactor(quality=q, score=s, reason="r")
            for q, s in zip(qualities, scores, strict=True)
        ],
    )


def _output() -> NaturalnessOutput:
    return NaturalnessOutput(
        turns=[_turn(0, [1, 1, 1]), _turn(1, [4, 4, 4]), _turn(2, [5, 5, 5])],
        conversation_factors=[
            NaturalnessFactor(quality="personaConsistency", score=3, reason="r")
        ],
        summary="summary text",
    )


def test_extract_agent_turns_pairs_user_and_agent() -> None:
    turns = extract_agent_turns(TRACE)

    assert len(turns) == 3
    assert turns[0].turn_index == 0
    assert turns[0].user == "event: welcome"
    assert turns[1].user == "I was charged twice and I am furious"
    assert turns[2].agent == "Got it. Give me just a moment..."


def test_extract_agent_turns_preserves_emotive_tags_and_tool_calls() -> None:
    turns = extract_agent_turns(TRACE)

    assert "[warm]" in turns[1].agent
    assert "[short pause]" in turns[1].agent
    assert turns[1].tool_calls == [
        "Tool Call (Output): lookup_account with args {'id': 1}"
    ]


def test_extract_agent_turns_ignores_agent_only_tool_noise() -> None:
    assert extract_agent_turns([]) == []
    assert extract_agent_turns(["User: hello"]) == []


# A tool-only turn speaks no words, so the platform writes no recording and
# no perceived latency for it. That makes the agent-block counter
# (`turn_index`) and the recording/metric counter (`speech_index`) diverge
# from that point on.
GAPPED_TRACE = [
    "User: hi",
    "Agent Text: Hello there.",
    "User: look it up",
    "Tool Call (Output): lookup_account with args {'id': 1}",
    "User: and?",
    "Agent Text: Found it.",
]


def test_extract_agent_turns_numbers_speech_separately_from_blocks() -> None:
    turns = extract_agent_turns(GAPPED_TRACE)

    assert len(turns) == 2
    # The tool-only block consumed index 1, so the indices have a gap...
    assert [t.turn_index for t in turns] == [0, 2]
    # ...but speech numbering stays contiguous, matching agent-turn-1/2.
    assert [t.speech_index for t in turns] == [0, 1]


def test_attach_latency_factors_uses_speech_index_across_a_gap() -> None:
    turns = extract_agent_turns(GAPPED_TRACE)
    graded = [
        TurnNaturalness(turn_index=0, factors=[]),
        TurnNaturalness(turn_index=2, factors=[]),
    ]
    # Trace metrics are numbered over speaking turns: 0 and 1, not 0 and 2.
    _attach_latency_factors(
        graded,
        {0: 500.0, 1: 3500.0},
        NaturalnessConfig(),
        speech_index_by_turn={t.turn_index: t.speech_index for t in turns},
    )

    first = [f for f in graded[0].factors if f.quality == LATENCY_QUALITY]
    second = [f for f in graded[1].factors if f.quality == LATENCY_QUALITY]
    assert [f.score for f in first] == [5]
    # Would silently be dropped if the lookup used turn_index (no key 2).
    assert [f.score for f in second] == [2]


def test_build_audio_contents_uses_speech_index_across_a_gap() -> None:
    turns = extract_agent_turns(GAPPED_TRACE)
    contents = _build_audio_contents(
        "PROMPT",
        turns,
        {0: "gs://b/agent-turn-1.wav", 1: "gs://b/agent-turn-2.wav"},
    )
    rendered = [c for c in contents if isinstance(c, str)]

    # Both speaking turns get audio; the second is agent-turn-2 even though
    # its turn_index is 2.
    assert any("[audio for agent turn 0]" in c for c in rendered)
    assert any("[audio for agent turn 2]" in c for c in rendered)


@pytest.mark.parametrize(
    "test_case",
    [
        None,
        {"name": "x"},
        {"naturalness_metric": False},
        {"naturalness_metric": {"enabled": False}},
    ],
)
def test_parse_naturalness_config_disabled(
    test_case: dict[str, typing.Any] | None,
) -> None:
    """A test case without (or opting out of) the metric leaves it off."""
    assert parse_naturalness_config(test_case) is None


@pytest.mark.parametrize(
    "key", ["naturalness_metric", "naturalness", "naturalness_config"]
)
def test_parse_naturalness_config_shorthand_and_aliases(key: str) -> None:
    config = parse_naturalness_config({key: True})

    assert config is not None
    assert config.enabled is True
    assert config.turn_weight == 0.75
    assert config.pass_threshold is None


def test_parse_naturalness_config_tolerates_unknown_keys() -> None:
    """Forward compatible: a newer YAML must still load on an older lib."""
    config = parse_naturalness_config(
        {"naturalness_metric": {"some_future_key": 42, "pass_threshold": 3.0}}
    )

    assert config is not None
    assert config.pass_threshold == 3.0


@pytest.mark.parametrize(
    "raw", [{"turn_weight": "not-a-number"}, ["bad"], "bad"]
)
def test_parse_naturalness_config_malformed_is_off_not_fatal(
    raw: typing.Any,
) -> None:
    assert parse_naturalness_config({"naturalness_metric": raw}) is None


def test_parse_naturalness_config_clamps_turn_weight() -> None:
    config = parse_naturalness_config(
        {"naturalness_metric": {"turn_weight": 9}}
    )

    assert config is not None
    assert config.turn_weight == 1.0


def test_parse_naturalness_config_run_level_overrides() -> None:
    # True enables the metric for a test case that declared nothing.
    assert parse_naturalness_config({"name": "x"}, True) is not None
    # False force-disables it everywhere.
    assert parse_naturalness_config({"naturalness_metric": True}, False) is None
    # A dict is merged OVER whatever the test case declared.
    merged = parse_naturalness_config(
        {"naturalness_metric": {"pass_threshold": 3.0}},
        {"model": "gemini-3.1-pro-preview"},
    )
    assert merged is not None
    assert merged.pass_threshold == 3.0
    assert merged.model == "gemini-3.1-pro-preview"


def test_evaluate_naturalness_scores_turns_from_factor_means() -> None:
    client = _FakeClient(_output())

    result = evaluate_naturalness(
        client, "fake-model", TRACE, NaturalnessConfig()
    )

    assert result is not None
    # The grader claimed a holistic 5.0 on every turn; the factors it
    # actually cited must win.
    assert result.turns[0].score == 1.0
    assert result.turns[0].label is NaturalnessLabel.BOT_LIKE
    assert result.turns[1].score == 4.0
    assert result.turns[1].label is NaturalnessLabel.HUMAN_LIKE
    assert result.turns[2].score == 5.0
    assert result.turns[2].label is NaturalnessLabel.HUMAN_LIKE


def test_evaluate_naturalness_blends_turn_and_conversation_scores() -> None:
    result = evaluate_naturalness(
        _FakeClient(_output()), "fake-model", TRACE, NaturalnessConfig()
    )

    assert result is not None
    # turn mean = 3.3333, conversation mean = 3.0
    # -> 0.75 * 3.3333 + 0.25 * 3.0 = 3.25
    assert result.overall_score == 3.25
    assert result.overall_label is NaturalnessLabel.TRANSITIONAL
    assert result.turn_count == 3
    assert result.summary == "summary text"
    assert result.model == "fake-model"
    assert result.factor_averages == {
        "emotion": 3.33,
        "grammarStyle": 3.33,
        "pacing": 3.33,
    }
    assert result.label_counts == {
        "Bot-like": 1,
        "Transitional": 0,
        "Human-like": 2,
    }


def test_evaluate_naturalness_uses_turn_mean_without_conversation_factors() -> (
    None
):
    output = _output()
    output.conversation_factors = []

    result = evaluate_naturalness(
        _FakeClient(output), "fake-model", TRACE, NaturalnessConfig()
    )

    assert result is not None
    assert result.overall_score == 3.33


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [(3.0, True), (4.5, False), (None, None)],
)
def test_evaluate_naturalness_pass_threshold(
    threshold: float | None, expected: bool | None
) -> None:
    result = evaluate_naturalness(
        _FakeClient(_output()),
        "fake-model",
        TRACE,
        NaturalnessConfig(pass_threshold=threshold),
    )

    assert result is not None
    assert result.passed is expected


def test_evaluate_naturalness_prompt_includes_turns_and_rubric() -> None:
    client = _FakeClient(_output())

    evaluate_naturalness(client, "fake-model", TRACE, NaturalnessConfig())

    assert "[agent turn 1]" in client.last_prompt
    assert "Bot-like" in client.last_prompt
    assert "grammarStyle" in client.last_prompt
    # include_tool_calls defaults to True.
    assert "lookup_account" in client.last_prompt


def test_evaluate_naturalness_pins_configured_model() -> None:
    config = NaturalnessConfig(model="gemini-3.1-pro-preview")

    result = evaluate_naturalness(
        _FakeClient(_output()), "fallback-model", TRACE, config
    )

    assert result is not None
    assert result.model == "gemini-3.1-pro-preview"


def test_evaluate_naturalness_result_is_json_serialisable() -> None:
    result = evaluate_naturalness(
        _FakeClient(_output()), "fake-model", TRACE, NaturalnessConfig()
    )

    assert result is not None
    # Must survive the round trip into sim_results.json.
    dumped = json.loads(json.dumps(result.model_dump()))
    assert dumped["overall_label"] == "Transitional"


def test_evaluate_naturalness_empty_trace_returns_none() -> None:
    client = _FakeClient(_output())

    assert evaluate_naturalness(client, "m", [], NaturalnessConfig()) is None
    assert client.call_count == 0


def test_evaluate_naturalness_empty_model_output_returns_none() -> None:
    assert (
        evaluate_naturalness(_FakeClient(None), "m", TRACE, NaturalnessConfig())
        is None
    )
    assert (
        evaluate_naturalness(
            _FakeClient(NaturalnessOutput()), "m", TRACE, NaturalnessConfig()
        )
        is None
    )


def test_evaluate_naturalness_grading_error_returns_none() -> None:
    """A quota error or similar must never fail the simulation."""

    class _Boom:
        def generate(self, **_: typing.Any) -> typing.Any:
            raise RuntimeError("quota exceeded")

    assert (
        evaluate_naturalness(_Boom(), "m", TRACE, NaturalnessConfig()) is None
    )


@pytest.mark.parametrize(
    ("latency_ms", "expected_score"),
    [
        (0, 5),
        (999, 5),
        (1000, 4),
        (1999, 4),
        (2000, 3),
        (2999, 3),
        (3000, 2),
        (3999, 2),
        (4000, 1),
        (12000, 1),
    ],
)
def test_latency_factor_bands(latency_ms: int, expected_score: int) -> None:
    """Band edges are inclusive at the bottom: exactly 2.0s is 'good', not 4."""
    factor = _latency_factor(float(latency_ms), NaturalnessConfig())

    assert factor.quality == LATENCY_QUALITY
    assert factor.score == expected_score
    assert factor.value == f"{latency_ms / 1000:.1f}s"


def test_latency_factor_honours_custom_bands() -> None:
    config = NaturalnessConfig(latency_bands_ms=[500, 800])

    assert _latency_factor(400, config).score == 5
    assert _latency_factor(600, config).score == 4
    # Past the last band the score floors at 5 - len(bands).
    assert _latency_factor(900, config).score == 3


def _latency_result(
    latency_ms_by_turn: dict[int, float], **config_kwargs: typing.Any
) -> typing.Any:
    return evaluate_naturalness(
        _FakeClient(_output()),
        "fake-model",
        TRACE,
        NaturalnessConfig(**config_kwargs),
        latency_ms_by_turn=latency_ms_by_turn,
    )


def test_latency_is_weighted_above_the_judged_factors() -> None:
    """A slow turn must move the score more than one factor in ten would."""
    result = _latency_result({1: 5000.0}, latency_weight=3.0)

    assert result is not None
    turn = next(t for t in result.turns if t.turn_index == 1)
    # Three judged factors at 4, plus latency at 1 counted three times:
    # (4*3 + 1*3) / 6 == 2.5, against 3.25 if latency were one vote.
    assert turn.score == 2.5
    assert result.latency_ms_by_turn == {1: 5000.0}


def test_zero_latency_weight_reports_without_scoring() -> None:
    """Weight 0 is the escape hatch: measure it, show it, do not grade on it."""
    result = _latency_result({1: 5000.0}, latency_weight=0.0)

    assert result is not None
    turn = next(t for t in result.turns if t.turn_index == 1)
    assert turn.score == 4.0
    assert any(f.quality == LATENCY_QUALITY for f in turn.factors)


def test_turns_without_measured_latency_get_no_latency_factor() -> None:
    """A missing measurement must not become a neutral 3 that skews the mean."""
    result = _latency_result({1: 500.0})

    assert result is not None
    scored = {
        t.turn_index: [f.quality for f in t.factors] for t in result.turns
    }
    assert LATENCY_QUALITY in scored[1]
    assert LATENCY_QUALITY not in scored[0]
    assert LATENCY_QUALITY not in scored[2]


def test_model_invented_latency_factor_is_discarded() -> None:
    """Latency is measured, so the grader does not get a vote on it."""
    output = _output()
    output.turns[1].factors.append(
        NaturalnessFactor(
            quality=LATENCY_QUALITY, score=5, reason="felt snappy"
        )
    )

    result = evaluate_naturalness(
        _FakeClient(output),
        "fake-model",
        TRACE,
        NaturalnessConfig(),
        latency_ms_by_turn={1: 5000.0},
    )

    assert result is not None
    turn = next(t for t in result.turns if t.turn_index == 1)
    latency_factors = [f for f in turn.factors if f.quality == LATENCY_QUALITY]
    assert len(latency_factors) == 1
    assert latency_factors[0].score == 1


def test_latency_past_the_limit_fails_the_run_outright() -> None:
    """No pass_threshold is set, so only the hard limit can fail this."""
    result = _latency_result({2: 4200.0})

    assert result is not None
    assert result.passed is False
    assert len(result.failure_reasons) == 1
    assert "4.2s" in result.failure_reasons[0]
    assert "turn 2" in result.failure_reasons[0]


def test_latency_hard_failure_overrides_a_passing_threshold() -> None:
    result = _latency_result({2: 9000.0}, pass_threshold=1.0)

    assert result is not None
    assert result.overall_score >= 1.0
    assert result.passed is False


def test_latency_hard_failure_can_be_disabled() -> None:
    result = _latency_result({2: 9000.0}, latency_failure_threshold_ms=None)

    assert result is not None
    assert result.failure_reasons == []
    assert result.passed is None


def test_latency_below_the_limit_does_not_fail_the_run() -> None:
    result = _latency_result({0: 3900.0, 1: 1000.0})

    assert result is not None
    assert result.failure_reasons == []
    assert result.passed is None


def test_audio_part_passes_bucket_uris_by_reference() -> None:
    """gs:// audio is handed to Gemini as a URI rather than downloaded."""
    part = _audio_part("gs://bucket/evaluations/x/agent-turn-1.wav")

    assert part is not None
    assert (
        part.file_data.file_uri == "gs://bucket/evaluations/x/agent-turn-1.wav"
    )


def test_audio_part_inlines_local_files(tmp_path: typing.Any) -> None:
    wav = tmp_path / "agent-turn-1.wav"
    wav.write_bytes(b"RIFF....WAVE")

    part = _audio_part(str(wav))

    assert part is not None
    assert part.inline_data.data == b"RIFF....WAVE"


def test_audio_part_missing_file_degrades_to_none() -> None:
    assert _audio_part("/nonexistent/agent-turn-9.wav") is None


def test_audio_contents_attach_recordings_and_guidance() -> None:
    client = _FakeClient(_output())

    evaluate_naturalness(
        client,
        "fake-model",
        TRACE,
        NaturalnessConfig(use_audio=True),
        audio_paths={1: "gs://bucket/dir/agent-turn-2.wav"},
    )

    assert isinstance(client.last_prompt, list)
    text = "".join(p for p in client.last_prompt if isinstance(p, str))
    assert "You can hear this call" in text
    assert "[audio for agent turn 1]" in text
    assert any(not isinstance(p, str) for p in client.last_prompt)


def test_audio_is_ignored_when_use_audio_is_off() -> None:
    client = _FakeClient(_output())

    evaluate_naturalness(
        client,
        "fake-model",
        TRACE,
        NaturalnessConfig(use_audio=False),
        audio_paths={1: "gs://bucket/dir/agent-turn-2.wav"},
    )

    assert isinstance(client.last_prompt, str)


def test_unreadable_audio_falls_back_to_the_transcript() -> None:
    """One bad path must degrade to text-only, not break the grading."""
    client = _FakeClient(_output())

    result = evaluate_naturalness(
        client,
        "fake-model",
        TRACE,
        NaturalnessConfig(use_audio=True),
        audio_paths={1: "/nonexistent/agent-turn-2.wav"},
    )

    assert result is not None
    # The prompt still goes out, just with no audio parts attached to it.
    assert all(isinstance(p, str) for p in client.last_prompt)
