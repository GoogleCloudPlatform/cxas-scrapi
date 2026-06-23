---
name: cxas-drawio-ingestor
description: "Extracts conversational transcripts from .drawio XML files."
---

# Draw.io Ingestor Skill

Use this skill when you need to extract dialogue turns and conversation flows
from `.drawio` diagram files.

## Protocol for Flowchart & Diagram Traversal

When processing `.drawio` flowchart designs, you must parse them as **complete
directed graphs** to construct dialogue paths, rather than extracting flat text
turns.

### 1. Parse Graph Structural Topology

*   Scan `<mxCell>` tags where `vertex="1"`. Extract their `id` and `value`
    (text label).
*   Scan `<mxCell>` tags where `edge="1"`. Match `source` and `target` vertex
    IDs to identify directed pathways.
*   Clean cell text by stripping internal HTML styling, tags, and formatting
    metadata (e.g., `&lt;div&gt;`, `<font>`, CSS styles). Unescape HTML entities
    (e.g., `&quot;` or `&lt;`) to obtain clean text.

### 2. Dialogue State & Path Traversal

*   Locate the start node (typically a top-level cell labeled "Start",
    "Welcome", or "Entry").
*   Perform a step-by-step traversal (DFS/BFS) along the directed edges. Each
    unique path from start to a terminal node constitutes a distinct
    conversation flow.
*   **State Mapping**:
    *   **Prompt Node**: Maps to an **Agent Turn** (using the node's cleaned
        label).
    *   **Directed Edge Label**: Maps to a **User Input** (representing speech
        utterances or DTMF keys that trigger that transition).
    *   **Computational Node**: Maps to a **System Action / Webhook** (e.g.,
        database lookups or validation calls).
    *   **Decision Diamond**: Represents logical branching.

### 3. Brand and Domain Consistency Mapping

Ensure that you map all domain concepts consistently to the target brand or
theme requested by the user or specified in the requirements. Do not mix
multiple industrial domains in a single report.

*   If the target brand has specific concepts, map requirements to fit that
    brand's services.
*   If no brand is specified, use clean, generic customer service phrasing
    suitable for the context.
*   Avoid using technical or backend-specific terminology in spoken Agent turns.

### 4. Format the Resulting CXAS Transcript

Compile the traversed paths into a structured CXAS YAML format complying with
the root-level turn structures (`turns` containing `speaker: Agent|User`,
`text`, and optional actions). Ensure every scenario starts with the
standardized Agent welcome greeting, is voice-realistic, and ends with the
standardized sign-off calling the `end_session` tool.

## Example Target Schema

```yaml
subintent_id: check_loyalty_status
subintent_name: "Check Loyalty VIP Status"
parent_cuj: "Loyalty Rewards"
turns:
  - speaker: Agent
    text: "Hello! Thanks for calling [Brand]. How can I help you today?"
  - speaker: User
    text: "I need to check my loyalty rewards point balance."
  - speaker: Agent
    text: "I'd be happy to help you check your loyalty rewards point balance. May I have your membership phone number?"
  - speaker: User
    text: "Yes, it is 555-0199."
  - speaker: Agent
    text: "Thank you. Let me verify your rewards balance."
    webhook_call:
      name: check_rewards_balance
      payload:
        phone_number: "5550199"
      response:
        vip_status: "ELITE"
        points_balance: 1500
  - speaker: Agent
    text: "I've found your account. You are an Elite member with a balance of 1,500 points. Is there anything else I can help you with today?"
  - speaker: User
    text: "No, that's all. Thank you."
  - speaker: Agent
    text: "Thank you for calling [Brand]! Goodbye."
    tool_call:
      name: end_session
      payload:
        session_escalated: false
        reason: "Conversation completed successfully"
```

## Linguistic & Voice Naturalness Standards

All generated spoken dialogue turns (Agent voice turns) MUST strictly adhere to
high-fidelity spoken voice standards. Subagents must ensure:

1.  **Numeric Voice Normalization**: Spoken Agent turns MUST NOT contain raw
    digits, formatted currencies, or punctuation symbols representing numbers
    (e.g., do NOT write `"450"`, `"$909"`, `"555-0199"`). Instead, numbers must
    be explicitly spelled out phonetically:
    *   *Correct*: `"four hundred fifty points"`, `"nine hundred nine dollars"`.
    *   *IDs and Phone Numbers*: Must be written digit-by-digit separated by
        spaces or commas: `"five five five, zero, one, nine, nine"`.
2.  **Spoken Breath Span Limit**: Agent turns must remain concise, natural, and
    conversational. Individual spoken text blocks MUST NOT exceed **300
    characters** inside a single turn.
3.  **Vocabulary Smoothness**: Avoid robotic repetitions of the same long words
    (do not repeat the same word of length 5+ more than 4 times in a single
    turn).
4.  **Conversational Politeness**: Agent turns must maintain standard polite
    voice markers (`please`, `thank you`, `thanks`, `certainly`, `happy to
    help`, `welcome`, `goodbye`, `great day`).
