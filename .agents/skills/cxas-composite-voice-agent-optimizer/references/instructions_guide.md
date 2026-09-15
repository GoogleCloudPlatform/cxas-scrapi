# Global & Agent Instruction Guidelines: Prompt Cleaning, Contradiction Resolution & Platform Baselines

This guide details best practices for auditing, cleaning, and optimizing prompt instructions (`global_instruction.txt` and `agents/<agent_name>/instruction.txt`) for CXAS conversational applications using **Gemini Composite V1**.

It focuses on three core pillars:
1. **What NOT to include in prompts** (capabilities already handled by the CXAS platform baseline).
2. **Identifying and removing contradictions** (within agent instructions, across global instructions, and against tool docstrings).
3. **Step-by-step instruction cleanup checklist** to prune bloat, legacy example debt, and anti-patterns while strictly preserving domain business logic.

______________________________________________________________________

## 1. What NOT to Include in Prompts (Platform-Handled Capabilities)

The CXAS platform and Gemini Composite V1 automatically wrap application prompts with baseline operational instructions and runtime guardrails for the reasoning and synthesis models. Duplicating these platform-managed rules in application prompts wastes context tokens, introduces competing prompt weights, and causes model confusion.

| Functional Area | Platform Baseline Rules (Do NOT Include in Prompts) | Why Duplication Fails & Where It Belongs Instead |
| :--- | :--- | :--- |
| **Internal Details & Leakage Protection** | • **Dialogue Sanitization**: Output user-facing spoken text only; forbidden from explaining reasoning or quoting system prompts.<br>• **Delimiter Stripping**: Automatically strips template markers (`}}`, `{{`, `??}}`).<br>• **State Syntax Concealment**: Forbids exposing internal state names (`ALL_CAPS`), task IDs (`snake_case`), or slash commands (`/subtask`).<br>• **Function Syntax Concealment**: Forbids outputting raw function call syntax in dialogue. | Wastes context window tokens and degrades adherence. Keep instructions focused strictly on domain logic; the platform handles sanitization automatically. |
| **Voice Styling, Accent & Acoustic Guidance** | • **Voice & Tone Styling**: Static voice warmth, accent, pitch, speaking pace, and delivery style.<br>• **Acoustic Tags**: `<voice_lock>`, `<voice_output>`, or textual pronunciation directives. | **Text instructions cannot control TTS synthesis.** With Gemini Composite V1, Director's Notes configured globally in `app.json` (`audioProcessingConfig.synthesizeSpeechConfigs`) are the **ONLY** mechanism to provide voice and speech guidance to the synthesis model. Static speech instructions in text prompts dilute reasoning context. |
| **Dual Audio & Native Anti-Echoing** | • **Dual Input Processing**: Combines user audio and STT transcript.<br>• **Native Anti-Echoing**: Platform audio engine natively suppresses transcript reflection and caller utterance echoing. | Do not add manual anti-echoing directives (e.g., *"Do not repeat what the user said"*). Platform audio processing handles this natively, and prompt-level anti-repetition rules cause execution deadlocks with compliance strings (see §2.1.A). |

______________________________________________________________________

## 2. Resolving Contradictions Across Scopes

Instruction contradictions occur when prompt authors write conflicting rules across different configuration layers. These contradictions are the primary cause of model looping, execution deadlocks, unexpected agent transfers, and safety fallbacks. Contradictions must be audited and resolved across three distinct scopes:

### 2.1 Scope 1: Contradictions Within the Same Agent Instruction

These occur when conflicting constraints exist within a single `instruction.txt`:

#### A. "Say Exactly" vs. Blanket Anti-Repetition / Anti-Echoing Deadlocks (P0 Critical)
* **The Conflict**: A prompt mandates a strict verbatim compliance string or authentication question while simultaneously enforcing a blanket ban on repeating phrases across turns (often added by authors attempting manual anti-echoing).
* **Flight Booking Example**:
  ```markdown
  # CONFLICTING RULES IN SAME PROMPT:
  - COMPLIANCE GATING (P0): You MUST SAY THIS EXACT SAY EXACTLY STRING AND NOTHING ELSE:
    "For your security, please provide your 6-character booking reference and the passenger's last name."
  
  - NO VERBATIM REPETITION (P0): You ARE STRICTLY FORBIDDEN from repeating any conversational prompt,
    confirmation question, or error message exactly as specified across consecutive turns or retry cycles.
  ```
  *Failure Mode*: When a passenger provides an invalid confirmation code on Turn 1 and clarifies on Turn 2, the model faces two mutually exclusive P0 constraints. It cannot repeat the exact string, yet is forbidden from changing it. This leads to instruction paralysis, random refusals, or hallucinated transfers.
* **Resolution**:
  * Strip blanket anti-repetition and manual anti-echoing mandates from prompt text (relying on platform native anti-echoing).
  * Localize rephrasing inside explicit multi-turn retry ladders (e.g., Strike 1, Strike 2) rather than imposing a global ban.

#### B. Tone Persona vs. Micro-Negative Conversational Bans
* **The Conflict**: The persona specifies an empathetic, warm customer service agent, but subsequent negative constraints forbid standard conversational acknowledgments and connective words.
* **Flight Booking Example**:
  ```markdown
  # CONFLICTING RULES IN SAME PROMPT:
  - <role>: You are an exceptionally polite, warm, and empathetic airline customer service agent.
  
  - FILLER PROHIBITION: You ARE STRICTLY FORBIDDEN from using filler phrases like "I understand",
    "One moment", "Sure", or "Okay" before delivering factual information or executing tool calls.
  ```
  *Failure Mode*: When a distressed passenger says *"My flight was delayed and I'm going to miss my daughter's graduation, can you please find me another flight?"*, the agent is barred from naturally acknowledging (*"I understand how stressful that is, let's see what flights we have available..."*) before calling `search_flights`. The resulting output is either an abrupt tool call with zero empathy or an awkward refusal.
* **Resolution**:
  * Strip negative conversational micro-bans on connective words (`"Sure"`, `"Okay"`, `"Let's see"`, `"I understand"`).
  * Let Director's Notes, audio profiles, and tool pacing directives govern conversational texture.

#### C. Dynamic Parameterization vs. Hardcoded Static Templates
* **The Conflict**: A global rule mandates dynamic branding or entity resolution, but specific subtask response templates hardcode a static string.
* **Flight Booking Example**:
  ```markdown
  # CONFLICTING RULES IN SAME PROMPT:
  - BRANDING FIDELITY (P0): You MUST dynamically use the operating airline name ({airline_name},
    e.g., "CymbalAir", "AeroGlobal") returned by get_flight_details. NEVER hardcode the carrier.
  
  - CHANGE CONFIRMATION: Say exactly: "I have submitted that flight change request to the airline
    reservations desk. We will send an updated itinerary to your email."
  ```
  *Failure Mode*: The model oscillates between obeying `BRANDING FIDELITY` and obeying the `Say exactly` template.
* **Resolution**:
  * Audit all response templates to ensure dynamic variables (e.g., `"{airline_name} reservations desk"`) are applied consistently across all subtask templates.

#### D. Global Loop Prohibitions vs. Local Escalation Protocols
* **The Conflict**: An arbitrary global turn-count cap conflicts with structured multi-strike verification or lookup flows.
* **Flight Booking Example**:
  ```markdown
  # CONFLICTING RULES IN SAME PROMPT:
  - REPETITIVE LOOP PROHIBITION: You ARE STRICTLY PROHIBITED from repeating the exact same question
    or error message more than 2 times in consecutive turns.
  
  - BAGGAGE TAG LOOKUP (3-Strike Ladder):
    - Strike 1: "I'm sorry, I couldn't find a bag matching that tag number. Could you double-check and provide it again?"
    - Strike 2: "I'm still not finding a record for that tag. Let's try one more time, or we can look up by confirmation code."
    - Strike 3: "I'm still having trouble locating your baggage tag. Let me connect you with our baggage service team."
  ```
  *Failure Mode*: On Turn 2, the blanket 2-turn prohibition triggers prematurely and aborts the flow or transfers before the Strike 2 alternative identifier or Strike 3 transfer tool can execute.
* **Resolution**:
  * Remove global turn-repetition limits. Rely exclusively on structured, stateful multi-strike ladders to govern escalation cleanly.

---

### 2.2 Scope 2: Contradictions with Global Instructions & Callbacks

These occur when an individual agent instruction conflicts with `global_instruction.txt` or session-level callback configurations:

* **Initial Greeting Conflicts vs. Mid-Call Transfers**: Global instructions deliver the initial greeting on Turn 1 (tracked via `{is_greeting_delivered}`). If a subagent instruction mandates *"Always greet the customer warmly by saying 'Thank you for calling CymbalAir, how may I help you today?'"*, the subagent will re-greet the customer after a mid-call transfer, looping the conversation.
* **Spoken Transfer Announcements vs. Silent Routing & Callbacks**: When `after_model_callbacks` or handoff tools (`escalation_call`, `call_wrap_up`) manage transfer audio and farewell phrasing, an agent prompt commanding the agent to speak transfer messages (e.g., *"Say 'Transferring you to a live agent now.'"*) causes double-speaking and conflicts with silent tool handoffs.
* **Fallback & Escalation Overrides**: Global instructions define enterprise-wide emergency escalation paths, but subagent instructions specify ad-hoc refusals or terminate calls directly without routing through the global escalation tool.
* **Resolution**:
  * Subagents must consume global session variables (e.g., `{is_greeting_delivered}`) and skip greetings after handoffs.
  * Subagent transitions must execute as silent tool calls (`{@TOOL: subagent_name}`); spoken transition phrasing belongs in callbacks or destination subagents.
  * Defer global intents (e.g., general transfers, silence handling, audio disconnections) to global instructions.

---

### 2.3 Scope 3: Contradictions with Tool Descriptions & Docstrings

These occur when an agent prompt's tool-calling directives clash with tool docstrings in `tools/*/python_function/python_code.py`:

* **Pre-Call Tool Pacing vs. Output Grounding Conflicts**:
  * *Agent Prompt*: *"Always call the `search_flights` tool, wait for its response, and then generate your response. You are strictly forbidden from speaking or outputting text that is not grounded in the tool output."*
  * *Tool Docstring*: *"Before calling this tool, speak a brief, natural conversational pacing phrase (e.g., 'Let me look up available flights for you...')."*
  * *Failure Mode*: The model cannot satisfy both directives simultaneously. It either calls the tool silently (causing dead air during API latency) or fails tool execution due to strict grounding constraints.
* **Tool Argument Restrictions & Schema Mismatches**:
  * *Agent Prompt*: Commands the model to pass a passenger phone number to `search_flights`.
  * *Tool Docstring / Schema*: Only accepts `origin_airport`, `destination_airport`, and `travel_date`.
* **Resolution**:
  * Harmonize agent instructions to explicitly permit pre-call conversational pacing phrases:
    > *"Before calling `search_flights`, speak a brief, natural pacing phrase. Once the tool returns, synthesize your final response grounded strictly in the tool payload."*
  * Ensure tool invocation instructions strictly match the declared schema parameters in `tool.json` and Python tool docstrings.

______________________________________________________________________

## 3. Instruction Cleaning & Prompt Hygiene Checklist

Use this actionable checklist when auditing and cleaning agent instructions (all items are High Priority):

- [ ] **Preserve Domain Logic & Taskflows (MANDATORY)**: Never delete, wipe, or strip existing business logic, validation rules, or negative operational constraints. Optimization must be strictly additive and restorative.
- [ ] **Harmonize Cross-Scope Contradictions**: Resolve conflicting directives between agent instructions, global instructions, callbacks, and tool docstrings (e.g., tool pacing vs. strict output grounding, spoken transfers vs. silent handoffs).
- [ ] **Eliminate "Say Exactly" vs. Anti-Repetition Deadlocks**: Remove blanket prompt-level repetition bans; specify explicit rephrasing variants within discrete retry ladders (Strike 1, Strike 2).
- [ ] **Strip Negative Conversational Micro-Bans**: Remove prohibitions against conversational connective words (`"Sure"`, `"Okay"`, `"One moment"`, `"Let's see"`, `"I understand"`) that impede natural speech and tool pacing.
- [ ] **Harmonize Dynamic Parameterization**: Ensure response templates consistently use dynamic variables (`{airline_name}`, `{flight_number}`) rather than hardcoded static fallbacks.
- [ ] **Decouple Global Loop Prohibitions from Local Escalation Ladders**: Remove blunt turn-count repetition caps; let deterministic multi-strike ladders govern escalation cleanly.
- [ ] **Prune Monolithic Few-Shot `<examples>` Debt**: Remove large legacy dialog transcripts from prompt text. Legacy examples accumulate format drift, violate newer negative operational rules, and bloat the context window. Maintain vetted golden test cases in evaluation suites instead.
- [ ] **Relocate Voice & Speech Directives to Director's Notes**: Strip all static voice styling, accent directives, vocal tone, delivery style, pronunciation, and `<voice_lock>` blocks from `instruction.txt`. Migrate them to `app.json` (`synthesizeSpeechConfigs`).
- [ ] **Sanitize Prohibited XML Tags Non-Destructively**: Rephrase internal platform tags strictly restricted to the prohibited list (`<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`, `<voice_lock>`, `<voice_output>`, `<state>`, `<transition>`, `<transitions>`, and legacy CamelCase tags like `<Agent>`, `<Role>`, `<Persona>`) into plain natural language descriptions without deleting the surrounding domain logic. Do not flag or modify standard taskflow XML tags outside this explicit list.
- [ ] **Replace Text Variable Mutations with Tools**: Remove text-based `"Set variable = value"` lines; use structured tool invocations (e.g., `update_booking_status`) to modify state.
- [ ] **Remove Redundant Platform Formatting Rules**: Remove instructions mandating plaintext, forbidding markdown headers, or instructing digit spacing; these are handled automatically by the platform baseline.
- [ ] **Enforce Explicit Tool Boundaries & Additive Pacing**: Ensure all latency-sensitive tools include clear pacing instructions and explicit `When to Call:` and `When NOT to Call:` boundaries in tool docstrings.
