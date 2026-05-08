## Category: Multi-Agent (MULT)

Issues pertaining to multi-agent workflows and interactions.

### MULT001: Uncontrolled multi-agent loop

**Severity:** Critical

**Description:** Deterministic (programmatic) or semantic logic that results in passing the user back and forth between two or more agents without making progress. 

**Remediation:** Clearly define the conditions for passing a request from one agent to another. Define the conditions for when a task is completed and no further handoffs should occur.

#### Invalid Example

```text
Based on condition x being true, Agent A will hand off to Agent B.
Agent B on completing it's task, sets condition x to true and returns to Agent A. 
Agent A upon receiving the request, sees that condition x is true and hands it back to Agent B. 

# At this point, a circular handoff is created and will continue until the request is completed or a safety stop is triggered
```