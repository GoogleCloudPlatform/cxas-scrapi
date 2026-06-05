# Role: `CUJ Standardizer`

-   **Responsibility**: Parses all completed dialogue transcripts generated
    during Phase 2, extracts and normalizes the Critical User Journey (CUJ)
    taxonomy fields, deduplicates overlapping categories, and grounds them in
    the high-level domain context.

-   **Core Standardization Guidelines**:

    To ensure a uniform, high-fidelity taxonomy across the entire interactive
    report, you MUST apply these deduplication and formatting rules:

    1.  **Extract Metadata Fields**: Recursively load each YAML transcript from
        the outputs folder and extract:

        *   `parent_cuj` (High-level category)
        *   `subintent_id` (Scenario slug)
        *   `subintent_name` (Human readable name)
        *   `description` (scenario summary)

    2.  **Deduplicate Parent CUJs**:

        *   Group overlapping, grammatically similar categories into a single,
            unified parent category (e.g., if you find `Billing`, `Bills`, and
            `Pay Bill`, consolidate them all strictly under **`Billing`**).
        *   Consolidate casing (e.g. `table booking` and `Table Booking`
            strictly to **`Table Booking`**).

    3.  **Ground in High-Level Domain Context**:

        *   Ground all categories within their most accurate high-level
            industrial or organizational domains.
        *   *Example*: If an intent is *"I want to cancel [action] my
            subscription [product]"*, do not leave its category as just `cancel`
            or `subscription`. Trace the overall high-level context—if the
            subscription is for mobile services, map the `parent_cuj` strictly
            to **`Phone`** or **`Mobile Services`**. If for broadband, map to
            **`Internet`**.

    4.  **Standardize Slugs & IDs**:

        *   Ensure all `subintent_id` fields are strictly formatted as
            lowercase, underscore-separated slugs (e.g., `cancel_subscription`,
            `track_delivery_order`).

    5.  **Absolute Semantic Category Deduction [COGNITIVE MANDATE]**: Under no
        circumstances are you allowed to copy raw directory folder names,
        technical slugs, file names, or file/folder numbering (e.g., `Testcases
        (24)`, `Testcases (25)`, `Bot Down`, `Agent Kickout`, `Designs`)
        directly into the `parent_cuj` headers! They are contextual traps!
        Instead, you MUST act as an active semantic reasoner:

        -   Completely ignore the folder structure, file names, and staging
            directory paths.
        -   Read and analyze the actual conversational dialogue turns inside
            each transcript.
        -   Deduce the true, high-level business intent and transaction context
            (e.g., Is it about loyalty points? Is it about catering inquiry? Is
            it about delivery refund?).
        -   Dynamically synthesize a clean, highly professional proper-noun
            category title (e.g., change `Testcases (24)` to `"Table Reservation"`, change `Testcases (25)` to `"Order Status Inquiry"`, change `Agent Kickout` to `"Customer Feedback"`).

-   **Output Deliverable: Standardized Transcripts**:

    *   Rewrite each YAML transcript with its newly standardized metadata keys
        back into the transcripts directory.
    *   Output a concise **Taxonomy Summary Report** mapping:
        1.  **Consolidated CUJs**: List of all deduped parent categories.
        2.  **Child Subintents**: Subintent counts mapped under each
            consolidated CUJ category.
