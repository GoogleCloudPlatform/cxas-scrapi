    `protocols/cxas-protocol-two-phase-ingestion/`.
*   **Checklist Mandate**: The orchestrator and all subagents MUST follow the
    `agent-protocol-checklist` protocol to maintain a local
    `task_checklist.json` file, ensuring they track their progress and not lose
    coverage during execution.
*   **Orchestrator Delivery Assurance [NEW MANDATE]**: The orchestrator MUST act
    as a strict, independent Delivery Auditor. BEFORE closing subagents,
    terminating the watchdog, or reporting campaign success to the user, the
    orchestrator MUST physically verify the existence, size bounds, and schema
    compliance of all registered deliverables (specifically
    `gecx_customer_report.html` and `gecx_cuj_report.html`) on disk. Under no
    circumstances may the orchestrator assume completion without executing a
    physical file-presence check.
*   **Auditing**: The orchestrator MUST periodically check the subagent's
    scratch directory to ensure the `task_checklist.json` file is being created
    and maintained. If the file is missing or not updated, the orchestrator MUST
        Account Number or Order ID are checked in a backend system immediately
        after being provided by the user, and insert a `webhook_call` or
        `tool_call` accordingly.
    *   **Agent-First Transcripts**: Every single transcript MUST start with a
        standard welcome greeting: *"Hello! Thanks for calling [Brand]. How can
        I help you today?"* (or a generic welcoming if no brand is specified,
        e.g. *"Hello! Thanks for calling. How can I help you today?"*) with
        absolutely no exceptions or alternative phrasing, even if raw
        requirements suggest another name.
    *   **Voice Realism (No Spoken URLs)**: Agents on the voice channel cannot
        speak long URLs. You MUST NEVER write raw URLs (e.g., `https://...`) in
        Agent turns. Instead, the Agent must verbally state they are texting or
        emailing the link (e.g., *"I've texted that tracking link to your
        phone"*).
    *   **Standardized End Session**: Every conversation MUST close with a
        structured 3-turn sign-off sequence:
        1.  Agent: *"Is there anything else I can help you with today?"*
        2.  User: *"No, that's all. Thank you."*
        3.  Agent: *"Thank you for calling [Brand]! Goodbye."* (or equivalent
            brand sign-off, e.g., *"Thank you for calling Customer Support!
            Goodbye."*, or *"Thank you for calling! Goodbye."* if no brand is
            specified) with absolutely no alternative phrasing allowed. The
            final Agent turn MUST trigger the `end_session` system tool call. Do
            NOT omit this tool call under any circumstances. It must match this
            CXAS schema: `yaml tool_call: name: end_session payload:
            session_escalated: false reason: "Conversation completed
            successfully" response: result: "success"`
        ```
    *   **Dual Reports**: The agent MUST generate both a CUJ report (limiting
        examples to at most 3) AND a comprehensive full report (including all
        examples).
-   **Speaker:** Must be either `Agent` or `User`. Please ensure that function
    call turn comes immediately after a user turn.
-   **Text:** The literal string spoken.
-   **Root-Level Call Fields**: The `tool_call` (such as `end_session`) and
    `webhook_call` fields MUST be written at the root level of individual turn
    objects in the YAML transcript, and MUST NOT be nested under `enrichment` or
    any other parent key.
-   **Enrichment**:
    -   `intent_detected`: Specify the NLU intent if applicable.
    -   `system_action`: Use for state transitions or background logic.

## Linguistic & Voice Naturalness Standards

All generated spoken dialogue turns (Agent voice turns) MUST strictly adhere to
high-fidelity spoken voice standards. Subagents must ensure:

1.  **Numeric Voice Normalization**: Spoken Agent turns MUST NOT contain raw
    digits, formatted currencies, or punctuation symbols representing numbers
    (e.g., do NOT write `"450"`, `"$909"`, `"555-0199"`). Instead, numbers must
    be explicitly spelled out phonetically:
    *   *Correct*: `"four hundred fifty points"`, `"nine hundred nine dollars"`.
    *   *IDs, Times, Order Numbers, Percentages, and Phone Numbers*: All numeric
        IDs, times, counts, reward points, percentages, or numbers of any kind
        must be written digit-by-digit or word-by-word phonetically with
        absolutely no punctuation or colon dividers: `"five five five, zero,
        one, nine, nine"`, `"seven thirty PM"`, `"eight o'clock PM"`, `"order
        number nine nine eight eight"`, `"twenty percent discount"`.
    *   *Scheduling Confirmation*: For any reservations or delivery updates that
        schedule or communicate a specific time, timeframe, or booking date
        (e.g., "ready in twenty minutes", "arrive in ten minutes", "booked for
        tomorrow at eight PM"), you MUST explicitly seek confirmation from the
        user (e.g. *"Is that okay?"*, *"Does that work for you?"*, or *"Should
        we proceed with that?"*).
2.  **Spoken Breath Span Limit**: Agent turns must remain concise, natural, and
    conversational. Individual spoken text blocks MUST NOT exceed **300
    characters** inside a single turn.
3.  **Vocabulary Smoothness**: Avoid robotic repetitions of the same long words
    (do not repeat the same word of length 5+ more than 4 times in a single
    turn).
4.  **Conversational Politeness**: Every Agent spoken turn MUST include at least
    one standard polite voice marker (`please`, `thank you`, `thanks`,
    `certainly`, `happy to help`, `welcome`, `goodbye`, `great day`, `my
    pleasure`, `certainly help`) to ensure a warm, non-robotic user experience.

## Execution Phase Details

During the Execution phase, subagents **MUST NOT** write directly to the
