---
title: Local Simulations
description: AI-driven open-ended conversation tests using SimulationEvals.
---

# Local Simulations

Local Simulations take a different approach to testing than Platform Goldens. Instead of scripting exact conversations and expected responses, you describe a *goal* and let an AI-powered user simulator (Gemini) try to achieve it. At the end, Gemini judges whether the agent met the goal and any additional expectations you specified.

This is valuable when you want to test that an agent can *complete a task*, without caring about the exact phrasing of each response — which is especially important for voice agents where natural language variation is expected.

---

## How simulations work

1. SCRAPI starts a real session with your agent using the Sessions API
2. Gemini plays the role of a human user, sending messages to the agent to try to achieve the goal
3. The conversation continues until the goal is met, the max number of turns is reached, or the agent ends the session
4. Gemini evaluates whether each step's `success_criteria` was met and whether any `expectations` were satisfied
5. SCRAPI produces a report with pass/fail status for each step and expectation

Because the user is simulated by a language model, the conversation is non-deterministic — each run may produce slightly different messages. This mirrors how real users behave.

---

## YAML format

Simulation files use the `evals:` key at the top level:

```yaml
evals:
  - name: "successful_order_lookup"
    tags: ["P0", "order_management"]
    session_parameters:
      order_12345_status: "shipped"
      order_12345_eta: "2026-04-18"
    steps:
      - goal: "Ask about the status of order ORD-12345"
        success_criteria: "The user has provided order ID ORD-12345 and the agent has acknowledged it"
        response_guide: "The user is a customer checking on a recent purchase. They are polite but want a quick answer."
        max_turns: 3

      - goal: "Get the order status and delivery date"
        success_criteria: "The agent has provided the shipping status and the estimated delivery date"
        max_turns: 2

    expectations:
      - "The agent correctly identified the order as shipped"
      - "The agent mentioned the estimated delivery date"
      - "The agent maintained a friendly, helpful tone throughout"
```

### Top-level fields

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Unique name for this evaluation |
| `tags` | list | Tags for filtering (e.g., `["P0", "smoke"]`) |
| `session_parameters` | dict | Variables injected at session start |
| `steps` | list | Ordered sequence of conversational goals |
| `expectations` | list | Post-conversation quality assertions evaluated by Gemini |

### Step fields

| Field | Type | Description |
|-------|------|-------------|
| `goal` | string | What the simulated user is trying to accomplish in this step |
| `success_criteria` | string | The condition that determines whether this step is complete |
| `response_guide` | string | Persona and context hints for the simulated user |
| `max_turns` | int | Maximum turns allowed before declaring the step incomplete |
| `static_utterance` | string | Instead of AI simulation, send this exact text (useful for testing specific inputs) |
| `inject_variables` | dict | Variables to inject for the first step only (overrides session_parameters) |

### Expectations

Expectations are evaluated by Gemini *after the full conversation completes*, looking at the entire transcript. They're natural language assertions:

```yaml
expectations:
  - "The agent never made up information that wasn't in the tool response"
  - "The agent asked for the order ID before looking it up"
  - "The agent offered to help with anything else before ending"
```

Each expectation is judged as Met or Not Met, with a justification from Gemini.

---

## The `SimulationEvals` class

For programmatic use, import `SimulationEvals`:

```python
from cxas_scrapi.evals.simulation_evals import SimulationEvals

sim_evals = SimulationEvals(
    app_name="projects/my-project/locations/us/apps/my-app",
)
```

### Running a single evaluation programmatically

The `simulate_conversation` method takes a `test_case` dict defining the steps and expectations:

```python
from cxas_scrapi.evals.simulation_evals import SimulationEvals

sim_evals = SimulationEvals(app_name="projects/my-project/locations/us/apps/my-app")

test_case = {
    "steps": [
        {
            "goal": "Ask about order ORD-12345",
            "success_criteria": "User provided order ID and agent acknowledged",
            "max_turns": 3,
        },
        {
            "goal": "Get delivery date",
            "success_criteria": "Agent provided estimated delivery date",
            "max_turns": 2,
        },
    ],
    "expectations": [
        "Agent maintained professional tone",
        "Agent never hallucinated data",
    ],
}

eval_conv = sim_evals.simulate_conversation(test_case=test_case)
report = eval_conv.generate_report()

# Goals report (one row per step)
print(report.goals_df)

# Expectations report (one row per expectation)
if report.expectations_df is not None:
    print(report.expectations_df)
```

### Running in parallel

Simulations can be slow because they involve multiple real API calls. Run them in parallel to speed things up:

```python
import concurrent.futures

test_cases = [...]  # list of test_case dicts

def run_single(tc):
    eval_conv = sim_evals.simulate_conversation(test_case=tc, console_logging=False)
    return eval_conv.generate_report()

with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(run_single, tc) for tc in test_cases]
    reports = [f.result() for f in concurrent.futures.as_completed(futures)]
```

!!! tip "Parallel execution and rate limits"
    The Sessions API and Gemini both have rate limits. Start with `max_workers=3` and increase if you're not hitting errors. The skills system's Run skill handles this automatically.

---

## Audio modality

If your agent handles voice conversations, you can run simulations in audio mode:

```python
sim_evals = SimulationEvals(app_name="projects/my-project/locations/us/apps/my-app")

eval_conv = sim_evals.simulate_conversation(
    test_case=test_case,
    modality="audio",  # default is "text"
    # voice_config is optional (defaults to US English voice)
    voice_config={
        "language_code": "en-US",
        "voice_name": "en-US-Standard-A"
    }
)
```

In audio mode, SCRAPI uses the Sessions API's audio streaming endpoint. The simulated user's messages are still text internally, but they're processed by the agent's audio pipeline, which exercises TTS/STT and any audio-specific callbacks.

---

## Naturalness Metric

Goals and expectations judge *what* the agent did. The **Naturalness Metric** judges *how it said it*: Gemini grades every agent turn on how closely it resembles a warm, competent human contact-centre agent rather than a script-reading bot.

Use it for voice and GECX / Gemini Composite agents, where emotive expression, pacing, disfluency, emotive tags (`[warm]`, `[short pause]`, `[sigh]`), and spoken-form numbers matter as much as task completion. A turn can complete every goal and still sound robotic — this metric is what catches that.

> [!NOTE]
> The metric is entirely **opt-in**. A test case that does not declare a `naturalness_metric` key behaves exactly as it did before: no extra Gemini call is made, and no extra keys appear in the results. Existing simulation YAML needs no changes, and a YAML file written against a newer version still loads (unknown keys inside the block are tolerated, not rejected).

### Minimal example

The shorthand `true` enables the metric with all defaults:

```yaml
evals:
  - name: upset_caller_billing_dispute
    tags: [P0, voice, naturalness]
    naturalness_metric: true
    steps:
      - goal: Complain about an incorrect charge and get it refunded
        success_criteria: Agent apologizes, looks up the account, and issues a refund
        max_turns: 10
```

Use `naturalness_metric: false` to explicitly disable it. The keys `naturalness` and `naturalness_config` are accepted as aliases for `naturalness_metric`.

### Fully-configured example

```yaml
evals:
  - name: upset_caller_billing_dispute
    tags: [P0, voice, naturalness]
    naturalness_metric:
      enabled: true
      model: "gemini-3.1-pro-preview"
      turn_qualities: [emotion, pacing, grammarStyle, disfluency, spokenNumbers]
      conversation_qualities: [personaConsistency, emotionalArcTracking]
      extra_guidance: >
        This is a US English voice agent. Currency must be spoken as
        "a hundred and fifty dollars", never "USD 150". Keep the calm
        register for the whole call once the caller has been upset.
      turn_weight: 0.8
      bot_like_below: 2.5
      human_like_at_or_above: 4.0
      pass_threshold: 3.5
      use_audio: true
      audio_source: auto
      latency_bands_ms: [1000, 2000, 3000, 4000]
      latency_weight: 3.0
      latency_failure_threshold_ms: 4000
      include_tool_calls: true
    steps:
      - goal: Complain about an incorrect charge and get it refunded
        success_criteria: Agent apologizes, looks up the account, and issues a refund
        max_turns: 10
```

### Configuration fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `true` | Set to `false` to keep the block in the file but skip grading |
| `model` | string | *(unset)* | Grading model. Falls back to the simulation's `eval_model` when unset |
| `turn_qualities` | list | `[]` | Overrides the graded turn-level dimensions. Empty means "use the defaults" |
| `conversation_qualities` | list | `[]` | Overrides the graded conversation-level dimensions. Empty means "use the defaults" |
| `extra_guidance` | string | `""` | Free-text rubric addendum appended to the prompt (brand voice, locale specifics) |
| `turn_weight` | float | `0.75` | Blend between the per-turn mean and the conversation-level mean. Clamped to 0.0–1.0 |
| `bot_like_below` | float | `2.5` | Scores below this are labelled `Bot-like` |
| `human_like_at_or_above` | float | `3.75` | Scores at or above this are labelled `Human-like` |
| `pass_threshold` | float | *(unset)* | When set, the simulation only passes if `overall_score` reaches this value |
| `use_audio` | bool | `false` | Judge the agent's speech acoustically rather than from its transcript. Enabled automatically when the simulation runs in `audio` modality |
| `audio_source` | string | `"auto"` | Where the agent audio comes from: `auto` (recording bucket, falling back to local capture), `gcs`, `local`, or `none` |
| `latency_bands_ms` | list | `[1000, 2000, 3000, 4000]` | Upper bounds in ms for the `perceivedLatency` score bands, best to worst |
| `latency_weight` | float | `3.0` | How heavily `perceivedLatency` counts in a turn's mean relative to a judged factor. `0` reports it without scoring it |
| `latency_failure_threshold_ms` | float | `4000` | A turn at or above this perceived latency fails the simulation outright. `null` disables the hard failure |
| `latency_fetch_timeout_s` | float | `120.0` | How long to keep polling for the conversation that latency is read from. `0` disables the wait |
| `include_tool_calls` | bool | `true` | Show tool calls in the graded transcript, so "thinking out loud while looking something up" is judged in context |

!!! tip "Malformed blocks never fail a run"
    A `naturalness_metric` block that isn't a mapping or bool, or that fails validation, is logged as a warning and treated as "off". The same is true of grading failures (quota errors, unparseable JSON, a trace with no agent turns): they log and return no result rather than failing the simulation.

### Default turn-level qualities

Every agent turn is scored 1–5 on each of these:

| Quality | Measures |
|---------|----------|
| `emotion` | Reads the user's emotional state and responds with the appropriate complementary register, with matched emotive tags |
| `pacing` | Conversational rhythm: pauses, `[slow]`, ellipses, digit chunking — and no tag over-saturation |
| `grammarStyle` | Natural imperfection: contractions, fragments, varied sentence length rather than textbook prose |
| `disfluency` | Filler and bridge phrases, hesitation, self-correction, brief thinking-out-loud while working |
| `lexicalVariety` | Avoids recycling the same openers, acknowledgements, and phrasing seen in earlier turns |
| `concision` | Says one thing and waits, instead of paragraph-shaped info dumps or restating the request |
| `spokenNumbers` | Numbers, dates, times, currency, and identifiers rendered the way a person says them aloud |
| `empathyCalibration` | Empathy when warranted, capped at roughly one explicit statement per call, no premature celebration |
| `scriptedness` | Freedom from IVR boilerplate, reflexive closings, and corporate/legalese register |
| `turnTaking` | Natural acknowledgement and handoff questions, no re-asking for information already supplied |

### Default conversation-level qualities

These are scored once across the whole call:

| Quality | Measures |
|---------|----------|
| `personaConsistency` | The agent is the same person throughout, with no tonal whiplash or register drift |
| `repetitionAvoidance` | Across the call, phrases, empathy lines, and sentence shapes are not recycled |
| `emotionalArcTracking` | Tracks the user's emotional trajectory, holds the gentle register, and only brightens when the user does |
| `conversationalFlow` | The call feels like one continuous conversation, not a series of context-free replies |

### Perceived latency

Dead air is the fastest way for an agent to stop sounding human, and it is the one dimension you cannot hear in a transcript. `perceivedLatency` is therefore **measured, not judged**: SCRAPI reads the platform's reported perceived latency for each turn from the conversation trace after the run and scores it against `latency_bands_ms`. No extra model call is involved, and any `perceivedLatency` factor the grader tries to invent is discarded.

| Perceived latency | Score | Label |
|-------------------|-------|-------|
| under 1s | 5 | excellent |
| 1s – 2s | 4 | very good |
| 2s – 3s | 3 | good |
| 3s – 4s | 2 | needs improvement |
| 4s and over | 1 | unacceptable |

Because a turn carries roughly ten judged factors, an unweighted latency factor would move the turn score by about 0.2 no matter how slow the agent was. `latency_weight` (default `3.0`) makes latency count for three factors instead of one. Set it to `0` to keep latency in the report without letting it affect any score.

Turns with no reported latency get no factor at all, rather than a neutral `3`, so a missing measurement cannot quietly drag a turn up or down. The platform publishes a conversation some time after the call ends — usually under a minute — so each simulation waits for it, polling until it appears or `latency_fetch_timeout_s` is spent. If it never arrives, the dimension is skipped and the rest of the metric still runs.

> [!TIP]
> That wait is real wall-clock time per simulation. If you are running a large suite and only want the judged dimensions, set `latency_fetch_timeout_s: 0`.

> [!WARNING]
> `latency_failure_threshold_ms` defaults to `4000`, so **a single turn at 4s or slower fails the simulation outright**, independently of `pass_threshold` and of whether every goal was met. Set it to `null` if you only want latency reported.

### Judging the audio, not the transcript

With `use_audio`, the agent's recorded audio for each turn is attached to the grading call so pacing, emotion, and disfluency are judged from what the caller actually heard. An inline tag such as `[warm]` or `[short pause]` is then only credited if it is audible — which is what catches tags that the agent read aloud verbatim or silently dropped.

This is enabled automatically when the simulation runs in `audio` modality; you do not need to set it. Audio is read from the platform's recording bucket by default and never downloaded. `audio_source` controls where it comes from:

- `auto` *(default)* — the recording bucket, falling back to WAVs captured locally during the run
- `gcs` — the recording bucket only
- `local` — locally captured WAVs only, which requires `capture_agent_audio=True`
- `none` — disable audio even when `use_audio` is set

If no audio can be attached, grading degrades to the transcript rather than failing.

> [!NOTE]
> Audio grading needs the app's evaluation audio recording bucket configured. Recordings are named per platform turn (`agent-turn-1.wav` is simulation turn 0), which is how each clip is matched to the turn it belongs to.

### How labels and scores are computed

Each factor score is an integer clamped to 1–5. Turn and overall scores are then derived by SCRAPI (not taken from the model's own holistic number), which keeps the scores, averages, and labels mutually consistent:

1. **Turn score** = weighted mean of that turn's factor scores, rounded to two decimals. Every judged factor has weight 1; `perceivedLatency` has weight `latency_weight`. (If the model returned no factors for a turn, its own score is used instead.)
2. **Turn label** comes from the score bands:
   - `score < bot_like_below` → `Bot-like` (default: below 2.5)
   - `score >= human_like_at_or_above` → `Human-like` (default: 3.75 and up)
   - otherwise → `Transitional`
3. **Overall score**:

   ```text
   overall_score = turn_weight * mean(turn scores)
                 + (1 - turn_weight) * mean(conversation factor scores)
   ```

   If the grader returned no conversation-level factors, `overall_score` is simply the mean of the turn scores. The result is clamped to 1–5 and rounded to two decimals, where **5 means fully human-like**.

4. **Overall label** is derived from `overall_score` using the same bands.

### Pass/fail behaviour

By default the *score* is **purely informational** — it reports a score and a label but never changes whether the simulation passed.

Setting `pass_threshold` opts the test case into enforcement: the simulation fails if `overall_score` is below the threshold, even when every goal and expectation was met.

```yaml
naturalness_metric:
  pass_threshold: 3.5  # fail the sim if the agent scores below 3.5/5
```

Separately, a turn at or above `latency_failure_threshold_ms` fails the run on its own. This is deliberately independent of the score: an agent can be charming and still be unusable if it leaves the caller in silence. The reason appears in the console line and the HTML report, and in `naturalness_details.failure_reasons`.

```yaml
naturalness_metric:
  latency_failure_threshold_ms: null  # report slow turns, never fail on them
```

### Enabling it for a whole run

You can turn the metric on without editing any YAML, using the `naturalness` argument on the constructor, `run_simulations()`, or `simulate_conversation()`:

```python
# Enable with defaults for every test case that doesn't declare its own block
sim_evals = SimulationEvals(app_name=app_name, naturalness=True)

# Same, at call time
results = sim_evals.run_simulations(test_cases=test_cases, naturalness=True)

# A dict is merged OVER whatever the test case declared
eval_conv = sim_evals.simulate_conversation(
    test_case=test_case,
    naturalness={"pass_threshold": 4.0, "model": "gemini-3.1-pro-preview"},
)
```

- `None` (the default) defers entirely to each test case.
- `True` enables the metric with defaults for test cases that don't declare the key. A test case with an explicit `naturalness_metric: false` stays off.
- `False` force-disables the metric everywhere, even where a test case asked for it.
- A dict is merged over the test case's own block, so run-level keys win.

The CLI exposes the same switch on `evals report`:

```bash
# Grade every simulation, even ones that don't declare the metric
cxas evals report --run --include sims --naturalness

# Skip grading entirely, even for test cases that do declare it
cxas evals report --run --include sims --no-naturalness

# Omit the flag to let each test case decide
cxas evals report --run --include sims
```

The flag is tri-state, mirroring the `naturalness` argument: `--naturalness` is `True`, `--no-naturalness` is `False`, and omitting it is `None`.

> [!TIP]
> `--no-naturalness` is the hill-climbing switch. Iterate on correctness first with grading off — it skips the extra Gemini call per run and stops any `pass_threshold` from failing a simulation that is functionally correct — then drop the flag to evaluate naturalness once the task success rate is where you want it.


### What you get in the results

When the metric ran, each entry in `sim_results.json` gains three keys (they are **absent** when it did not run, so existing consumers see an unchanged payload):

| Key | Example | Description |
|-----|---------|-------------|
| `naturalness` | `"4.1/5"` | The overall score |
| `naturalness_label` | `"Human-like"` | `Bot-like`, `Transitional`, or `Human-like` |
| `naturalness_details` | *(object)* | The full result: per-turn gradings and factors, `conversation_factors`, `factor_averages`, `label_counts`, `summary`, `model`, `pass_threshold`, `passed`, `latency_ms_by_turn`, and `failure_reasons` |

The console progress line also gains a ` | naturalness: 4.1/5 (Human-like)` suffix, plus ` | FAILED: ...` when a turn broke the perceived-latency limit.

Programmatically, the result object lives on the conversation and feeds the report:

```python
eval_conv = sim_evals.simulate_conversation(test_case=test_case, naturalness=True)

result = eval_conv.naturalness_result  # None if the metric didn't run
if result:
    print(result.overall_score, result.overall_label.value)
    print(result.factor_averages)  # mean score per quality across turns
    print(result.summary)

report = eval_conv.generate_report()
if report.naturalness_df is not None:
    print(report.naturalness_headline)  # e.g. "Overall: 4.1/5 (Human-like)"
    print(report.naturalness_df)
```

### Cost

Grading adds **one extra Gemini call per simulation run** (per test case, per repeat), sending the whole transcript. It's cheap relative to the simulation itself, but it is not free — reserve it for the evals where sounding human actually matters. In `audio` modality the agent's recordings are attached to that call as well, which makes it larger; set `audio_source: none` to grade the transcript only. `perceivedLatency` costs nothing extra: it is read from the conversation trace, not judged.

---

## Interpreting results

The `SimulationReport` object has two DataFrames, plus an optional third when the [Naturalness Metric](#naturalness-metric) ran:

### `goals_df`

| Column | Description |
|--------|-------------|
| `eval_name` | Name of the simulation |
| `step_index` | Which step (0-indexed) |
| `goal` | The goal text |
| `status` | `Completed` or `Not Completed` |
| `justification` | Gemini's explanation |
| `turns_used` | How many turns it took |

### `expectations_df`

| Column | Description |
|--------|-------------|
| `eval_name` | Name of the simulation |
| `expectation` | The expectation text |
| `status` | `Met` or `Not Met` |
| `justification` | Gemini's explanation |

### `naturalness_df`

Present only when the Naturalness Metric ran — otherwise `report.naturalness_df` is `None`. One row per graded agent turn:

| Column | Description |
|--------|-------------|
| `turn` | Agent turn index (0-indexed) |
| `label` | `Bot-like`, `Transitional`, or `Human-like` (colorized in the terminal) |
| `score` | The turn's weighted 1–5 score |
| `latency_s` | The measured perceived latency in seconds, when the platform reported it |
| *(one per quality)* | The 1–5 score for each graded quality, e.g. `emotion`, `pacing`, `perceivedLatency` |
| `justification` | Gemini's explanation for the turn |

`report.naturalness_headline` carries the summary line, e.g. `Overall: 4.1/5 (Human-like)`, with ` | threshold 3.5 -> PASS` appended when `pass_threshold` is set and ` | FAILED: ...` when a turn broke the perceived-latency limit.

### Reading the output

```python
# Overall pass rate
total = len(report.goals_df)
passed = (report.goals_df["status"] == "Completed").sum()
print(f"Steps completed: {passed}/{total} ({passed/total*100:.0f}%)")

# Failed steps
failed = report.goals_df[report.goals_df["status"] != "Completed"]
for _, row in failed.iterrows():
    print(f"FAILED: {row['goal']}")
    print(f"  Reason: {row['justification']}")
```

---

## Using Tool Fakes

By default, simulations execute real tool calls (including webhooks or database queries) when the agent invokes a tool. During testing, you can enable **Tool Fakes** to tell the platform to return pre-defined mock/fake responses instead of running the actual tool backend.

> [!NOTE]
> Tool fakes (mock tool responses) are defined per-tool within the Agent Studio Console. See the [Mock Tool Responses](mock-tool-responses.md) guide for how to configure these in the console and write the mock handler scripts.

To enable tool fakes in your simulations:

### Programmatically
Pass `use_tool_fakes=True` when calling `simulate_conversation` or `run_simulations`:

```python
eval_conv = sim_evals.simulate_conversation(
    test_case=test_case,
    use_tool_fakes=True,  # Bypasses real tool backends
)
```

### Via CLI
Run your simulations in bulk using the `cxas evals report` command with the `--run` and `--include sims` flags, specifying the agent app name, output directory, parallel workers, and the `--use-tool-fakes` flag:

```bash
cxas evals report \
    --run \
    --include sims \
    --app-name "projects/my-project/locations/us/apps/my-app" \
    --output-dir "eval-reports" \
    --sim-parallel 5 \
    --use-tool-fakes
```

---

## Tips for writing good simulations

**Keep steps focused**
: Each step should test one thing. Broad goals like "complete the full conversation" are hard to debug when they fail.

**Write meaningful success criteria**
: "The agent helped the user" is too vague. "The agent provided the order status and delivery date" is testable.

**Use `response_guide` to set tone**
: If your agent needs to handle impatient users or edge cases, use `response_guide` to set that context for the simulator.

**Use `static_utterance` for exact inputs**
: When you want to test how the agent handles a specific phrasing (e.g., "what's my ETA?"), use `static_utterance` to send that exact text.

**Use session parameters for mocking**
: Just like goldens, use `session_parameters` to inject mock tool responses so your simulations are deterministic and fast.
