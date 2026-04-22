---
title: ConversationHistory
---

# ConversationHistory

`ConversationHistory` gives you access to the recorded conversation logs stored in CX Agent Studio. Each conversation captures the full turn-by-turn exchange — user utterances, agent responses, tool calls, tool results, agent transfers, and timing data.

You'll use this class when you want to mine real conversation data for testing (for example, generating realistic tool test inputs), review what happened in a specific session, or pull past conversation IDs to replay as historical context in new sessions.

## Quick Example

```python
from cxas_scrapi import ConversationHistory

app_name = "projects/my-project/locations/us/apps/my-app-id"
history = ConversationHistory(app_name=app_name)

# List recent conversations
conversations = history.list_conversations()
for conv in conversations:
    print(conv.name, conv.start_time)

# Get the full payload for one conversation
full_conv = history.get_conversation(conversations[0].name)
print(full_conv)

# Replay a past conversation as historical context in a new session
from cxas_scrapi import Sessions
sessions = Sessions(app_name=app_name)
session_id = sessions.create_session_id()

response = sessions.run(
    session_id=session_id,
    text="Continue from where we left off",
    historical_contexts=conversations[0].name,  # Pass the conversation ID
)
```

## Reference

::: cxas_scrapi.core.conversation_history.ConversationHistory
