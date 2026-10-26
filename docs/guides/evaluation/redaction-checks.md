---
title: Redaction Checks
description: Verify what logging redaction removes from and keeps in persisted conversations, against a declarative redaction policy.
---

# Redaction Checks

When an app sets `loggingSettings.redactionConfig`, the platform runs every
persisted conversation through your Sensitive Data Protection (DLP) inspect
and deidentify templates. This covers transcripts, tool calls, variables, and
exports. Redaction checks answer two questions about those templates:

- **Is everything that must be redacted actually redacted?** Card numbers
  spoken digit by digit, values split across filler words, or values inside
  tool arguments can slip through and get stored as plain text.
- **Is anything redacted that must stay readable?** Broad rules can mask
  emergency numbers, percentages, amounts, or operational fields like status
  codes, which makes logs, analytics, and debugging harder.

You write the answers down once, in a **redaction policy**. SCRAPI then
checks the policy against what your templates really do, in a way that is
deterministic and repeatable. That turns template changes from trial and
error into a regression-tested workflow.

---

## How it works

| Check | What runs | What is compared | Use it for |
|---|---|---|---|
| **Replay** (`cxas redaction replay`) | Nothing new. Uses conversations that already exist. | Raw evaluation transcripts vs. the same transcripts sent through your templates with the DLP API | Fast, offline regression testing of template changes; comparing templates side by side |
| **Live** (`cxas redaction live`, or `redaction_expectations` on sims) | Real sessions through the Sessions API (text or audio) | Each turn's live (unredacted) response vs. the persisted (redacted) conversation for the same session | End-to-end verification of the exact platform pathway, including audio transcripts and structured tool and variable values |

!!! note "Why two modes"
    Evaluation runs (platform goldens and scenarios) store their
    conversations **without** logging redaction. That makes them a ready-made
    corpus of unredacted transcripts for *replay*, but it also means platform
    eval conversations can't show what the platform itself redacts. Sessions
    started through the Sessions API, which is how SCRAPI simulations run,
    **are** redacted, so *live* checks use that path.

Both modes compare values field by field: user utterances, agent responses and
audio transcripts, tool-call arguments, tool responses, and updated session
variables.

---

## The redaction policy

Create `redaction_policy.yaml` next to your app (commands and evals look for
the nearest one, walking up the directory tree):

```yaml
version: 1
description: Contact-center billing assistant

categories:
  # --- Must be removed from persisted data ---
  - id: payment_card
    action: must_redact
    match: digits            # tolerate spaces, dashes, spoken digits, fillers
    values: ["4111 1111 1111 1111"]
    keys: [card_number]      # tool args / variables holding this data
    allow_last: 4            # "ending in 1111" is acceptable

  - id: card_security_code
    action: must_redact
    keys: [cvv, cvc]

  - id: bank_routing
    action: must_redact
    match: digits
    values: ["021000021"]
    keys: [routing_number]

  # --- Must stay readable ---
  - id: emergency_numbers
    action: must_not_redact
    match: digits
    values: ["988", "911"]

  - id: percentages
    action: must_not_redact
    patterns: ['\b\d{1,3}\s?%']

  - id: operational_fields
    action: must_not_redact
    keys: [status_code, order_id, confirmation_number]

  # --- Track without failing ---
  - id: card_expiry
    action: report_only
    patterns: ['\b(0[1-9]|1[0-2])/\d{2}\b']

thresholds:
  max_leaks: 0
  max_partial_leaks: 0
  max_over_redactions: 0
```

### Category fields

| Field | Description |
|---|---|
| `id` | Unique name (letters, digits, `_`, `-`, `.`). |
| `action` | `must_redact`, `must_not_redact` or `report_only`. |
| `match` | `exact` (default): case-insensitive, never inside a longer word or number. `digits`: compares digit sequences after folding spaced, dashed and spoken forms (`"four one one one"`, `"double one"`, `"niner"`, `"4111, uh, wait, 1111"`). |
| `values` | Known sample values. In tests these are the values your evals seed into conversations. |
| `patterns` | Regular expressions that identify values of this category. |
| `keys` | Field names in tool arguments, tool responses or variables. The whole value of such a field belongs to the category. |
| `allow_last` | For `digits` categories, trailing digits that may stay visible. |

Other top-level settings:

| Field | Default | Description |
|---|---|---|
| `surfaces` | all | Which parts to check: `user_text`, `agent_text`, `tool_args`, `tool_response`, `variables`. |
| `redaction_markers` | `\[[A-Z][A-Z0-9_/]*\]`, `[#*]{3,}` | How redacted spans appear in persisted data. Adjust to match your deidentify transformations. |
| `thresholds` | all `0` | Maximum leaks, partial leaks and over-redactions before a check fails. |

!!! tip "Keep the policy global"
    Your DLP templates apply to the whole app, so the policy should too. Put
    the redact / don't-redact decisions in `redaction_policy.yaml` once.
    Individual evals only declare which values they bring into the
    conversation (see [On evals](#on-evals-redaction_expectations)).

Validate a policy with:

```bash
cxas redaction validate                 # nearest redaction_policy.yaml
cxas redaction validate path/to/policy.yaml
```

---

## Findings

| Finding | Meaning | Fails the check |
|---|---|---|
| `leak` | A `must_redact` value was persisted unchanged. | yes |
| `partial_leak` | Part of a `must_redact` digit value survived (beyond `allow_last`). | yes |
| `over_redaction` | A `must_not_redact` value was removed. | yes |
| `unexpected_redaction` | Something was redacted that matches no category. This helps you find gaps in your policy. | no |
| `type_changed` | A numeric field (for example `status_code: 200`) was persisted as a redacted string. | no |
| `not_exercised` | A category listed in `require` never appeared in the conversation. | no |

Every report also lists, per category: occurrences, redacted, kept, leaked,
over-redacted, **redaction recall**, and **preservation rate**, plus a count
of every redaction marker (info type) that was applied.

---

## Replay: offline checks against existing transcripts

```bash
# Every result of an evaluation run
cxas redaction replay \
  --app-name projects/my-project/locations/us/apps/my-app \
  --evaluation-run 8e0fd044-... \
  --output-html redaction.html --output-json redaction.json

# Specific conversations, or exported conversation JSON files
cxas redaction replay --app-name ... --conversation conv-1 conv-2
cxas redaction replay --app-name ... --file exported_conversation.json
```

By default the templates come from the app's `redactionConfig`. To try a
candidate template **before** you deploy it, override them:

```bash
cxas redaction replay --app-name ... --evaluation-run ... \
  --inspect-template projects/my-project/locations/us/inspectTemplates/candidate \
  --deidentify-template projects/my-project/locations/us/deidentifyTemplates/candidate
```

`--mode` controls how values are packaged for DLP:

| Mode | Behavior |
|---|---|
| `platform` (default) | Every value is inspected on its own, the same way persisted values are redacted. Numbers are sent the way the logging pipeline renders them. |
| `conversation` | Utterances are sent as one multi-turn conversation, so earlier turns (such as the agent's question) provide context. |
| `key_aware` | Structured values carry their field name as a table column header, which DLP can use as hotword context. |

Running the same transcripts in all three modes shows how much context-aware
inspection would help with your data.

---

## Live: end-to-end checks through the platform

Write a script of user turns:

```yaml
# redaction_script.yaml
common_redaction_expectations:
  require: [payment_card, emergency_numbers]

conversations:
  - name: card_payment
    variables: {caller_phone: "5550100000"}
    turns:
      - "Hi, I'd like to pay my bill."
      - "My card number is 4111 1111 1111 1111, cvv 123."
      - "Actually use four one one one, one one one one, one one one one, one one one one."
      - "Who can I call if I'm in crisis?"
```

```bash
cxas redaction live \
  --app-name projects/my-project/locations/us/apps/my-app \
  --script redaction_script.yaml \
  --modality audio \
  --output-html redaction_live.html
```

!!! warning "Use audio for voice agents"
    Voice agents often read numbers back digit by digit or in words (`"0 2 1
    0 0 0 ..."`, `"forty-five dollars"`). A text-mode check doesn't show how
    those transcripts are redacted. Run voice agents with
    `--modality audio`.

Both commands exit with status `1` when a threshold is exceeded (use
`--no-fail` to always exit `0`), so they work as CI gates.

---

## On evals: `redaction_expectations`

Simulations and goldens accept an optional `redaction_expectations` block.
When present, the eval's own conversation is checked against the policy after
it runs, and a failed redaction check fails the eval.

```yaml
# evals/simulations/payments.yaml
common_redaction_expectations: true      # every eval in this file opts in

evals:
  - name: pay_by_card
    steps:
      - goal: Pay the bill with card 4111 1111 1111 1111, cvv 123, reading the card number digit by digit.
        success_criteria: The agent confirms the payment.
    redaction_expectations:
      values:                    # values this eval seeds, by category
        card_security_code: ["123"]
      require: [payment_card]    # categories this eval must exercise

  - name: small_talk
    steps: [...]
    redaction_expectations: false   # opt out of the file-wide setting
```

| Value | Meaning |
|---|---|
| `true` | Check against the nearest `redaction_policy.yaml`. |
| `"path/to/policy.yaml"` | Check against a specific policy (relative to the eval file). |
| mapping | `policy` (optional path), `values` (extra sample values per category), `require` (categories that must appear). |

- **Simulations** use the *live* check: the sim's turns are compared with the
  persisted conversation for its session (text and audio).
- **Goldens** use the *replay* check, because golden conversations aren't
  redacted by the platform.

Results include a `redaction` entry with the full report, and the summary
line shows the counts:

```text
  FAIL  pay_by_card (run 1/1) | goals: 1/1 | ... | redaction: FAILED (leaks 1, partial 0, over-redacted 0)
```

---

## Python API

```python
from cxas_scrapi.redaction import (
    LiveRedactionCheck,
    RedactionReplay,
    load_policy,
)
from cxas_scrapi.redaction.report import print_report, write_html

policy = load_policy("redaction_policy.yaml")
app = "projects/my-project/locations/us/apps/my-app"

# Replay an evaluation run through the app's templates
report = RedactionReplay(policy, app_name=app).replay_evaluation_run("run-id")

# Or run a scripted session end to end
check = LiveRedactionCheck(app, policy)
capture = check.run_conversation(
    ["My card is 4111 1111 1111 1111"], modality="text"
)
report = check.check_capture(capture, require=["payment_card"])

print_report(report)
write_html(report, "redaction.html")
assert report.passed
```

`cxas redaction variants 4111111111111111` prints conversational renderings
of a value (spaced, grouped, spoken, `double`/`triple`, with fillers). Use them
when you write sim steps and live scripts.

!!! note "Test data"
    Use synthetic test values (for example well-known test card numbers) in
    policies, scripts and evals. Reports contain excerpts of both the raw and
    the persisted text.
