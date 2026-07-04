# Advanced Simulation Testing in CXAS Scrapi

With the recent additions of `historical_contexts` and `use_tool_fakes` to the Simulation Engine, you can now write robust, hermetic, and fast tests. This guide explains how these fields interact with the underlying Conversational Agents (CXAS) platform and how you can use them.

## 1. Using `use_tool_fakes`

**How it works:**
Setting `use_tool_fakes: true` in your simulation YAML does *not* mean you define the mock responses directly in the YAML file. Instead, it acts as a toggle switch that tells the CXAS backend: "Do not execute any live network calls or webhooks during this session. Instead, use the static mock responses defined on the agent."

**How to set up the fakes:**
1. **In the Agent Definition:** When building your CXAS Agent (either in the UI or via your `scrapi` codebase definitions), you can define standard "Mock Responses" for your tools. 
2. **In your `simulations.yaml`:**
   ```yaml
   name: "Check Order Status - Network Failure Simulation"
   use_tool_fakes: true
   steps:
     - goal: "Ask the agent for the status of order ID 9999"
       success_criteria: "The agent gracefully informs the user that the order system is down."
   ```

When the simulation hits the tool execution step, the platform intercepts it and returns the pre-configured mock (for example, a mock mimicking a `500 Internal Server Error`). Your test then verifies if the agent's LLM properly synthesized a polite apology instead of crashing.

*(Note: If you need to dynamically inject different mock responses per test step rather than using static platform mocks, you would use the `tool_responses` input parameter on the session, which handles mid-turn tool injection. `use_tool_fakes` is strictly for platform-managed static mocks).*

## 2. Using `historical_contexts`

**How it works:**
The CXAS platform allows passing a list of historical messages to seamlessly bootstrap a conversation's state. When `scrapi` sends this list on the first turn of the simulation, the CXAS agent processes it as if that conversation just happened, allowing you to instantly warp to a specific point in the conversational graph.

**How to use it:**
In your `simulations.yaml`, define the exact back-and-forth transcript required to reach your target sub-agent or conversational state.

```yaml
name: "Technical Support Sub-Agent - Hardware Issue"
historical_contexts:
  - user: "I am calling about a problem with my laptop."
  - agent: "Hello! How can I help you today?"
  - user: "I need help troubleshooting my display."
  - agent: "I can help with that. Is this a laptop or a desktop?"
  - user: "Laptop."
  - agent: "Transferring you to the Laptop Support specialist."
  # The simulation is now effectively inside the Laptop Support sub-agent!
steps:
  - goal: "Provide the device serial number."
    static_utterance: "The serial number is 123456789."
    success_criteria: "The agent recognizes the serial number and asks for the model year."
```

### When is it beneficial to use?
If your agent has a "Hub and Spoke" architecture with a central router and 15 different specialized sub-agents, you no longer need to write 15 different simulations that all redundantly test the central router's ability to transfer the user. You can unit-test the router once, and then use `historical_contexts` to bypass the router and unit-test the 15 sub-agents in total isolation.
