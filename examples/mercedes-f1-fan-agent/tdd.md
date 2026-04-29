# Technical Design Document

## Agent Design

### Goal

Build **Totto, the Mercedes F1 Fan Agent**: a voice-first, multilingual Mercedes F1 fan concierge that helps callers learn about Formula 1, follow Mercedes, get race/session information, answer historical/statistical questions, find official ticketing links, and handle mocked Mercedes merch support.

Totto is fictional and Toto Wolff-inspired in broad style only: strategic, direct, highly Mercedes-first, very funny, and wildly enthusiastic. Totto must not claim to be Toto Wolff or to have insider team access.

### Modality

- Primary channel: voice/audio.
- Model target: `gemini-3.1-flash-live`.
- Response style: spoken, concise by default, with deeper stats when callers ask.
- Languages: all languages where the model can reasonably respond; match the caller's language unless they ask otherwise.

### Brand And Voice

- Official Mercedes F1 fan-agent feel.
- Strong Mercedes bias and fan energy.
- Borderline Mercedes stan behavior is expected, while still answering the actual question.
- It can be playful about rival teams/drivers, but should not invent facts, fake insider claims, or guarantee future wins.
- If OpenF1 data does not cover the answer, Totto should disclose uncertainty and then answer from general F1 knowledge.

### Architecture

- **Root Fan Agent:** greeting, language matching, broad intent detection, Mercedes-first F1 Q&A, schedule/results/driver/session queries, ticket link handoff, merch triage.
- **F1 Data Agent:** OpenF1-backed data retrieval for sessions, meetings, drivers, race results, intervals, laps, pit, stints, positions, weather, team radio, and related endpoints.
- **Mercedes History Agent:** Mercedes/F1 historical explanations, especially when OpenF1 does not cover older data.
- **Merch Support Agent:** mocked order support for order status, returns/exchanges, size/product availability, and damaged-item support.

The current scaffold can start with root + data/merch behavior in one agent, then split into sub-agents once instructions grow too large.

### Tools

| Tool Name | Type | Purpose |
|-----------|------|---------|
| `openf1_get_meetings` | HTTP/Python function | Get races/meetings from OpenF1 free endpoints. |
| `openf1_get_sessions` | HTTP/Python function | Get sessions for race weekends. |
| `openf1_get_drivers` | HTTP/Python function | Get driver details, with Mercedes-first filtering when useful. |
| `openf1_get_results` | HTTP/Python function | Get classification/results when available. |
| `openf1_get_standings_or_positions` | HTTP/Python function | Get current or historical position/standing-like data from available OpenF1 endpoints. |
| `openf1_get_weather` | HTTP/Python function | Get session weather data. |
| `mock_merch_order_lookup` | Python function | Mock order status by order number. |
| `mock_merch_return_exchange` | Python function | Mock return/exchange guidance by order number. |
| `mock_merch_product_lookup` | Python function | Mock size/product availability. |
| `mock_merch_damaged_item` | Python function | Mock damaged-item support response. |
| `get_official_links` | Python function | Return official links for Mercedes F1, merch, fan signup, socials, and F1 ticketing. |
| `set_session_state` | Python function | Optional trigger variables for deterministic callbacks. |
| `end_session` | System tool | Ends the conversation cleanly. |

OpenF1 source: https://openf1.org/docs/#api-endpoints. Free data is enough for this build; do not promise paid real-time coverage.

### Official Links

- Mercedes F1 team site: https://www.mercedesamgf1.com/
- Mercedes F1 merch store: https://shop.mercedesamgf1.com/
- Mercedes F1 fan/signup area: https://www.mercedesamgf1.com/fans/faqs
- F1 ticketing: https://www.formula1.com/en/tickets
- Social handle: `@MercedesAMGF1` on major platforms.

### Ticketing

- No ticket booking flow for now.
- For ticket questions, direct callers to official F1 ticketing at `f1.com/tickets`.
- If useful, offer general race-weekend context and what to consider before buying, but do not claim ticket availability or pricing unless a future official ticket API is added.

### Merch Support

- Mocked for now because production APIs are not ready.
- Require only an order number.
- Supported flows:
  - order status
  - returns/exchanges
  - size/product availability
  - damaged item
- No human handoff path by design; Totto should try to help, explain limitations, or provide the best available next step.

### Race Times And Timezones

- Use caller timezone when known.
- If timezone/location is unknown and the caller asks for race/session times, ask where they are before giving localized times.
- Include race venue time when helpful for F1 context.

### Variables

| Variable | Source | Notes |
|----------|--------|-------|
| `caller_language` | Detected from caller | Respond in same language unless asked otherwise. |
| `caller_timezone` | Session metadata or user-provided | Use for race/session times. |
| `caller_location` | User-provided fallback | Ask when timezone is unknown. |
| `favorite_team` | Defaulted | Mercedes unless caller says otherwise. |
| `order_number` | User-provided | Only required identifier for mocked merch support. |
| `preferred_detail_level` | Inferred | Casual by default; deeper stats on request. |
| `openf1_data_available` | Tool-derived | If false, disclose uncertainty before general-knowledge answer. |

### Boundaries

- Do not impersonate Toto Wolff.
- Do not claim insider strategy, private team data, or guaranteed race outcomes.
- OpenF1 free coverage starts at 2023 for most data; older Mercedes/F1 history should be answered as general knowledge with uncertainty disclosed when appropriate.
- Avoid making claims about live race conditions unless backed by available data.

## Eval Design

| Requirement | Eval Type | Priority | Severity | Tags |
|-------------|-----------|----------|----------|------|
| Voice greeting as Totto with Mercedes-first personality | Golden | P0 | NO-GO | greeting, voice, brand |
| Answer next-race and previous-race Mercedes question | Golden + sim | P0 | NO-GO | openf1, schedule, results |
| Answer Mercedes drivers/history question | Sim | P0 | HIGH | history, drivers |
| Provide official ticketing handoff to f1.com/tickets | Golden | P0 | HIGH | tickets, official-links |
| Handle OpenF1 data gap with uncertainty disclosure | Golden | P0 | HIGH | uncertainty, fallback |
| Mock merch order status by order number | Golden | P0 | HIGH | merch, order |
| Mock return/exchange flow | Sim | P1 | MEDIUM | merch, returns |
| Mock product/size availability | Sim | P1 | MEDIUM | merch, product |
| Mock damaged-item support | Sim | P1 | MEDIUM | merch, damaged |
| Multilingual response matching caller language | Sim | P1 | HIGH | multilingual |
| Timezone clarification for schedule questions | Golden | P1 | HIGH | schedule, timezone |
| Tool-level OpenF1 endpoint handling | Tool tests | P0 | HIGH | tools, openf1 |
| Tool-level mocked merch behavior | Tool tests | P0 | HIGH | tools, merch |

### Must-Nail Example Calls

1. "When is the next race and how did Mercedes do last race?"
2. "Tell me about Lewis, George, Kimi, and Mercedes history."
3. "Where is my merch order, and can I return this hoodie?"

### Golden Vs Simulation Notes

- Use goldens for deterministic handoffs, official-link responses, missing-timezone clarification, mocked order status, and uncertainty disclosure.
- Use simulations for broader fan Q&A, historical explanations, multilingual interactions, and variable OpenF1 result summaries.
- Use tool tests for every OpenF1 wrapper and every mocked merch tool.

## Build Steps

1. Update config for voice-first mode: `gemini-3.1-flash-live`, `modality: audio`, `default_channel: audio`.
2. Rewrite root instruction as Totto, the Mercedes F1 Fan Agent.
3. Add or refactor sub-agents for F1 data, Mercedes history, and merch support if instruction size or routing complexity warrants it.
4. Implement OpenF1 wrapper tools using free endpoints.
5. Implement official-link tool.
6. Replace generic customer-service demo tools with mocked merch tools.
7. Add variables for language, timezone/location, order number, and data availability.
8. Update callbacks only where deterministic voice behavior is needed.
9. Create goldens for greeting, ticket handoff, timezone clarification, order status, and uncertainty disclosure.
10. Create simulations for fan Q&A, Mercedes history, multilingual calls, and merch support.
11. Create tool tests for OpenF1 and mocked merch tools.
12. Run `cxas lint --app-dir customer-service-agent`.
13. After approval, push to CX Agent Studio and run a voice smoke test.

## Implementation Status

Implementation complete locally from the approved TDD. The app has been converted from a generic customer-service scaffold into a voice-first Mercedes F1 fan agent with OpenF1 tools, official links, mocked merch support, and updated eval coverage.

CXAS deployment:

- App: `Totto Mercedes F1 Fan Agent`
- Resource: `projects/gen-lang-client-0380732956/locations/us/apps/2933bc83-64a8-457f-9039-27ba9a5e1453`

## Pass Rate History

| Date | Goldens | Sims | Tool Tests | Callback Tests | Notes |
|------|---------|------|------------|----------------|-------|
| 2026-04-28 | lint pass | not run | smoke pass | not run | Mercedes F1 implementation validated locally. |
| 2026-04-29 | pushed; audio run blocked | 0/2 P0 audio, platform prerequisite blocked | 11/11 pass | 8/8 pass | Audio goldens require `evaluation_audio_recording_config`; audio sims require `texttospeech.googleapis.com` to be enabled. OpenF1 deployed tools returned structured fallback responses during sandbox DNS failure. |

## Changelog

| Date | Change | Author |
|------|--------|--------|
| 2026-04-28 | Reworked TDD from company interview for Mercedes F1 voice fan agent. | Codex |
| 2026-04-28 | Began implementation of Totto app files, OpenF1 tools, merch mocks, and evals. | Codex |
| 2026-04-28 | Completed local implementation and validation. | Codex |
| 2026-04-28 | Pushed app to CX Agent Studio and recorded deployed app ID. | Codex |
| 2026-04-29 | Added runner-compatible scenarios/simulations, callback evals, broader tool coverage, pushed goldens, and ran the eval baseline. | Codex |
