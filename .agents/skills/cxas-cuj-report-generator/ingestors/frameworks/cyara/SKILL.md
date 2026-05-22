---
name: cxas-cyara-ingestor
description: "Extracts conversational transcripts from Cyara XML test case files."
---

# Cyara Ingestor Skill

Use this skill when you need to extract dialogue turns and conversation flows
from Cyara XML test case files.

## Protocol

Cyara files are XML documents containing test scenarios for voice or chat
agents. They are rich sources for transcripts because they map user utterances
to agent states.

1.  **Identify Test Cases**: Look for elements representing individual test
    cases using their unique identifiers.
2.  **Extract Utterances**: Extract the list of sample user utterances. These
    are valuable for generating realistic User turns.
3.  **Parse Expected Flow**: Locate the `expected_flow` sequence, which
    describes the chronological steps of the interaction.
4.  **Map Turns**:
    *   Iterate through the `expected_flow`.
    *   Look for steps that include `(User: ...)` annotations. This directly
        maps what the user said at that specific point in the flow.
    *   The step names (e.g., `Greeting_State`, `Ask_Identifier`) indicate the
        Agent's state or prompt. You must infer natural Agent dialogue based on
        these state names if literal text is not provided.
5.  **Extract Metadata**: Look for intent tags to understand the high-level
    classification.

## Example

A summarized Cyara item might look like this: `yaml id: test_case_identifier
utterances:

-   I need help with my account expected_flow:
-   Welcome_Prompt (User: I need help with my account) - Request_Account_ID`

You should infer a transcript like:

-   User: "I need help with my account."
-   Agent: [Triggers Welcome Prompt and recognizes intent, proceeding to Request
    Account ID]
