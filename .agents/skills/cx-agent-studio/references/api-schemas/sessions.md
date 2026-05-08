# API Schemas: Sessions & Conversations

### RunSessionRequest
- **config** (-> SessionConfig): [required]
- **inputs** (array[-> SessionInput]): [required]

### RunSessionResponse
- **outputs** (array[-> SessionOutput])

### SessionConfig
- **inputAudioConfig** (-> InputAudioConfig)
- **outputAudioConfig** (-> OutputAudioConfig)
- **historicalContexts** (array[-> Message]): Override session history.
- **entryAgent** (string): Entry agent (default: root). Format: `projects/.../agents/{agent}`
- **timeZone** (string): User's IANA time zone.
- **useToolFakes** (boolean): Use fake tools.

### SessionInput
- **text** (string): User text.
- **dtmf** (string): DTMF digits.
- **audio** (string): Audio data.
- **toolResponses** (-> ToolResponses): Client tool results.
- **image** (-> Image)
- **blob** (-> Blob)
- **variables** (object): Session variables, keyed by name. Only declared variables used by CES.
- **event** (-> Event)

### SessionOutput
- **text** (string): Agent text.
- **audio** (string): Agent audio.
- **toolCalls** (-> ToolCalls): Tool requests for client.
- **endSession** (-> EndSession): Session ended.
- **payload** (object): Custom structured payload.
- **turnIndex** (integer): Turn number (from 1).
- **turnCompleted** (boolean): Agent finished this turn.

### EndSession
- **metadata** (object): Reason for ending.

### Message
- **role** (string): `user` or `agent`.
- **chunks** (array[-> Chunk])

### Chunk
- **text** (string)
- **transcript** (string): Audio transcript.
- **toolCall** (-> ToolCall)
- **toolResponse** (-> ToolResponse)
- **agentTransfer** (-> AgentTransfer)
- **updatedVariables** (object)

### Event
- **event** (string): [required] Event name.

### InputAudioConfig
- **audioEncoding** (enum: `LINEAR16` | `MULAW` | `ALAW`): [required]
- **sampleRateHertz** (integer): [required]

### OutputAudioConfig
- **audioEncoding** (enum: `LINEAR16` | `MULAW` | `ALAW`): [required]
- **sampleRateHertz** (integer): [required]

### Conversation
- **name** (string): Format: `projects/.../conversations/{conversation}`
- **turns** (array[-> ConversationTurn])
- **turnCount** (integer): Output only.
- **source** (enum: `LIVE` | `SIMULATOR` | `EVAL`): Output only.

### ConversationTurn
- **messages** (array[-> Message])
