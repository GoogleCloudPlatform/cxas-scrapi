## Agent Review Results

### Metadata
- App directory: path to the agent app directory containing the agents.
- App name: the name of the app
- Review date and time: date and time the review was performed.

Repeat the following section for each agent:
<section>
### Agent n
- Agent Name: The name of the agent.
- Agent Path: The path to the agent file.

### Review Summary Table
Identify any logic issues in any prompt to the model in a markdown table with the columns listed below. One issue per line (if there are multiple issues in a file, output multiple rows). 

| Column | Format | Description |
| :--- | :--- | :--- |
| **reference** | `filename:lines:"snippet"` | The file name, line number range, and the text snippet where the issue occurs. Ensure the text snippet is complete and includes the entire phrase or sentence that causes the issue. |
| **issue_id** | `issue_id` | The ID of the issue category. |
| **issue_name** | `issue_name` | The issue name. |
| **severity** | `severity` | The severity of the issue. |
| **description** | `issue_description` | Description of how this specific issue manifests in the file. |
| **recommendation**| `recommendation` | Specific, actionable steps to resolve the issue. |

### Detailed Observations and Recommendations
Based on your analysis, provide detailed observations and recommendations.
</section>

## Linter Results
