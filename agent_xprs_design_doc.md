# Agent xprs (express)

## **Agentic Design Through Direct Conversational Graphs**

**Self Link:**  
**Tracking Bug:**   
**Status:** Draft  
**Author:** [Denis Calin](mailto:deniscalin@google.com), [Vishal Ghorpade](mailto:vghorpade@google.com)  
**Contributor:**  
**Approvers:**

## 

| \#begin-approvals-addon-section Username Role Status Last change  Approver 🟢 Pending Mar 13, 2026  Reviewer 🟢 Pending Apr 1, 2026  Reviewer 🟡 Pending Mar 13, 2026      ![][image1] For more information, see [go/g3a-approvals-reviewing](https://goto.google.com/g3a-approvals-reviewing)  |
| ----- |

## 1\. Build your interactive agentic surface

- `Agent xprs (express)` is a conversational designer that allows us to define the agent through composing a simple agent/user conversational graph for all possible use cases in the agent  
- A conversational graph is a simple directed graph that contains agent and user nodes, and represents the flow of the conversation between the agent and the user. Each agent can have one or more graphs, with each graph representing the critical user journeys the user can take during the conversation with the agent.  
- We can use a canvas-type drag and drop field to build these conversational graphs.  
- We need to build agent xprs as a service and a separate module that will be exposed in the HTML report tab, but might also be used independently.  
- High-level idea: to expose this inthe existing HTML report that gets generated after the migration, we can do the following:  
  - Move the report instantiation and surface its link to the user in the beginning of migration  
  - Create a new tab called Agent xprs designer that lets the user formalize/define the agent by specifying the agent/user utterances order, content and verbatim/generative selection.  
  - High-level: inside this tab we will have several pre-populated canvases with ordered agent/user utterances that the user can click into and edit and confirm manually.   
  - The outcome of this for migration: once the user confirms the conversational graphs/canvases, these are saved and will be used to drive the final optimization stage (we will repurpose Stage 2 (instructions and mock tools), or will create a new one). Basically, our migrated agent in the end MUST follow what the user defined in these conversational graphs.  
  - Here is how I think we can implement this service (be very critical and see if there is a more efficient and production-grade way to do this)  
    - We scan all utterances in the Tree View for the entire agent/selected resources  
    - In flows, we can take a look at Say: (we need to validate that (a) Tree View is correctly parsing everything possible in there), and in Playbooks we will need to look at instructions and Code Blocks  
    - Categorize into categories: greeting, authentication, re-prompt, general logic, general guardrails, wrap-up/goodbye, no match/no input. Attribute each utterance to the latest consolidated agent (for tracking/visibility)  
    - Pre-build canvases/conversation graphs for the user to edit  
    - During each migration, multiple canvases/conversation graphs will be prebuilt. We should be able to cycle through them by clicking on the mini-map of the conversation graphs (e.g. if there are 4 canvases that were pre-populated, there will be 4 small labeled tiles that the user can click on to switch between the canvases).  
    - The prebuilt quality should be really high, so that the user can quickly glance at each canvas and approve.  
    - Each prebuilt canvas will also be categorized: Core CUJ, No input, Unrelated Query, Attack (what are the other ones we might need to ensure we cover 100% of the Enterprise production-grade functionality?)  
    - Each canvas represents an end-to-end critical user journey that the agent must support. We need to see what is the best production-grade way possible to identify these (if we aren’t doing that already).  
    - We should also have the ability to add a new canvas (in case the service missed a critical journey, or the user wants to add something).  
    - On the left side we have the conversation graph with agent and user “bubbles”, which are editable fields. Each “bubble” has a toggle between Verbatim (icon: 100%)  and Generative (icon: Gemini sparkle)  
    - On the right hand side we have all of the utterance “bubbles” that were extracted from the Tree View of the agent. These are ordered by categories, starting with greeting, authentication, re-prompt, general logic, general guardrails, wrap-up/goodbye, no match/no input.  
    - The user is able to drag and drop these onto the canvas.  
    - The user is able to reorder the “bubbles” on the canvas.  
    - When the user toggles Verbatim on an agent “bubble”, that means that this utterance must be delivered by the target migrated agent exactly as it is here. This likely means that we will need to programmatically or through optimization stage implement the delivery of this utterance through a before\_model\_callback, or by adjusting the prompt to include the guidance like (just an example) “you MUST tell the user exactly like this: \_\_\_\_”  
    - When the user toggles Generative on an agent “bubble”, it means that the optimization stages might have certain degrees of freedom in optimizing this utterance, or breaking it up into several steps.  
    - The user “bubbles” represent possible inputs (chat/voice) entered by the user. If set to Verbatim, that means this is one of the expected utterances from the agent. If set to Generative, the agent builder can provide a description of what we expect from the user at this stage.

Questions/important points to consider:

1. When should we start the xprs process: as soon as the tree view is available (even before the migration is kicked off), or once we consolidate the agents in Stage 1B of optimization? Pros for starting early: since we already have access to the source agent data, we can pretty much extract and prefill the CUJ canvases. While the user is doing that, the migration process continues. After a certain point (after the consolidation at Stage 1B), the migration process will pause and wait for the user to finish designing the conversations before proceeding further.  
2. We need to think about the output of this canvas conversational designer. I think it can be a YAML file that, in a declarative way, defines the complete conversational graph of the agent. There has been something checked into Scrapi that is called Bella Notte, which is a slot filling framework that might be the declarative way we are looking for. We can consider integrating it into our migration pipeline. I believe it uses a similar file. It that will not cover our needs completely, let’s design a new file format to use.  
3. Once the file is ready, it will be used as the core blueprint for modifying the agent configuration (instructions, variables, Python tools, OpenAPI tools, callbacks), with best practices of the target platform (CXAS) in mind top ensure these conversations flow exactly as the user specified. Since we will have the migrated and newly synthesized prompts, deduped variables, tools, callbacks, we will use them as the base, and then very rigorously modify these configs to ensure the conversations, as they were designed in the canvas, will flow exactly like that. This should be done in optimization Stage 2, or in a new optimization stage we will add.  
4. The designed conversations will then be used as evals to ensure we can test the migrated agent behavior programmatically. Once discrepancies are recognized, we will start an update loop to make sure we align the agent with these evals. We will need to create a new module called AutoHillclimber (look through existing Scrapi code, do we already have something like this? Can agent-foundry be useful here?) that will be able to do both: (1) resolve cxas lint errors and warnings by updating the agent configuration (instructions, settings, variables, tools, callbacks) with absolutely minimal changes and (2) resolve these conversation eval errors and disparities by updating the agent configuration (instructions, settings, variables, tools, callbacks) with absolutely minimal changes. It should run after the agent is completely deployed in CXAS.
