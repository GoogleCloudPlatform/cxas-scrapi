# Product Requirements Document (PRD): Reference Agent - Sunday Mobile Lite

## 1. Overview
This document describes the requirements for a simplified reference agent,
**Sunday Mobile Lite**. The goal is to provide a clear specification of a
single-agent system demonstrating core virtual agent patterns like identity
verification, state-driven conversation, and external action invocation,
without relying on product-specific or library-specific terminology.

## 2. Goals

-   Define an actionable blueprint for a simple support agent.
-   Demonstrate secure handling of user data via mandatory identity verification.
-   Serve as a reference that can be implemented in various conversational AI platforms.

## 3. Core Concepts

-   **Session State**: Variables that persist across the conversation (e.g., `Account ID`, `Auth Status`, `Customer Name`).
-   **State Machine**: The logic governing the flow of conversation based on user input and system state.
-   **Actions (Tools)**: External functions the agent can invoke to interact with backend systems.

## 4. Scope & Features
The agent supports exactly three core user journeys:
1.  **Identity Verification**: Mandatory first step before any account details are accessed or modified.
2.  **Plan Inquiry**: Retrieve the user's current subscription plan.
3.  **Profile Update**: Update the user's contact email address.

## 5. Conversation Logic (State Machine)
The conversation should follow these logical states:

1.  **State: `Greet_And_Collect_ID`**
    -   **Trigger**: Conversation starts.
    -   **Action**: Greet user, ask for Account ID.
    -   **Transition**: Upon receiving ID, move to `Verify_Identity`.

2.  **State: `Verify_Identity`**
    -   **Action**: Retrieve account details using the ID. Ask the user to confirm their name against the retrieved record.
    -   **Transition**:
        -   If Name matches: Set `Auth Status` to "authenticated" and move to `Main_Menu`.
        -   If Name mismatch or error: Refuse access, offer escalation, and terminate or transfer.

3.  **State: `Main_Menu`**
    -   **Trigger**: User is authenticated.
    -   **Action**: Ask how to help. Listen for intents.
    -   **Transition**:
        -   Intent "Check Plan" -> `Provide_Plan`.
        -   Intent "Update Email" -> `Collect_New_Email`.

4.  **State: `Provide_Plan`**
    -   **Action**: Read plan from stored account details and state it to the user.
    -   **Transition**: Return to `Main_Menu` after asking if further help is needed.

5.  **State: `Collect_New_Email`**
    -   **Action**: Ask for new email. Upon receiving, invoke action to update email.
    -   **Transition**: Return to `Main_Menu` after confirming update.

## 6. Sample User Journeys (Critical User Journeys)

### Journey 1: Successful Plan Inquiry
1.  **User**: "Hi, I want to check my plan."
2.  **Agent**: "Hello! I can help you with that. Could you please provide your Account ID?"
3.  **User**: "My ID is 12345."
4.  **Agent**: "Thank you. To confirm, is the name on the account 'John Doe'?"
5.  **User**: "Yes, that's me."
6.  **Agent**: "Perfect. You are currently on the 'Unlimited Data' plan. Is there anything else I can help with?"

### Journey 2: Identity Verification Failure
1.  **User**: "What is my plan?"
2.  **Agent**: "I can help with that. Please provide your Account ID first."
3.  **User**: "ID is 12345."
4.  **Agent**: "Confirming, is the name on the account 'John Doe'?"
5.  **User**: "No, my name is Alice."
6.  **Agent**: "I'm sorry, I cannot access account details if the name does not match. Let me connect you with a human specialist."

## 7. High-Level Testing Scenarios

### Scenario 1: Happy Path Plan Inquiry

-   **Goal**: Verify that a user can successfully get their plan after verification.
-   **User Behavior**: Asks for plan, provides correct ID, confirms correct name.
-   **Success Criteria**: The agent must state the correct plan name and must not reveal it before verification.

### Scenario 2: Verification Failure Block

-   **Goal**: Verify that account info is not disclosed if verification fails.
-   **User Behavior**: Asks for plan, provides correct ID, provides *incorrect* name.
-   **Success Criteria**: The agent must refuse to provide the plan information and offer escalation.
