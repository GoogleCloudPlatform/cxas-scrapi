---
title: Shadow Evals
description: Replay a past (production or simulated) conversation against the current agent using the caller's recorded audio.
---

# Shadow Evals

Shadow Evals replay a **past conversation** against the **current version** of
your agent. The simulated caller streams the *original recorded caller audio*
turn by turn, so the agent hears the same audio (accents, pauses, background
noise) as it did the first time. That lets you check a new agent version
against real calls, not just scripted or synthesized ones.

When the new agent goes off script (asks something new, or asks in a different
order), a Gemini-driven simulated caller decides each turn whether to:

- **replay a recorded caller turn** (`use_past_audio`), which is the default,
  or
- **synthesize a new reply with TTS** (`generate_tts`), only when no recording
  contains the needed information.

After the replay, Gemini judges the new conversation against your
natural-language `expectations`.

---

## Prerequisites

Check these before writing a Shadow Eval. `ShadowEvals.preflight` (run
automatically, see [Preflight checks](#preflight-checks)) checks most of them
for you.

1. **The original call was recorded.** The source app must have had audio
   recording (`loggingSettings.audioRecordingConfig`) enabled when the call
   happened. Recordings are stored as
   `gs://<bucket>/<project>/<location>/<app>/<date>/<conversation_id>/user-turn-N.wav`.
2. **You can read the recording bucket.** Your credentials need
   `storage.objects.get` / `list` on it.
3. **You know the session state the original call ran with.** If the original
   session relied on seeded session variables or tool fakes (for example a
   mocked caller or account lookup keyed on a session variable), set the same
   `session_parameters` / `use_tool_fakes`. If you don't, the agent can take a
   different path from turn one, and the replay can't follow the original call.

---

## YAML format

Put Shadow Eval files under `evals/shadows/` (subdirectories are fine; `.yaml`
and `.yml` are both picked up):

```yaml
config:                      # defaults inherited by every case below
  project_id: my-project     # where the *past* conversation lives
  location: us
  app_id: my-app
  gcs_bucket: gs://my-recordings-bucket
  use_tool_fakes: true       # run with the same tool fakes as the original
  session_parameters:
    customer_tier: gold      # same seeded state as the original session
  max_turns: 15

shadow_evals:
  - name: order_never_arrived
    conversation_id: 7f3c9a52-...        # the past conversation to replay
    replay_mode: hybrid                  # or "exact"
    tags: [P0]
    expectations:
      - "The agent recognizes the returning customer without asking."
      - "The agent finds the order and explains the delivery status."
      - "The agent offers a replacement or refund."
```

### Case fields

| Field | Type | Description |
|-------|------|-------------|
| `conversation_id` | string | **Required.** ID of the past conversation to replay |
| `expectations` | list | **Required.** Natural-language pass criteria judged by Gemini |
| `name` | string | Case name (defaults to `shadow_<conversation_id>`) |
| `replay_mode` | `hybrid` \| `exact` | See [Replay modes](#replay-modes). Default `hybrid` |
| `session_parameters` | dict | Session variables seeded at session start. Merged over `config.session_parameters` |
| `use_tool_fakes` | bool | Run with tool fakes. Overrides the run-level flag for this case |
| `project_id`, `location`, `app_id` | string | Where the past conversation lives (defaults to the target app) |
| `gcs_bucket` | string | Recording bucket. Inferred from the trace / source app if omitted |
| `max_turns` | int | Upper bound on replayed turns |
| `goal`, `response_guide`, `steps` | | Optional guidance for the simulated caller (inferred from the past call if omitted) |
| `audio_expectations` | list | Extra expectations judged together with the captured agent audio (enabled by agent audio capture / `artifacts_dir`) |
| `voice_config` | dict | TTS voice used for synthesized turns |
| `naturalness_metric` | bool \| dict | Optional [Naturalness Metric](local-simulations.md) |
| `initial_utterance` | string | First event/utterance sent to start the session |
| `tags` | list | Tags for filtering |

Everything in `config:` except `session_parameters` is a default that a case can
override; `session_parameters` are merged, with case values winning.

!!! warning "Typos are reported, not silently dropped"
    Unknown keys (for example `session_params` or `use_toolfakes`) produce a
    warning with a *did you mean* hint when the file is loaded, and a warning
    from `cxas lint` (rule E012). An invalid `replay_mode` is an error.

---

## Replay modes

| Mode | How the caller turns are chosen | Use it when |
|------|-------------------------------|-------------|
| `hybrid` (default) | Gemini matches each new agent prompt to an unused recorded caller turn and replays it; synthesizes TTS only for information no recording contains. If the generated text just restates a recorded turn, the recording is replayed instead. If the agent re-asks the same question, the same recording is played again. | The agent may have changed: new questions, different order, re-prompts |
| `exact` | Replays the recorded caller turns strictly in order, with no LLM. | You want an exact audio regression and expect the same dialog flow |

!!! tip
    Speech recognition can transcribe the same recording slightly differently
    from run to run (for example a short "four" heard as "for"), which makes
    the agent re-prompt. `hybrid` recovers by replaying the recording again;
    `exact` keeps going in order, so every later answer lands one question
    late. Prefer `hybrid` unless you really need a strict in-order replay.

---

## Running Shadow Evals

### CLI

```bash
cxas evals report --run \
  --app-name projects/my-project/locations/us/apps/my-app \
  --include shadows --modality audio \
  --output-dir eval_results/
```

Shadow Evals always run in audio modality. With `--output-dir`, each run's
audio and a side-by-side HTML report are written under
`eval_results/shadow_artifacts/<case>_run<N>/` (with a timestamp suffix on
`shadow_artifacts` when timestamped output is enabled).

### Python

```python
from cxas_scrapi.evals import ShadowEvals

shadow = ShadowEvals(app_name="projects/my-project/locations/us/apps/my-app")
cases = shadow.load_shadow_test_cases_from_file("evals/shadows/orders.yaml")

results = shadow.run_shadow_evals(
    cases,
    modality="audio",
    artifacts_dir="shadow_artifacts/",  # keep audio + HTML report per run
)
for r in results:
    print(r["name"], r["passed"], r["expectations"],
          "past audio:", r.get("past_audio_turns_used"),
          "tts:", r.get("tts_turns_generated"),
          r.get("report_path"))
```

For a single case, `run_shadow_conversation(test_case, artifacts_dir=...)`
returns the `ShadowUserConversation`; `conversation.generate_report()` gives
per-turn decision, goal and expectation DataFrames, and
`conversation.report_path` points to the HTML report.

---

## Preflight checks

`run_shadow_evals` calls `ShadowEvals.preflight` for every case before opening
any session (`preflight=False` turns this off). You can also call it on its
own:

```python
check = shadow.preflight(cases[0])
print(check.ok, check.past_user_turns, check.past_user_turns_with_audio)
for issue in check.issues:
    print(issue.level, issue.message)
```

| Level | Check |
|-------|-------|
| error | The past conversation can't be loaded (wrong `project_id` / `location` / `app_id` / ID) |
| error | The past conversation has no caller turns |
| error | None of the caller turns has a recording, so the replay would be pure TTS |
| warning | Only some caller turns have recordings |
| info | The target app doesn't record audio, so new calls won't be in GCS (use `artifacts_dir`) |
| info | No `session_parameters` / `use_tool_fakes` set |

Cases with an error are reported as failed with `error: "Preflight failed:
..."` and are not run. Other issues are printed and attached to the result as
`preflight_issues`.

---

## Side-by-side report and audio artifacts

Downloaded and captured audio normally lives in a temporary directory that is
deleted after each run. Pass `artifacts_dir` (or `--output-dir` on the CLI) to
keep it, which makes it possible to *audit* the replay:

```
<artifacts_dir>/<case>_run1/
  shadow_report.html          # past call vs. new call, turn by turn, with audio
  shadow_result.json          # the same data, machine-readable
  audio/past_user_turn_N.wav  # recorded caller audio (exactly what was streamed)
  audio/past_agent_turn_N.wav # the original agent reply
  audio/new_agent_turn_N.wav  # the new agent reply
```

Each row of the report pairs a new turn with the past turn it replayed. You
can play the recorded caller audio, the original agent reply and the new agent
reply next to each other, alongside the simulated caller's decision and
reasoning. Recorded turns that were never replayed are listed at the end.
Agent audio is captured automatically when `artifacts_dir` is set. The
directory uses relative paths, so it can be zipped or served statically as is.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| Most caller turns are `generate_tts` | Recordings weren't found. Check `gcs_bucket` and that recording was enabled for the original call (see preflight) |
| The agent takes a different path from the first turn (e.g. doesn't recognize the caller, lookups fail) | The original session relied on seeded state. Set `session_parameters` and/or `use_tool_fakes` |
| The agent re-prompts for a short answer that was clearly spoken | Speech recognition varies from run to run; use `hybrid` mode |
| A key in your YAML seems to have no effect | Check the load-time warnings or run `cxas lint` (E012) for typos |
