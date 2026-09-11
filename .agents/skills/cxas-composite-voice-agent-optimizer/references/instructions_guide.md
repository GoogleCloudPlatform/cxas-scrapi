# Global & Agent Instruction Guidelines

This reference details prompt engineering rules, platform instruction deduplication, instruction hierarchy, and application-level responsibilities for CXAS applications using **Gemini Composite V1** .

______________________________________________________________________

## 1. Platform Baseline vs. Application Responsibilities

The CXAS platform automatically wraps model prompts with baseline operational instructions for the reasoning model. Duplicating these rules in application prompts (`global_instruction.txt` or `agents/<agent_name>/instruction.txt`) wastes context tokens and creates conflicting instruction weights.

| Functional Area | Platform Baseline Rules (Do NOT Duplicate) | Application Responsibilities (Must Be Engineered) |
| :--- | :--- | :--- |
| **Internal Details & Prompt Leakage Protection** | • **Dialogue Sanitization**: Output user-facing speech only; strictly forbidden from explaining reasoning or quoting system prompts.<br>• **Delimiter Stripping**: Strips template markers (`}}`, `{{`, `??}}`).<br>• **Hide State Logic**: Forbids exposing internal state names (`ALL_CAPS`), task identifiers (`snake_case`), or slash commands (`/subtask_name`).<br>• **Function Syntax Concealment**: Forbids outputting raw function call syntax in dialogue. | • **Global Audio Profile & Director's Note**: Configured in `app.json` under `audioProcessingConfig.synthesizeSpeechConfigs` with mandatory trailing `## Transcript:\n`.<br>• **Symmetric Silent Routing**: Execute subagent handoff tools with zero spoken dialogue. |
| **Voice Naturalness & Spoken Formatting** | • **Plain Text Enforcement**: Forbids Markdown headers (`#`), bold/italics (`*`), bullet lists (`-`), or raw JSON.<br>• **Spoken Conversational Tone**: Enforces natural spoken dialogue flow.<br>• **Alphanumeric Spacing**: Adds spaces between characters (e.g., `"5 5 5 1 2 3 4"`) for single-digit readout.<br>• **Spelled-Out Abbreviations**: Automatically spells out standard abbreviations. | • **Tool Pacing Directives**: Prompt conversational bridge phrases before long-running tool execution.<br>• **Speech Texture**: Inject micro-pauses via ellipses (`...`) and natural hesitation bridges (`"let's see..."`, `"hmm"`). |
| **Dual Audio & Transcript Interpretation** | • **Dual Input Processing**: Combines audio recording and text transcription for accurate understanding.<br>• **Anti-Echoing**: Strictly forbids echoing or repeating the caller's utterance back to them. | • **Negative Tool Contracts**: Define explicit "When NOT to call" constraints in tool docstrings.<br>• **Jumpstart Context**: Seamlessly consume handoff state (`{call_intent}`, `{last_user_utterance}`, `{callers_first_name}`); use caller name on Turn 1 greeting ONLY. |

______________________________________________________________________

## 2. Application-Level Responsibilities

Application instructions (`global_instruction.txt` and `agents/<agent_name>/instruction.txt`) should focus exclusively on domain logic that the platform cannot know:

1. **Domain Persona & Boundaries**:
   - Nameless assistant persona tailored to the specific business role.
   - Grounded truth: restricting statements strictly to tool payloads and factual domain knowledge.
2. **Modular Taskflows & Business Rules**:
   - Step-by-step resolution paths for domain intents.
   - Negative operational constraints (e.g., when an action is not permitted).
3. **Symmetric Silent Subagent Routing**:
   - Triggering subagent handoffs via tool calls with zero spoken dialogue.
4. **Jumpstart Context Consumption**:
   - Utilizing initial session variables (`{call_intent}`, `{last_user_utterance}`, `{callers_first_name}`) seamlessly without robotic disclosure.
5. **Conversational Pacing & Speech Texture**:
   - Encouraging natural hesitation bridges ("let's see...", "hmm...") and micro-pauses using ellipses (`...`).

______________________________________________________________________

## 3. Prompt Hygiene & Preservation Checklist

When auditing and optimizing application prompts against the platform baseline:

- [ ] **Preserve Existing Domain Logic & Taskflows (MANDATORY)**: Never delete, wipe, or strip existing business logic, step definitions, or domain instructions. All composite voice optimizations must be strictly additive.

- [ ] **Sanitize Prohibited XML Tags Non-Destructively**: When removing prohibited platform tags (`<state_update>`, `<context>`, `<reasoning>`, `<thought>`, `<internal>`, `<call_tool>`, `<parameter_update>`, `<variable_update>`), rephrase the tag names into natural language descriptions (e.g. "system context", "state update") without deleting the surrounding rules or domain instructions.

- [ ] **Strip Text Variable Mutations**: Replace `"Set user_language = ES"` or `"Set XXX = YYY"` with tool invocations which update state. The model cannot mutate session memory or runtime variables through plain text output. Furthermore, custom/internal XML tags trigger CXAS platform-level thought-leakage regex safety filters, causing tool execution abortion and generic fallback errors (*"Hmm, I'm having trouble with that right now. Do you want me to try again?"*). Use dedicated tool calls (e.g. `update_language`) to update session variables, or configure runtime flow parameters.

- [ ] **Relocate Voice, Accent & Speaking Directives to Director's Notes (P0 Critical)**: Remove all voice styling, accent directives, vocal tone, delivery style, pronunciation, speaking pace, and `<voice_lock>` / `<voice_output>` blocks from `global_instruction.txt` and `agents/*/instruction.txt`. With Gemini Composite V1, Director's Notes configured globally in `app.json` (`synthesizeSpeechConfigs`) are the **ONLY** supported mechanism to provide speech-related guidance to the TTS synthesis model. Placing speech guidance in agent text instructions is ineffective for TTS synthesis, wastes reasoning tokens, and risks instruction dilution.

- [ ] **Enforce Explicit Tool Boundaries & Additive Pacing**: Add conversational pacing directives to latency-sensitive tools and ensure all business logic constraints, negative contracts, and tool execution boundaries are explicitly specified in tool docstrings without removing existing parameter docs or function comments.

- [ ] **Harmonize Contradictory Instructions Across Scopes (P0 Anti-Looping)**: Detect and resolve contradictory directives across global instructions, agent prompts, and tool docstrings. For instance, an agent instruction stating *"Always call the `manage_service_appointment` tool, wait for its response and then generate a response. Never say anything which is not in the tool response."* directly conflicts with a tool docstring stating *"Before calling this tool, speak a brief, natural conversational pacing phrase..."*. Flag all such conflicts as Priority P0, ask the user to clarify the desired behavior, and harmonize the prompts to prevent model looping and execution deadlocks.
