# CXAS Google Search Tool Configuration Guide

To enable your agent to search the web using Google Search, you must configure a
Google Search Tool.

## Tool Definition Schema

Create a JSON file under `tools/<tool_name>/<tool_name>.json`. The file must
follow this schema:

```json
{
  "name": "<tool_name>",
  "displayName": "<tool_name>",
  "googleSearchTool": {
    "name": "<tool_name>",
    "description": "A description of what this search tool does.",
    "preferredDomains": [
      "example.com"
    ]
  }
}
```

### Key Fields:

*   **`googleSearchTool`**: The root configuration block for the Google Search
    tool. This key is case-sensitive and must be exactly `googleSearchTool`.
*   **`preferredDomains`**: (Optional) A list of domains to restrict the search
    to. Highly recommended to ensure targeted search results.
*   **`displayName`**: Must match the `<tool_name>` exactly (use snake_case).

## Linking to Agent

To equip an agent with this tool, add the `<tool_name>` to the `tools` array in
the agent's JSON configuration (e.g., `agents/root_agent/root_agent.json`):

```json
{
  "name": "root_agent",
  "displayName": "root_agent",
  "tools": [
    "end_session",
    "<tool_name>"
  ],
  "instruction": "agents/root_agent/instruction.txt"
}
```
