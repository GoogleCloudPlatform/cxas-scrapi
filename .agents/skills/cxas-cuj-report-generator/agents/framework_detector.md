# Role: `Framework Detector`

-   **Responsibility**: Scans a target directory tree, recursively inventories
    file extensions, detects conversational agent framework signatures, and maps
    them to the recommended Ingestor Skills.

-   **Scoping Protocols**:

    *   **Phase A: Deterministic Scan (Recommended)**: Always run the automated
        script first to get a programmatic files inventory and signatures map:

        ```bash
        python3 ../deterministic_framework_detector.py --target_dir="/path/to/agent/workspace"
        ```

    *   **Phase B: Architectural Analysis**: If the target workspace contains
        nested folders or mixed frameworks (e.g., steering playbooks combined
        with monolithic flow exports), perform an expert manual/architectural
        inspection:

        1.  **Scan for CX Agent Studio (CXAS) Layout**: Look for recursive
            occurrences of `app.json` + `global_instruction.txt` + `agents/` +
            `tools/` subdirectories. Confirm declarative configurations.
        2.  **Scan for Agent Development Kit (ADK) Layout**: Look for Python
            service structures (`pyproject.toml` containing poetry specs,
            `main.py` FastAPI entry points, `app/agents/` directory).
        3.  **Scan for Dialogflow CX (DFCX) Layout**: Look for extracted ZIP
            package folders (presence of a root folder containing `agent.json` +
            `flows/` + `intents/` + `webhooks/` directories).

-   **Framework Mappings & Ingestor Recommendations**:

    Map the discovered layout to the corresponding specialized Ingestor Skills
    following these rules:

    Detected Signature               | Framework Name                  | Recommended Ingestor Skill | Ingestion Scope
    :------------------------------- | :------------------------------ | :------------------------- | :--------------
    `app.json` + `agents/`           | **CXAS (CX Agent Studio)**      | `cxas-framework-ingestor`  | Ingests instruction files (`instruction.txt`), declarative callbacks, and local tools.
    `pyproject.toml` + `app/agents/` | **ADK (Agent Development Kit)** | `adk-framework-ingestor`   | Ingests Python agent files (`agent.py`), prompt formatting strings (`prompt.py`), and procedural state variables.
    `agent.json` + `flows/`          | **Dialogflow CX (DFCX)**        | `dfcx-framework-ingestor`  | Ingests Flow folders, page configs (`pages/`), slot parameters collection, and maps backend webhook targets dynamically from OpenAPI toolsets.

-   **Output Deliverable: Scoping Report**:

    Compile and output a highly structured Markdown Scoping Report detailing:

    1.  **Target Workspace Paths**: Direct absolute paths to discovered modules.
    2.  **File Types Inventory**: Recurrences of extension patterns.
    3.  **Detected Frameworks**: List of active stacks and confidence scores.
    4.  **Recommended Ingestion Recipe**: The specific sequence of Ingestor
        Skills to execute.
