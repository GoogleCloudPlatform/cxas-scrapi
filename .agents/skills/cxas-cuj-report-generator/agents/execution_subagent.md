# Role: `Execution Subagent`

-   **Responsibility**: Generates full natural conversation transcripts for a
    batch of sub-intents, strictly using the `append_turn.py` script to build
    them turn-by-turn.
-   **Prompting Guidance**: "Process the batch of items in file X. For each
    item, generate a full conversation. You MUST use `append_turn.py` for every
    turn to ensure schema compliance. Do not skip any items."
