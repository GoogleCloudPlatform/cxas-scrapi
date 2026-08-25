# Contact Center AI Insights CEL Cookbook & Reference Guide

This document is a comprehensive reference for authoring Common Expression Language (CEL) rules for Google Cloud Contact Center AI (CCAI) Insights Autolabeling Rules.

______________________________________________________________________

## 1. Overview & Evaluation Semantics

Autolabeling rules evaluate incoming conversations in real-time or during batch ingestion.

- **Evaluation Order**: Conditions are evaluated from top to bottom (first-match-wins).
- **Fallback Rules**: The last condition should always have an empty condition `condition: ""` to act as the default fallback tag.
- **String Literals**: Values in CEL expressions or return values should be properly quoted (e.g. `'vip_tier'`, `"support"`).
- **Return Value**: The matched `value` expression is assigned to the conversation's `labels[label_key]`.

______________________________________________________________________

## 2. Conversation Attributes & Available Variables

When writing CEL condition expressions, the root `conversation` object is accessible with the following properties:

| Field                                      | Type                  | Description                           | Example                                                   |
| ------------------------------------------ | --------------------- | ------------------------------------- | --------------------------------------------------------- |
| `conversation.agent_id`                    | `string`              | ID or name of the virtual agent / app | `conversation.agent_id == 'billing_bot'`                  |
| `conversation.language_code`               | `string`              | BCP-47 language tag                   | `conversation.language_code == 'es-US'`                   |
| `conversation.duration`                    | `duration` / `int`    | Length of call/chat (seconds)         | `conversation.duration > 300`                             |
| `conversation.turn_count`                  | `int`                 | Total number of dialog turns          | `conversation.turn_count >= 10`                           |
| `conversation.labels`                      | `map[string, string]` | Pre-existing key-value labels         | `'vip' in conversation.labels`                            |
| `conversation.dialogflow_runtime_metadata` | `map`                 | Dialogflow / CXAS session metadata    | `conversation.dialogflow_runtime_metadata.session_params` |

______________________________________________________________________

## 3. Built-in CCAI Helper Functions

CCAI Insights provides specialized helper functions to inspect transcripts and runtime annotators:

### 3.1 Sub-Agent / Flow Traversal

Checks whether a specific GECX/CXAS sub-agent or Dialogflow flow participated in the conversation:

```cel
containsSubAgent(conversation, "billing_subagent")
```

```cel
containsSubAgent(conversation, "payment_flow")
```

### 3.2 Session Parameters & Context

Inspects session variables captured during the interaction (e.g., from tools or webhook responses):

```cel
hasSessionParam(conversation, "authenticated", "true")
```

```cel
hasSessionParam(conversation, "membership_tier", "platinum")
```

### 3.3 Sentiment Analysis

Checks sentiment score across conversation participants:

```cel
// Customer sentiment is negative (-1.0 to 1.0)
hasCallerSentiment(conversation, "NEGATIVE")
```

```cel
// Agent sentiment is positive
hasAgentSentiment(conversation, "POSITIVE")
```

### 3.4 Entity & Keyword Matching

Checks if specific entity types or phrase matchers fired:

```cel
hasEntity(conversation, "CREDIT_CARD_DISPUTE")
```

```cel
hasIntent(conversation, "cancel_subscription")
```

______________________________________________________________________

## 4. Common Recipes & Examples

### Recipe 1: Sub-Agent Routing & Containment

Classifies conversation category based on the sub-agents traversed during the session.

```yaml
- rule_id: "agent_domain"
  display_name: "Agent Domain Classifier"
  label_key: "agent_domain"
  label_key_type: "LABEL_KEY_TYPE_CUSTOM"
  active: true
  conditions:
    - condition: "containsSubAgent(conversation, 'billing_specialist') || containsSubAgent(conversation, 'invoice_lookup')"
      value: "'billing'"
    - condition: "containsSubAgent(conversation, 'tech_support') || containsSubAgent(conversation, 'troubleshooting')"
      value: "'tech_support'"
    - condition: "containsSubAgent(conversation, 'sales_inquiry')"
      value: "'sales'"
    - condition: ""
      value: "'general_inquiry'"
```

______________________________________________________________________

### Recipe 2: Customer Frustration & Escalation Risk

Flags conversations that exhibited negative caller sentiment, high turn count, or repeated clarification questions.

```yaml
- rule_id: "escalation_risk"
  display_name: "Escalation Risk Detector"
  label_key: "escalation_risk"
  active: true
  conditions:
    - condition: "hasCallerSentiment(conversation, 'NEGATIVE') && conversation.turn_count > 12"
      value: "'high_risk'"
    - condition: "hasCallerSentiment(conversation, 'NEGATIVE') || conversation.turn_count > 15"
      value: "'medium_risk'"
    - condition: ""
      value: "'low_risk'"
```

______________________________________________________________________

### Recipe 3: User Authentication Status

Tags whether the caller completed two-factor or step-up authentication during their journey.

```yaml
- rule_id: "auth_status"
  display_name: "User Authentication Status"
  label_key: "auth_status"
  active: true
  conditions:
    - condition: "hasSessionParam(conversation, 'auth_level', '2fa_verified')"
      value: "'authenticated_2fa'"
    - condition: "hasSessionParam(conversation, 'auth_level', 'pin_verified')"
      value: "'authenticated_pin'"
    - condition: "hasSessionParam(conversation, 'auth_attempted', 'true')"
      value: "'auth_failed'"
    - condition: ""
      value: "'unauthenticated'"
```

______________________________________________________________________

### Recipe 4: Interaction Complexity (Duration & Turns)

Buckets sessions by length and turn count to isolate quick self-service resolutions vs lengthy troubleshooting.

```yaml
- rule_id: "interaction_complexity"
  display_name: "Interaction Complexity Tier"
  label_key: "complexity"
  active: true
  conditions:
    - condition: "conversation.duration > 600 || conversation.turn_count > 20"
      value: "'very_complex'"
    - condition: "conversation.duration > 240 || conversation.turn_count > 8"
      value: "'moderate'"
    - condition: ""
      value: "'simple_quick'"
```

______________________________________________________________________

## 5. Authoring Best Practices

1. **Explicit Fallback**: Always ensure the final condition in every rule is `condition: ""` to guarantee deterministic labeling.
1. **Quoted Values**: Ensure all string literal values in the `value` field are single or double quoted (e.g. `'billing'` instead of `billing`).
1. **Keep Expressions Focused**: Use logical operators (`&&`, `||`, `!`) to combine checks cleanly rather than writing monolithic nested logic.
1. **Test via Dry-Run**: Always review diffs before deploying (`cxas insights diff-autolabel-rules` or `push-autolabel-rules --dry-run`).
