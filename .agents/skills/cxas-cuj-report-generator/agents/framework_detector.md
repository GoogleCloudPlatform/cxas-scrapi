
-   **Scoping Protocols**:

    Perform an expert manual and tool-based architectural inspection of the
    target requirements directory (using your standard `list_dir`,
    `code_search`, or `find` tools to crawl the folder tree):

    1.  **Scan for CX Agent Studio (CXAS) Layout**: Look for recursive
        occurrences of `app.json` + `global_instruction.txt` + `agents/` +
        `tools/` subdirectories. Confirm declarative configurations.
        1.  **Scan for Agent Development Kit (ADK) Layout**: Look for Python
            service structures (`pyproject.toml` containing poetry specs,
            `main.py` FastAPI entry points, `app/agents/` directory).
        2.  **Scan for Dialogflow CX (DFCX) Layout**: Look for extracted ZIP
            package folders (presence of a root folder containing `agent.json` +
            `flows/` + `intents/` + `webhooks/` directories).

