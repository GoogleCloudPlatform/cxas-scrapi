---
name: cxas-steering-agent-creator
description: >-
  Create a strict 2-level CXAS steering agent hierarchy with stub/mock subagents
  from a DFCX agent export zip. The Level 1 Steering Agent (Root Playbook) routes
  semantically to Level 2 Subagents (Sub-Playbooks) containing all stub conversational
  logic in their instructions. Deployed as a complete package using the standard
  scrapi CLI command 'cxas push'. No DFCX flows are migrated or called, and there are
  no Level 3 transitions. If the number of intents is <= 30, it creates a 1-level
  structure (only the Root Playbook containing all stubs directly).
---

# CXAS Steering Agent Creator (CLI Push Mode)

This skill guides the AI agent to parse a Dialogflow CX (DFCX) agent export `.zip` file, analyze its intents, group them semantically (if there are $> 30$ intents) using its own reasoning, generate conversational stub instructions for each intent directly within a standard local CXAS folder structure, and deploy the entire application to GCP using the standard `cxas push` CLI tool.

## Architecture Guidelines (Strict 2-Level Max)

*   **$\le 30$ Intents (1-Level Structure)**: A single **Steering Agent (Root Playbook)** is created. It handles all intents directly in its own instructions with stub steps for each topic. No subagents are created (1-level structure).
*   **$> 30$ Intents (2-Level Structure)**: The intents are grouped into $N$ groups ($N \le 30$).
    *   **Level 1: Steering Agent (Root Playbook)**: Act as a semantic router. Its goal is to analyze user requests and transition to the appropriate subagent.
    *   **Level 2: Subagents (Sub-Playbooks)**: $N$ subagents are created (one for each group). All business logic stubs are written **directly inside the instructions of these Level 2 Subagents**. No third-level transitions or separate stub playbooks are allowed.

---

## Prerequisites & Environment

Ensure you run all python scripts and CLI commands within the local virtual environment:
```bash
# Path to virtual env python
.venv/bin/python3

# Path to virtual env cxas CLI
.venv/bin/cxas
```

Ensure GCP credentials are active:
```bash
gcloud auth application-default login
```

---

## Step-by-Step Execution Workflow

Follow these steps exactly to create the steering agent hierarchy:

### Step 1: Pre-collect Inputs
Ask the user for the following required details if not already provided:
1.  **GCP Project ID** (where the CXAS App will be deployed/created).
2.  **Location** (e.g. `us`, `global`, or other GCP region as appropriate for the project).
3.  **Source DFCX Agent Zip Path** (local path to the DFCX export `.zip` file).
4.  **Target App Name** (the display name for the new CXAS App).

> [!NOTE]
> It is up to you (the executing agent) to determine the best temporary or workspace paths to write any intermediate files (such as the layout JSON and the generated local structure folder) before deployment.

### Step 2: Parse Intents from DFCX Zip
Run the parsing script to extract all intents and sample training phrases:
```bash
.venv/bin/python3 \
  .agents/skills/cxas-steering-agent-creator/scripts/parse_dfcx_zip.py \
  --zip-path "<PATH_TO_ZIP>"
```
*Read the JSON output from this command. It contains the list of intent names and their training phrases.*

### Step 3: Analyze and Cluster (LLM Reasoning in Context)
Analyze the list of parsed intents:

1.  **Filter Contextual & Utility Intents**: Identify and exclude generic contextual, slot-filling, or local utility intents that are not genuine conversational entry points. This includes:
    *   System default intents (e.g., `Default Welcome Intent`, `Default Negative Intent`).
    *   Yes/No confirmations or mid-dialog control slots (e.g., intents trained on simple inputs like 'yes', 'no', 'cancel', 'go back', 'more time', or inputs capturing individual zip codes, dates, numbers, or account validation slot entries).
    *   *Design Pattern Note*: Large enterprise agents often organize these utility slots under specific naming prefixes, but you must evaluate them semantically based on their training phrases to verify if they are local utility handlers rather than main topic entry points.
2.  **Count the remaining steering intents**.
3.  **If Total Intents $\le 30$**:
    *   No subagents are needed. You will write a **1-level structure**.
4.  **If Total Intents $> 30$**:
    *   **Cluster them into $N$ groups ($N \le 30$)** semantically using your own reasoning. Each group must represent a cohesive topic (e.g. `BillingManagement`, `TechnicalSupport`, `AccountSecurity`).
    *   Assign each group a safe alphanumeric display name (e.g. `PaymentsSubagent`).
    *   Write a clear, high-level description for each group summarizing the domain it covers (used by the root agent for routing).

### Step 4: Generate Playbook Instructions (Best-Practices XML Layout)

To improve instruction-following and accuracy, all playbook instructions MUST be structured using standard XML elements: `<role>`, `<persona>`, `<constraints>`, `<guidelines>`, `<taskflow>`, `<subtask>`, `<step>`, `<trigger>`, `<examples>`, and `<action>`.

**CRITICAL RULES FOR PLAYBOOK ARCHITECTURE**:
*   **Use `<constraints>` Structural Blocks**: Always define a `<constraints>` block right after `<persona>` in both root and child agents.
    *   *Silent Agent Transfers (Steering Router)*: Define constraint name `"Silent Agent Transfers"`. State that the agent is strictly forbidden from generating conversational transition filler words (e.g., "One moment, let me connect you") during a transfer. The transfer route must be silent.
    *   *End Session Guardrail (Subagents)*: Define constraint name `"End Session Guardrail"`. State that the agent must always verify that the user needs absolutely no further assistance with *any* other issues before executing an `end_session` call.
*   **Embed Turn-End Guards (`**STOP:**`)**: To prevent the LLM from "hallucinating" replies or executing parallel actions in a single turn, terminate all conversational prompts or transfers with explicit guards:
    *   After prompting a user: `- Say: [Question]. **STOP:** Wait for the user to reply.`
    *   After executing transfers: `- Transfer the conversation to {@AGENT: SubagentName}. **STOP:** Stop generating text immediately.`
*   **Handle Overlapping & Vague Inputs (Router Disambiguation)**:
    When a user request overlaps multiple subagent domains (e.g., mentions keywords from both Billing and Payments), the Steering Router's `<taskflow>` must resolve it cleanly:
    *   *Direct Routing Shortcuts (Bypass Clarification)*: Define explicit priority shortcuts to transfer the user directly without asking questions when a specific combination of words points to one specialist (e.g., if the word "late" or "extension" appears anywhere near "bill" or "invoice", transfer directly to `PaymentsSubagent` rather than treating it as an ambiguous overlap with `BillingSubagent`).
    *   *Implement `Handle_Domain_Disambiguation` Step*: For genuine overlaps that cannot be bypassed by a shortcut, the Steering Router's `<taskflow>` must contain an explicit `Handle_Domain_Disambiguation` step. Use this step to ask a single, highly targeted, succinct clarifying question (e.g., "I can connect you to our Billing team to review that charge, or our Payments team to submit a one-time payment. Which one of those would you like to focus on first?") before routing.

**CRITICAL RULES FOR STEP DEFINITIONS**:
*   **Do NOT combine all intents into a single large `<action>` step** using nested IF-ELSE text blocks.
*   **Split each intent into a separate, independent `<step>` block** inside the `<subtask>` tag.
*   **Keep `<trigger>` clean**: Define a short, clean, natural description of the target action (e.g., `User wants to update their profile.`). **Do NOT include raw intent names (like 'account_update_profile') or system parameters in the trigger string.**
*   **Discard Multi-line/Developer Notes**: Raw DFCX descriptions often contain multi-line bullet points, developer notes, changelogs, or trailing lists. You MUST discard all trailing lines and take ONLY the first active-voice sentence for both the `<trigger>` and `<action>` step definitions to keep them grammatically correct and concise.
*   **Use the `<examples>` tag**: Place 5 clean, representative user training utterances (parsed from the DFCX training phrases, with any customer names genericized!) inside individual `<example>` tags under each step.
*   Convert raw intent tags to friendly, Title-Cased, human-readable names (e.g., `update_user_profile` $\rightarrow$ `Update User Profile`) for the step names.

#### Scenario A: 1-Level Structure ($\le 30$ Intents)
Generate standard multi-step XML-structured instructions for the single **Root Playbook**:

```xml
<role>
You are the main Steering Agent (Root Playbook) operating in a conversational MOCK/STUB simulation mode. Your goal is to handle all user requests by providing helpful mock steps.
</role>

<persona>
- Be professional, warm, clear, and empathetic.
- Always remain helpful, reminding the user politely of the mock environment while simulating the dialogue.
</persona>

<guidelines>
  <guideline name="mock_simulation">
    You are currently operating in a conversational MOCK/STUB simulation mode. DO NOT connect to real external systems or execute actual operations.
  </guideline>
  <guideline name="stub_response_pattern">
    ALWAYS acknowledge the user's specific request, explain that the feature is a simulation, and ask if they need anything else in mock mode.
  </guideline>
</guidelines>

<taskflow>
  <subtask name="Intent_Handling">
    <step name="Update_User_Profile">
      <trigger>User wants to update their user profile details.</trigger>
      <examples>
        <example>change my contact info</example>
        <example>update my phone number</example>
      </examples>
      <action>
        1. Acknowledge: Greet and politely acknowledge their specific request to update their user profile details.
        2. Notice: Explicitly say: "I'd be happy to help you with Update User Profile, but please note that I am currently running in a mock/stub simulation mode for this specific feature."
        3. Check: Ask: "Is there anything else I can help you with in mock mode today?"
      </action>
    </step>
    
    <step name="Reset_Password">
      <trigger>User wants to reset their account password.</trigger>
      <examples>
        <example>forgot my password</example>
        <example>need to change password</example>
      </examples>
      <action>
        1. Acknowledge: Greet and politely acknowledge their request to reset their password.
        2. Notice: Explicitly say: "I'd be happy to help you with Reset Password, but please note that I am currently running in a mock/stub simulation mode for this specific feature."
        3. Check: Ask: "Is there anything else I can help you with in mock mode today?"
      </action>
    </step>
  </subtask>
</taskflow>
```

#### Scenario B: 2-Level Structure ($> 30$ Intents)

##### 1. Level 2 Subagent (Sub-Playbook) Stub Instructions:
For each group, generate multi-step XML playbook instructions where each intent is a separate `<step>` containing pure triggers and explicit structured `<examples>`:

```xml
<role>
You are the [SubagentName] subagent operating in conversational MOCK/STUB simulation mode. Your primary goal is to handle user requests related to: [Domain Description].
</role>

<persona>
- Be professional, warm, clear, and empathetic.
- Always remain helpful, reminding the user politely of the mock environment while simulating the dialogue.
</persona>

<guidelines>
  <guideline name="mock_simulation">
    You are currently operating in a conversational MOCK/STUB simulation mode for all user tasks. DO NOT connect to real external systems or execute actual operations.
  </guideline>
  <guideline name="stub_response_pattern">
    ALWAYS acknowledge the user's specific request, explain that the feature is a simulation, and ask if they need anything else in mock mode.
  </guideline>
</guidelines>

<taskflow>
  <subtask name="Intent_Handling">
    <step name="Make_Payment">
      <trigger>User wants to make a payment on their bill.</trigger>
      <examples>
        <example>pay my bill</example>
        <example>submit payment</example>
      </examples>
      <action>
        1. Acknowledge: Greet and politely acknowledge their specific request to make a payment.
        2. Notice: Explicitly say: "I'd be happy to help you with Make Payment, but please note that I am currently running in a mock/stub simulation mode for this specific feature."
        3. Check: Ask: "Is there anything else I can help you with in mock mode today?"
      </action>
    </step>
    
    <step name="Cancel_Autopay">
      <trigger>User wants to cancel their pre-authorized automatic payments.</trigger>
      <examples>
        <example>stop autopay</example>
        <example>remove auto debit</example>
      </examples>
      <action>
        1. Acknowledge: Greet and politely acknowledge their request to cancel auto-payments.
        2. Notice: Explicitly say: "I'd be happy to help you with Cancel Autopay, but please note that I am currently running in a mock/stub simulation mode for this specific feature."
        3. Check: Ask: "Is there anything else I can help you with today?"
      </action>
    </step>
  </subtask>
</taskflow>
```

##### 2. Level 1 Steering Agent (Root Playbook) Instructions:
Write standard XML instructions for the central router playbook. Use the **special target agent transitions syntax**: `{@AGENT: SubagentName}` to direct transitions inside the actions block:

```xml
<role>
You are the main Steering Agent (Root Playbook). Your primary goal is to analyze the user's conversational request and transfer them to the appropriate Level 2 subagent that handles their domain.
</role>

<persona>
- Be professional, warm, helpful, and concise.
- Speak directly and route efficiently without unnecessary small talk.
</persona>

<constraints>
  <constraint name="Silent Agent Transfers">
    Whenever a step instructs you to transfer to another playbook, you MUST execute the handoff immediately. You are STRICTLY FORBIDDEN from generating conversational transition phrases, filler words (e.g., "One moment," "I am connecting you"), or follow-up questions. The transfer must be completely silent.
  </constraint>
  <constraint name="Address User Preferences">
    If the user explicitly objects to being routed or asks for direct help, acknowledge their preference politely and explain why a specialist subagent is required before proceeding with the transfer.
  </constraint>
</constraints>

<guidelines>
  <guideline name="no_direct_handling">
    DO NOT attempt to answer specific business questions or perform tasks yourself. Always route the user to the correct child subagent.
  </guideline>
  <guideline name="small_talk">
    For basic greetings, simple small talk, or general thanks, respond politely directly without transferring (e.g. "Hello! How can I help you today? I can route your request to our Accounts, Billing, Payments, Tech Support, Sales, or General Info subagents.").
  </guideline>
  <guideline name="clarification">
    If the user's request is ambiguous and does not map clearly to any subagent, ask a polite clarifying question first before transferring.
  </guideline>
</guidelines>

<taskflow>
  <subtask name="Domain_Routing">
    <step name="Route_To_Subagent">
      <trigger>User describes their request.</trigger>
      <action>
        1. If the user's request is related to [Group 1 Domain Description]:
           - Transfer the conversation to {@AGENT: Subagent1Name}. **STOP:** Stop generating text immediately.

        2. If the user's request is related to [Group 2 Domain Description]:
           - Transfer the conversation to {@AGENT: Subagent2Name}. **STOP:** Stop generating text immediately.
        ...
        3. If the request matches multiple overlapping subagent domains:
           - Go to step name="Handle Domain Disambiguation".
      </action>
    </step>

    <step name="Handle Domain Disambiguation">
      <trigger>The user request overlaps multiple subagent responsibilities after initial analysis.</trigger>
      <action>
        1. Ask: Greet and ask a highly targeted, succinct clarifying question to resolve the overlap (e.g., "I can connect you to our Billing team to review that charge, or our Payments team to submit a one-time payment. Which one of those would you like to focus on first?").
        2. **STOP:** End your turn immediately and wait for the user to respond.
        3. On user reply, go back to step name="Route_To_Subagent".
      </action>
    </step>
  </subtask>
</taskflow>
```

---

### Step 5: Present the Plan & Groups to the User
Before generating local files, present the proposed structure to the user in the chat:
*   **If 1-Level**: Confirm a single-agent layout with all stubs directly inside it.
*   **If 2-Level**: Show the clean list of proposed Subagents, their intent counts, and high-level responsibilities.
*   Ask for their approval to proceed with local files generation and deployment.

### Step 6: Assemble Layout YAML
Write a temporary layout YAML file to a local path of your choice (e.g., `<path_to_layout_yaml>`) describing the complete designed hierarchy in standard YAML.

Use the literal block scalar (`instruction: |`) to write the raw, multi-line XML playbook content directly with perfect indentation, requiring **absolutely no double-quote or newline character escaping**!

#### Scenario A: 1-Level Structure Layout Schema ($\le 30$ intents)
```yaml
app_name: "<TARGET_APP_NAME>"
root_agent:
  display_name: "<ROOT_PLAYBOOK_NAME>"
  instruction: |
    <INSERT_ROOT_PLAYBOOK_XML_INSTRUCTIONS_FROM_STEP_4_SCENARIO_A>
  model: "gemini-2.5-flash"
subagents: []
```

#### Scenario B: 2-Level Structure Layout Schema ($> 30$ intents)
```yaml
app_name: "<TARGET_APP_NAME>"
root_agent:
  display_name: "<ROOT_STEERING_AGENT_NAME>"
  instruction: |
    <INSERT_ROOT_STEERING_PLAYBOOK_XML_INSTRUCTIONS_FROM_STEP_4_SCENARIO_B_2>
  model: "gemini-2.5-flash"
subagents:
  - display_name: "PaymentsSubagent"
    instruction: |
      <INSERT_SUBAGENT_PLAYBOOK_XML_INSTRUCTIONS_FROM_STEP_4_SCENARIO_B_1>
    model: "gemini-2.5-flash"
```
*(Ensure standard space-based YAML indentation is strictly followed for the block scalar layout)*

### Step 7: Generate Local CXAS App Structure
Run the generic generation script:
```bash
.venv/bin/python3 \
  .agents/skills/cxas-steering-agent-creator/scripts/generate_local_app.py \
  --layout-path "<PATH_TO_LAYOUT_YAML>" \
  --output-dir "<TARGET_OUTPUT_DIR>"
```
*This will create the root `app.json`, the `agents/` directory, individual `<agent>.json` metadata files, and their respective `instruction.txt` files (with empty `childAgents` for the 1-level scenario).*

### Step 8: Deploy via Standard CLI ('cxas push')
Deploy the generated local application structure using the standard `cxas push` command:
```bash
.venv/bin/cxas push \
  --app-dir "<TARGET_OUTPUT_DIR>" \
  --display-name "<TARGET_APP_NAME>" \
  --project-id "<PROJECT_ID>" \
  --location "<LOCATION>"
```

### Step 9: Complete and Report
Once the CLI command completes successfully, print the final deployment details and target Google Cloud Console URL for the user to verify!
---
