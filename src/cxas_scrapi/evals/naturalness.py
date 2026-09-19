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


"""Optional Naturalness Metric for SimulationEvals.

Grades how human an agent sounds, per turn and in aggregate, using Gemini.

The metric is entirely opt-in. A simulation test case that does not declare a
``naturalness_metric`` block behaves exactly as it did before this module
existed: no extra Gemini calls are made and no extra keys appear in the
results. This keeps existing simulation YAML schemas both backward and
forward compatible.
"""

import enum
import logging
import os
import statistics
import typing
from typing import Any

import pydantic
from google import genai

from cxas_scrapi.prompts import naturalness_prompts

logger = logging.getLogger(__name__)

# Keys a test case may use to declare the metric. The plural/suffixed forms
# are accepted so hand-written YAML does not fail on a near-miss.
_CONFIG_KEYS = (
    "naturalness_metric",
    "naturalness",
    "naturalness_config",
)

_MAX_UTTERANCE_CHARS = 400

# The one dimension that is measured rather than judged: it is computed from
# the platform's reported perceived latency, never asked of the model.
LATENCY_QUALITY = "perceivedLatency"

_LATENCY_LABELS = {
    5: "excellent",
    4: "very good",
    3: "good",
    2: "needs improvement",
    1: "unacceptable",
}


class NaturalnessLabel(str, enum.Enum):
    """Graded label assigned to an agent turn and to the whole call."""

    BOT_LIKE = "Bot-like"
    TRANSITIONAL = "Transitional"
    HUMAN_LIKE = "Human-like"


class NaturalnessFactor(pydantic.BaseModel):
    """A single scored conversational quality, e.g. pacing or grammarStyle."""

    quality: str = ""
    score: int = 3
    value: str = ""
    reason: str = ""


class TurnNaturalness(pydantic.BaseModel):
    """Naturalness grading for one agent turn."""

    turn_index: int = 0
    agent_utterance: str = ""
    label: NaturalnessLabel = NaturalnessLabel.TRANSITIONAL
    score: float = 0.0
    justification: str = ""
    factors: list[NaturalnessFactor] = []


class NaturalnessOutput(pydantic.BaseModel):
    """Raw structured response returned by the grading model."""

    turns: list[TurnNaturalness] = []
    conversation_factors: list[NaturalnessFactor] = []
    summary: str = ""


class NaturalnessConfig(pydantic.BaseModel):
    """Per-test-case configuration for the Naturalness Metric.

    Unknown keys are preserved rather than rejected so that a YAML file
    written against a newer version of the library still loads here.
    """

    model_config = pydantic.ConfigDict(extra="allow")

    enabled: bool = True
    # Falls back to the simulation's eval_model when unset.
    model: str | None = None
    # Override the graded dimensions. Empty means "use the defaults".
    turn_qualities: list[str] = []
    conversation_qualities: list[str] = []
    # Free-text rubric addendum, e.g. brand voice or locale specifics.
    extra_guidance: str = ""
    # Blend between the per-turn mean and the conversation-level factors.
    turn_weight: float = 0.75
    # Score bands used to derive labels from numeric scores.
    bot_like_below: float = 2.5
    human_like_at_or_above: float = 3.75
    # When set, the simulation's pass/fail also requires this overall score.
    pass_threshold: float | None = None
    # Judge the agent's speech acoustically, not just its transcript. Enabled
    # automatically when the simulation runs in audio modality.
    use_audio: bool = False
    # Where agent audio comes from when `use_audio` is set:
    #   "auto"  - the recording bucket, falling back to local capture
    #   "gcs"   - the recording bucket only
    #   "local" - locally captured WAVs only (capture_agent_audio=True)
    #   "none"  - disable audio even if `use_audio` is set
    audio_source: str = "auto"
    # Upper bounds in ms for the perceivedLatency bands, best to worst. With
    # the defaults: <1s scores 5, <2s scores 4, <3s scores 3, <4s scores 2,
    # and anything slower scores 1.
    latency_bands_ms: list[int] = [1000, 2000, 3000, 4000]
    # How heavily perceivedLatency counts in a turn's mean relative to the
    # judged factors. 1.0 makes it one factor among many; 0 drops it from the
    # mean (it is still reported).
    latency_weight: float = 3.0
    # A turn at or above this perceived latency fails the simulation outright,
    # independently of `pass_threshold`. None disables the hard failure.
    latency_failure_threshold_ms: float | None = 4000.0
    # How long to keep polling for the conversation that latency is read
    # from. The platform publishes a conversation some time after the call
    # ends — usually under a minute — so a fetch straight after a simulation
    # always 404s. 0 disables the wait, and the dimension is simply omitted
    # when the conversation never arrives.
    latency_fetch_timeout_s: float = 120.0
    # Show tool calls in the transcript so "thinking out loud while looking
    # something up" can be judged in context.
    include_tool_calls: bool = True

    @pydantic.field_validator("turn_weight")
    @classmethod
    def _clamp_turn_weight(cls, v: float) -> float:
        return min(max(v, 0.0), 1.0)


class NaturalnessResult(pydantic.BaseModel):
    """Aggregated Naturalness Metric result for a whole simulation."""

    overall_score: float = 0.0
    overall_label: NaturalnessLabel = NaturalnessLabel.TRANSITIONAL
    turn_count: int = 0
    turns: list[TurnNaturalness] = []
    conversation_factors: list[NaturalnessFactor] = []
    # Mean score per quality across all graded turns.
    factor_averages: dict[str, float] = {}
    # How many turns landed in each label band.
    label_counts: dict[str, int] = {}
    summary: str = ""
    model: str = ""
    pass_threshold: float | None = None
    passed: bool | None = None
    # Perceived latency in ms per turn index, when the platform reported it.
    latency_ms_by_turn: dict[int, float] = {}
    # Populated when something fails the run outright rather than by score,
    # e.g. a turn past `latency_failure_threshold_ms`.
    failure_reasons: list[str] = []

    def model_dump(self, **kwargs: typing.Any) -> typing.Any:
        kwargs.setdefault("mode", "json")
        return super().model_dump(**kwargs)


class AgentTurn(pydantic.BaseModel):
    """One (user, agent) exchange extracted from a simulation trace."""

    turn_index: int
    # Position among turns that actually produced speech. Per-turn audio and
    # perceived latency are addressed by this, not by `turn_index`; see
    # `extract_agent_turns`.
    speech_index: int = 0
    user: str = ""
    agent: str = ""
    tool_calls: list[str] = []


def parse_naturalness_config(
    test_case: dict[str, Any] | None,
    override: Any = None,
) -> NaturalnessConfig | None:
    """Resolves the naturalness configuration for a test case.

    Args:
        test_case: The simulation test case dict. May omit the metric
            entirely, which is the common case.
        override: A run-level override. ``True`` enables the metric with
            defaults for every test case, ``False`` force-disables it, and a
            dict is merged over whatever the test case declared.

    Returns:
        A :class:`NaturalnessConfig` when the metric should run, otherwise
        ``None``. Malformed configuration is logged and treated as "off" so
        that a bad block can never fail an otherwise healthy simulation.
    """
    if override is False:
        return None

    raw: Any = None
    if test_case:
        for key in _CONFIG_KEYS:
            if key in test_case:
                raw = test_case[key]
                break

    if isinstance(override, dict):
        base = dict(raw) if isinstance(raw, dict) else {}
        base.update(override)
        raw = base
    elif override is True and raw is None:
        raw = True

    if raw is None:
        return None
    # `naturalness_metric: true` / `false` shorthand.
    if isinstance(raw, bool):
        return NaturalnessConfig() if raw else None
    if not isinstance(raw, dict):
        logger.warning(
            "Ignoring naturalness_metric: expected a mapping or bool, got %s.",
            type(raw).__name__,
        )
        return None

    try:
        config = NaturalnessConfig(**raw)
    except pydantic.ValidationError as exc:
        logger.warning("Ignoring malformed naturalness_metric config: %s", exc)
        return None

    return config if config.enabled else None


def _clean_agent_line(line: str) -> str:
    """Strips the trace prefix from an agent text line."""
    for prefix in ("Agent Text: ", "Agent: "):
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def extract_agent_turns(trace: list[str]) -> list[AgentTurn]:
    """Pairs user utterances with the agent response that followed.

    ``trace`` is the ``detailed_trace`` built by
    :meth:`SimulationEvals.simulate_conversation`: alternating ``"User: ..."``
    entries and multi-line agent blocks whose lines are prefixed with
    ``"Agent Text: "``, ``"Tool Call ..."`` and similar.

    Two numberings come out of this, and they are not interchangeable:

    ``turn_index`` counts every agent block, including tool-only turns and
    turns with neither text nor tool calls. Those blocks are dropped from the
    result, so the returned indices may have gaps. It is what the transcript
    shown to the grader is labelled with.

    ``speech_index`` counts only the turns that actually produced speech, so
    it is always contiguous. The platform numbers both its per-turn
    recordings (``agent-turn-N`` is ``speech_index`` ``N-1``) and its trace
    turn metrics this way, because a turn that said nothing has no audio and
    no perceived latency. Per-turn audio and latency must therefore be looked
    up by ``speech_index``; using ``turn_index`` silently shifts them onto the
    wrong turn from the first tool-only block onwards.
    """
    turns: list[AgentTurn] = []
    pending_user = ""
    index = 0

    for entry in trace or []:
        if not isinstance(entry, str):
            continue
        if entry.startswith("User: "):
            pending_user = entry[len("User: ") :].strip()
            continue

        agent_parts: list[str] = []
        tool_calls: list[str] = []
        for raw_line in entry.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            text = _clean_agent_line(line)
            if text:
                agent_parts.append(text)
            elif line.startswith("Tool Call"):
                tool_calls.append(line)

        turns.append(
            AgentTurn(
                turn_index=index,
                user=pending_user,
                agent=" ".join(agent_parts),
                tool_calls=tool_calls,
            )
        )
        pending_user = ""
        index += 1

    speaking = [t for t in turns if t.agent]
    for position, turn in enumerate(speaking):
        turn.speech_index = position
    return speaking


def _render_transcript(turns: list[AgentTurn], include_tool_calls: bool) -> str:
    """Formats turns for the grader, numbering each agent turn."""
    lines: list[str] = []
    for turn in turns:
        if turn.user:
            lines.append(f"[user] {turn.user}")
        if include_tool_calls and turn.tool_calls:
            for tool_call in turn.tool_calls:
                lines.append(f"    ({tool_call})")
        lines.append(f"[agent turn {turn.turn_index}] {turn.agent}")
        lines.append("")
    return "\n".join(lines).strip()


def _qualities_block(overrides: list[str], default_block: str) -> str:
    """Renders the quality list, honouring a caller override."""
    if not overrides:
        return default_block
    return "\n".join(f"-   `{q}`" for q in overrides)


def _build_prompt(turns: list[AgentTurn], config: NaturalnessConfig) -> str:
    """Assembles the grading prompt from the rubric and the transcript."""
    extra = config.extra_guidance.strip()
    if extra:
        extra = f"## Additional guidance for this agent\n\n{extra}"

    return (
        naturalness_prompts.NATURALNESS_METRIC_PROMPT.replace(
            "{rubric}", naturalness_prompts.NATURALNESS_RUBRIC
        )
        .replace(
            "{turn_qualities}",
            _qualities_block(
                config.turn_qualities,
                naturalness_prompts.DEFAULT_TURN_QUALITIES,
            ),
        )
        .replace(
            "{conversation_qualities}",
            _qualities_block(
                config.conversation_qualities,
                naturalness_prompts.DEFAULT_CONVERSATION_QUALITIES,
            ),
        )
        .replace("{extra_guidance}", extra)
        .replace(
            "{transcript}",
            _render_transcript(turns, config.include_tool_calls),
        )
    )


def _audio_part(ref: str) -> Any | None:
    """Builds a Gemini audio part from a `gs://` URI or a local WAV path.

    Bucket URIs are handed to Gemini by reference rather than downloaded.
    Returns ``None`` — after logging — if the audio cannot be read, so one
    unreadable turn degrades to text-only instead of failing the grading.
    """
    if ref.startswith("gs://"):
        return genai.types.Part.from_uri(file_uri=ref, mime_type="audio/wav")
    if not os.path.exists(ref):
        logger.warning("Naturalness: agent audio not found at %s.", ref)
        return None
    try:
        with open(ref, "rb") as handle:
            return genai.types.Part.from_bytes(
                data=handle.read(), mime_type="audio/wav"
            )
    except OSError as exc:
        logger.warning("Could not attach audio %s: %s", ref, exc)
        return None


def _build_audio_contents(
    prompt: str,
    turns: list[AgentTurn],
    audio_paths: dict[int, str],
) -> list[Any]:
    """Interleaves the agent's recorded audio after each agent turn.

    ``audio_paths`` maps `speech_index` (see `extract_agent_turns`) to either
    a `gs://` URI or a local path. Turns without usable audio are simply
    judged on their transcript; if no turn has usable audio the audio
    preamble is dropped entirely rather than sending the model an empty
    section.
    """
    contents: list[Any] = [
        prompt,
        naturalness_prompts.AUDIO_GRADING_GUIDANCE,
        "\nRAW AGENT AUDIO BY TURN:\n",
    ]
    attached = 0
    for turn in turns:
        ref = audio_paths.get(turn.speech_index)
        if not ref:
            continue
        part = _audio_part(ref)
        if part is None:
            continue
        contents.append(f"\n[audio for agent turn {turn.turn_index}]\n")
        contents.append(part)
        attached += 1

    if not attached:
        logger.info(
            "Naturalness: no agent audio could be attached; "
            "grading the transcript only."
        )
        return [prompt]
    logger.info("Naturalness: attached audio for %d turn(s).", attached)
    return contents


def _latency_factor(
    latency_ms: float, config: NaturalnessConfig
) -> NaturalnessFactor:
    """Scores one turn's perceived latency against the configured bands.

    This dimension is computed, not judged: no model call is involved.
    """
    bands = sorted(config.latency_bands_ms)
    score = max(1, 5 - len(bands))
    for position, upper in enumerate(bands):
        if latency_ms < upper:
            score = 5 - position
            break
    score = int(min(max(score, 1), 5))

    label = _LATENCY_LABELS.get(score, "")
    seconds = latency_ms / 1000.0
    if score >= 4:
        reason = f"{label}: responded in {seconds:.1f}s"
    else:
        reason = (
            f"{label}: {seconds:.1f}s is slow for a spoken reply "
            f"(target is under {bands[1] / 1000:.0f}s)"
            if len(bands) > 1
            else f"{label}: responded in {seconds:.1f}s"
        )

    return NaturalnessFactor(
        quality=LATENCY_QUALITY,
        score=score,
        value=f"{seconds:.1f}s",
        reason=reason,
    )


def _attach_latency_factors(
    graded: list[TurnNaturalness],
    latency_ms_by_turn: dict[int, float],
    config: NaturalnessConfig,
    speech_index_by_turn: dict[int, int] | None = None,
) -> None:
    """Adds the computed perceivedLatency factor to each graded turn.

    ``latency_ms_by_turn`` is keyed by `speech_index` (see
    `extract_agent_turns`), matching how the platform numbers its trace turn
    metrics, so `speech_index_by_turn` translates each graded turn's
    `turn_index` before the lookup.

    Any `perceivedLatency` the model invented is discarded first — this
    dimension is measured, so the model does not get a vote. Turns with no
    reported latency get no factor at all rather than a neutral score, so a
    missing measurement cannot quietly drag a turn up or down.
    """
    if not latency_ms_by_turn:
        return
    lookup = speech_index_by_turn or {}
    for turn in graded:
        turn.factors = [f for f in turn.factors if f.quality != LATENCY_QUALITY]
        key = lookup.get(turn.turn_index, turn.turn_index)
        latency_ms = latency_ms_by_turn.get(key)
        if latency_ms is None:
            continue
        turn.factors.append(_latency_factor(latency_ms, config))


def _latency_failures(
    latency_ms_by_turn: dict[int, float], config: NaturalnessConfig
) -> list[str]:
    """Returns hard-failure reasons for turns past the latency threshold.

    Deliberately checks every measured turn, not just the graded ones: a turn
    can be too slow to be acceptable whether or not it was worth grading.
    """
    threshold = config.latency_failure_threshold_ms
    if threshold is None or not latency_ms_by_turn:
        return []
    over = {
        index: ms for index, ms in latency_ms_by_turn.items() if ms >= threshold
    }
    if not over:
        return []
    worst_turn = max(over, key=lambda i: over[i])
    return [
        f"perceived latency reached {over[worst_turn] / 1000:.1f}s on turn "
        f"{worst_turn} (turns {sorted(over)}), at or above the "
        f"{threshold / 1000:.1f}s limit"
    ]


def _label_for_score(
    score: float, config: NaturalnessConfig
) -> NaturalnessLabel:
    """Maps a 1-5 score onto a graded label using the configured bands."""
    if score < config.bot_like_below:
        return NaturalnessLabel.BOT_LIKE
    if score >= config.human_like_at_or_above:
        return NaturalnessLabel.HUMAN_LIKE
    return NaturalnessLabel.TRANSITIONAL


def _clamp_score(value: float) -> float:
    return min(max(float(value), 1.0), 5.0)


def _weighted_factor_mean(
    factors: list[NaturalnessFactor], config: NaturalnessConfig
) -> float | None:
    """Means the factor scores, weighting perceivedLatency separately.

    A turn has ~10 judged factors, so an unweighted latency factor would move
    the turn by about 0.2 no matter how slow the agent was. `latency_weight`
    lets latency count for more than one factor; 0 excludes it from the mean
    while still reporting it.
    """
    total = 0.0
    weight_sum = 0.0
    for factor in factors:
        weight = (
            config.latency_weight if factor.quality == LATENCY_QUALITY else 1.0
        )
        if weight <= 0:
            continue
        total += factor.score * weight
        weight_sum += weight
    return total / weight_sum if weight_sum else None


def _normalise_turns(
    graded: list[TurnNaturalness],
    source: list[AgentTurn],
    config: NaturalnessConfig,
) -> list[TurnNaturalness]:
    """Repairs model output: clamps scores and re-derives labels.

    The turn score is recomputed as the weighted mean of the factor scores
    rather than taken from the model's separate holistic number. The factors
    are the evidence the model actually cited, so deriving from them keeps
    ``overall_score``, ``factor_averages`` and the labels mutually consistent
    and makes the metric reproducible.
    """
    by_index = {t.turn_index: t for t in source}
    normalised: list[TurnNaturalness] = []

    for turn in graded:
        for factor in turn.factors:
            factor.score = int(min(max(factor.score, 1), 5))

        weighted = _weighted_factor_mean(turn.factors, config)
        score = turn.score if weighted is None else weighted

        turn.score = round(_clamp_score(score), 2)
        turn.label = _label_for_score(turn.score, config)

        if not turn.agent_utterance:
            original = by_index.get(turn.turn_index)
            if original:
                turn.agent_utterance = original.agent[:_MAX_UTTERANCE_CHARS]

        normalised.append(turn)

    normalised.sort(key=lambda t: t.turn_index)
    return normalised


def _factor_averages(turns: list[TurnNaturalness]) -> dict[str, float]:
    """Means each quality across all graded turns."""
    buckets: dict[str, list[int]] = {}
    for turn in turns:
        for factor in turn.factors:
            if factor.quality:
                buckets.setdefault(factor.quality, []).append(factor.score)
    return {
        quality: round(statistics.fmean(scores), 2)
        for quality, scores in sorted(buckets.items())
    }


def _aggregate(
    turns: list[TurnNaturalness],
    conversation_factors: list[NaturalnessFactor],
    config: NaturalnessConfig,
) -> float:
    """Blends the per-turn mean with the conversation-level factor mean."""
    if not turns:
        return 0.0

    turn_mean = statistics.fmean(t.score for t in turns)
    conv_scores = [
        int(min(max(f.score, 1), 5)) for f in conversation_factors if f.quality
    ]
    if not conv_scores:
        return round(_clamp_score(turn_mean), 2)

    conv_mean = statistics.fmean(conv_scores)
    blended = (
        config.turn_weight * turn_mean + (1.0 - config.turn_weight) * conv_mean
    )
    return round(_clamp_score(blended), 2)


def evaluate_naturalness(
    gemini_client: Any,
    model_name: str,
    trace: list[str],
    config: NaturalnessConfig,
    audio_paths: dict[int, str] | None = None,
    latency_ms_by_turn: dict[int, float] | None = None,
) -> NaturalnessResult | None:
    """Grades the naturalness of every agent turn in a simulation trace.

    Args:
        gemini_client: A ``GeminiGenerate`` instance.
        model_name: Fallback model, used when the config does not pin one.
        trace: The simulation's ``detailed_trace``.
        config: Resolved metric configuration.
        audio_paths: Optional map of turn index to the agent's recorded audio,
            as either a `gs://` URI or a local WAV path. Used only when
            ``config.use_audio`` is set.
        latency_ms_by_turn: Optional map of turn index to the platform's
            reported perceived latency in ms. When supplied, each turn gains a
            computed ``perceivedLatency`` factor.

    Returns:
        A :class:`NaturalnessResult`, or ``None`` when there was nothing to
        grade or the grading call failed. Failures are logged rather than
        raised so the metric can never break a simulation run.
    """
    turns = extract_agent_turns(trace)
    if not turns:
        logger.info("Naturalness metric skipped: no agent turns in trace.")
        return None

    target_model = config.model or model_name
    prompt: Any = _build_prompt(turns, config)

    if config.use_audio and audio_paths:
        prompt = _build_audio_contents(prompt, turns, audio_paths)

    try:
        output: NaturalnessOutput | None = gemini_client.generate(
            prompt=prompt,
            model_name=target_model,
            response_mime_type="application/json",
            response_schema=NaturalnessOutput,
        )
    except Exception as exc:  # noqa: BLE001 - metric must never break a run
        logger.error("Naturalness grading failed: %s", exc)
        return None

    if not output or not output.turns:
        logger.warning("Naturalness grading returned no turn gradings.")
        return None

    latencies = latency_ms_by_turn or {}
    # Injected before scoring so the weighted turn mean includes latency.
    _attach_latency_factors(
        output.turns,
        latencies,
        config,
        speech_index_by_turn={t.turn_index: t.speech_index for t in turns},
    )

    graded_turns = _normalise_turns(output.turns, turns, config)
    for factor in output.conversation_factors:
        factor.score = int(min(max(factor.score, 1), 5))

    overall = _aggregate(graded_turns, output.conversation_factors, config)
    label_counts: dict[str, int] = {
        label.value: 0 for label in NaturalnessLabel
    }
    for turn in graded_turns:
        label_counts[turn.label.value] += 1

    passed = None
    if config.pass_threshold is not None:
        passed = overall >= config.pass_threshold

    # A turn slow enough to break the conversation fails the run regardless of
    # how well it scored on everything else.
    failure_reasons = _latency_failures(latencies, config)
    if failure_reasons:
        passed = False

    return NaturalnessResult(
        overall_score=overall,
        overall_label=_label_for_score(overall, config),
        turn_count=len(graded_turns),
        turns=graded_turns,
        conversation_factors=output.conversation_factors,
        factor_averages=_factor_averages(graded_turns),
        label_counts=label_counts,
        summary=output.summary,
        model=target_model,
        pass_threshold=config.pass_threshold,
        passed=passed,
        latency_ms_by_turn=latencies,
        failure_reasons=failure_reasons,
    )
