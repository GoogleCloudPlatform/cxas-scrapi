# Turn-eval reference: operators, context, audio, curation heuristics

Table of contents:
- Deterministic operators (what each asserts)
- historical_contexts (prefix replay) forms
- Audio gotchas
- Conversation-fetch reliability (why drafts sometimes lack tool outputs)
- Curation heuristics (the agent's judgment job)

## Deterministic operators

A turn-eval probe is one user input asserted against the agent's response. The
response is decomposed into five signals: `full_text`, `called_tools`,
`tool_inputs`, `tool_outputs`, `target_agent`. Operators (all ANDed):

| `type` | Asserts | Match semantics |
|---|---|---|
| `agent_transfer` | `target_agent` | exact OR suffix |
| `tool_called` | a tool ran | exact OR suffix on name |
| `no_tools_called` | nothing ran | `value: null` |
| `tool_input` | call args | **dict subset** — list only the args that matter |
| `tool_output` | call response | dict subset; `{tool: {}}` = "tool returned at all" |
| `contains` | `full_text` | case-insensitive substring |
| `equals` | `full_text` | exact (whitespace-trimmed) |
| `fuzzy_match` | `full_text` | embedding cosine ≥ 0.75 (paraphrase-tolerant) |
| *bare string* | full trace | LLM-judged (flash-lite) — subjective only |

Prefer structural operators (`agent_transfer`, `tool_called`, `tool_input`,
`tool_output`) over prose (`contains`/`equals`) — they survive rewording.

## historical_contexts (prefix replay) forms

Order of preference (most faithful first):
1. `test_name: <other-test>` — chain; child inherits the parent's real session.
2. `session_id: <id>` + `turn_count: <k>` — replay the REAL recorded
   conversation's first k turns; restores real session state. Needs the
   platform conversation fetch to succeed.
3. `utterances: [{user: ...}, {agent: ..., name: ...}]` — inline fabricated
   prefix. Does NOT re-run prior tools, so resulting session state (e.g.
   `auth_status`, `account_id`) MUST be injected via the probe's `variables:`.

The generator emits form 2 when it can fetch the conversation, else form 3 +
`variables`.

## Audio gotchas

The agent under test is audio-native; probes run with `config: {modality: AUDIO}`.
- STT drops formatting: `"152.10"` is transcribed as `"152 10"`. Write
  `contains`/`equals` against the SPOKEN form, or assert `tool_output` instead.
- Favor structural checks; avoid asserting long verbatim sentences.
- `use_tool_fakes: true` keeps the audio model the only quota-bound call.

## Conversation-fetch reliability

Full fidelity (tool args + **outputs** + real `session_id` replay) requires the
CES `get_conversation` read. The API IS available (ces_v1beta, global
`ces.googleapis.com`) and returns tool_call + tool_response chunks — no GCS
bucket or API-version change needed. But in some projects the read **flaps**
(same call returns full data one moment, `501/"status 404"` the next). The
generator retries (`--fetch-retries`) then falls back to the local
`detailed_trace`, which has tool calls + args but NOT outputs (so no
`tool_output` assertions; uses `utterances`+`variables` instead of replay).
If outputs are unavailable, curation can RECONSTRUCT them (see below).

## Curation heuristics (the agent's job)

The script harvests everything observed; the agent decides what is *required*:

- **Prune incidental calls.** Keep load-bearing tools (`billing_account_lookup`,
  `service_diagnostics_lookup`, the transfer). Drop bookkeeping (`set_session_state`,
  `end_session`) unless the scenario is specifically about them.
- **Flag suspected bugs — do not encode them.** If the capture came from a
  FAILED sim, or shows wrong behavior (e.g. authenticated as TEL-1003 but the
  agent looked up TEL-1001), do NOT turn that into an assertion. Surface it.
- **Pick semantic `tool_input` keys.** Pin identifiers/decision args
  (`account_id`, `line_id`), not volatile ones.
- **Reconstruct missing `tool_output`.** When the trace lacks outputs, ground an
  assertion from the spoken answer + the `test_accounts_data` fixture: agent says
  balance is "152 10" and fixture says TEL-1003 balance is "152.10" → emit
  `tool_output {billing_account_lookup: {current_balance: '152.10'}}` (or a
  `contains` on the spoken form).
- **Convert free-text to deterministic where possible.** "Must not reveal another
  account" → `tool_input {account_id: <authenticated>}`. Keep genuinely
  subjective ones as bare strings.
- **Name + tag** probes by intent so `cxas sxs --tags` can slice them.
