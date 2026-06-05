"""Callback library datamodels and utilities."""

# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import json as json_lib
from typing import Any

import pydantic


# Util
def is_json_serializable(value: Any) -> bool:
    """Returns true if it can get a JSON dump of the value."""
    try:
        _ = pydantic.TypeAdapter(type(value)).dump_json(value)
        return True
    except TypeError:
        return False


# Types
class BaseModel(pydantic.BaseModel):
    """Base class for the pydantic model."""

    model_config = pydantic.ConfigDict(
        arbitrary_types_allowed=True,
        extra="ignore",
        populate_by_name=True,
    )


class Blob(BaseModel):
    """Datamodel for the blob content."""

    display_name: str | None = pydantic.Field(default=None, alias="displayName")
    data: bytes | None = pydantic.Field(
        default=None,
    )
    mime_type: str | None = pydantic.Field(default=None, alias="mimeType")
    transcript_field: str | None = pydantic.Field(
        default=None, alias="transcript"
    )

    @property
    def raw_data(self) -> bytes:
        return base64.b64decode(self.data)

    @raw_data.setter
    def raw_data(self, value: bytes) -> None:
        self.data = base64.b64encode(value)

    def transcript(self) -> str | None:
        return self.transcript_field

    @classmethod
    def from_json(cls, data: str) -> "Blob":
        return cls(
            data=base64.b64encode(data.encode("utf-8")),
            mime_type="application/json",
        )


class FunctionCall(BaseModel):
    """Datamodel for the function call."""

    id: str | None = pydantic.Field(
        default=None,
    )
    args: dict[str, Any] | None = pydantic.Field(
        default=None,
    )
    name: str | None = pydantic.Field(
        default=None,
    )


class FunctionResponse(BaseModel):
    """Datamodel for the function response."""

    will_continue: bool | None = pydantic.Field(
        default=None, alias="willContinue"
    )
    id: str | None = pydantic.Field(
        default=None,
    )
    name: str | None = pydantic.Field(
        default=None,
    )
    response: dict[str, Any] | None = pydantic.Field(
        default=None,
    )


class Part(BaseModel):
    """Datamodel for the part."""

    function_call: FunctionCall | None = pydantic.Field(
        default=None, alias="functionCall"
    )
    function_response: FunctionResponse | None = pydantic.Field(
        default=None, alias="functionResponse"
    )
    text: str | None = pydantic.Field(
        default=None,
    )
    inline_data: Blob | None = pydantic.Field(default=None, alias="inlineData")

    def text_or_transcript(self) -> str | None:
        if self.text is not None:
            return self.text
        if self.inline_data is not None:
            return self.inline_data.transcript()
        return None

    def has_function_call(self, name: str) -> bool:
        return (
            self.function_call is not None and self.function_call.name == name
        )

    def has_function_response(self, name: str) -> bool:
        return (
            self.function_response is not None
            and self.function_response.name == name
        )

    @classmethod
    def from_text(cls, text: str) -> "Part":
        return cls(text=text)

    @classmethod
    def from_function_call(cls, name: str, args: dict[str, Any]) -> "Part":
        function_call = FunctionCall(name=name, args=args)
        return cls(function_call=function_call)

    @classmethod
    def from_function_response(
        cls, name: str, response: dict[str, Any]
    ) -> "Part":
        function_response = FunctionResponse(name=name, response=response)
        return cls(function_response=function_response)

    @classmethod
    def from_inline_data(cls, data: bytes, mime_type: str) -> "Part":
        return cls(inline_data=Blob(data=data, mime_type=mime_type))

    @classmethod
    def from_json(cls, data: str) -> "Part":
        return cls(inline_data=Blob.from_json(data=data))

    @classmethod
    def from_audio(
        cls,
        audio_uri: str,
        cancellable: bool = False,
        interruptable: bool = True,
    ) -> "Part":
        data = json_lib.dumps(
            {
                "audioUri": audio_uri,
                "cancellable": cancellable,
                "interruptable": interruptable,
            }
        )
        return cls(
            inline_data=Blob(
                data=base64.b64encode(data.encode("utf-8")),
                mime_type="application/json+audio",
            )
        )

    @classmethod
    def from_agent_transfer(cls, agent: str) -> "Part":
        function_call = FunctionCall(
            name="transfer_to_agent", args={"agent_name": agent}
        )
        return cls(function_call=function_call)

    @classmethod
    def from_end_session(
        cls, *, reason: str, escalated: bool = False
    ) -> "Part":
        function_call = FunctionCall(
            name="end_session",
            args={"reason": reason, "session_escalated": escalated},
        )
        return cls(function_call=function_call)

    @classmethod
    def from_customized_response(
        cls,
        *,
        content: str = "",
        disable_barge_in: bool = False,
        enable_dtmf: bool = False,
        dtmf_finish_digit: str = "#",
        dtmf_endpointing_timeout: int = 3,
    ) -> "Part":
        data = json_lib.dumps(
            {
                "content": content,
                "disableBargeIn": disable_barge_in,
                "enableDtmf": enable_dtmf,
                "dtmfFinishDigit": dtmf_finish_digit,
                "dtmfEndpointingTimeout": dtmf_endpointing_timeout,
            }
        )
        return cls(
            inline_data=Blob(
                data=base64.b64encode(data.encode("utf-8")),
                mime_type="application/json+agentResponse",
            )
        )


class Content(BaseModel):
    """Datamodel for the content."""

    parts: list[Part] | None = pydantic.Field(
        default=None,
    )
    role: str | None = pydantic.Field(
        default=None,
    )

    def is_user(self) -> bool:
        return self.role == "user"

    def is_model(self) -> bool:
        return self.role == "model"


class FunctionDeclaration(BaseModel):
    """Datamodel for the function declaration."""

    name: str | None = pydantic.Field(
        default=None,
    )


class ToolDeclaration(BaseModel):
    """Datamodel for the tool declaration."""

    function_declarations: list[FunctionDeclaration] | None = pydantic.Field(
        default=None, alias="functionDeclarations"
    )


class GenerateContentConfig(BaseModel):
    """Datamodel for the generate content config."""

    system_instruction: Content | None = pydantic.Field(
        default=None, alias="systemInstruction"
    )
    tools: list[ToolDeclaration] | None = pydantic.Field(
        default=None,
    )
    excluded_tools: list[str] | None = pydantic.Field(
        default=None, alias="excludedTools"
    )

    def hide_tool(self, tool_name: str):
        if self.excluded_tools is None:
            self.excluded_tools = []
        self.excluded_tools.append(tool_name)


class EventActions(BaseModel):
    """Event actions data model for the tool context."""

    skip_summarization: bool | None = pydantic.Field(
        default=None, alias="skipSummarization"
    )
    state_delta: dict[str, Any] = pydantic.Field(
        default_factory=dict, alias="stateDelta"
    )
    artifact_delta: dict[str, Any] = pydantic.Field(
        default_factory=dict, alias="artifactDelta"
    )
    transfer_to_agent: str | None = pydantic.Field(
        default=None, alias="transferToAgent"
    )
    escalate: bool | None = None
    requested_auth_configs: dict[str, dict[str, Any]] = pydantic.Field(
        default_factory=dict, alias="requestedAuthConfigs"
    )
    end_invocation: bool | None = pydantic.Field(
        default=None, alias="endInvocation"
    )


class Event(BaseModel):
    """Representation of an event in the session in the tool context."""

    id: str
    author: str
    timestamp: int
    invocation_id: str = pydantic.Field(alias="invocationId")
    content: Content | None = None
    actions: EventActions = EventActions()
    long_running_tool_ids: set[str] | None = pydantic.Field(
        default=None, alias="longRunningToolIds"
    )
    partial: bool | None = None
    turn_complete: bool | None = pydantic.Field(
        default=None, alias="turnComplete"
    )
    error_code: str | None = pydantic.Field(default=None, alias="errorCode")
    error_message: str | None = pydantic.Field(
        default=None, alias="errorMessage"
    )
    interrupted: bool | None = None
    branch: str | None = None
    grounding_metadata: Any = pydantic.Field(
        default=None, alias="groundingMetadata"
    )

    def is_user(self) -> bool:
        return self.author == "user"

    def is_agent(self, agent_name: str | None = None) -> bool:
        if agent_name is None:
            return self.author != "user"
        return self.author == agent_name

    def has_error(self) -> bool:
        return self.error_code is not None

    def parts(self) -> list[Part]:
        if self.content and self.content.parts:
            return self.content.parts
        return []


class CallbackContext(BaseModel):
    """Datamodel for the callback context."""

    user_content: Content | None = pydantic.Field(
        default=None, alias="userContent"
    )
    invocation_id: str = pydantic.Field(default="", alias="invocationId")
    agent_name: str = pydantic.Field(default="", alias="agentName")
    session_id: str = pydantic.Field(default="", alias="sessionId")
    state: dict[str, Any] = {}
    actions: EventActions = EventActions()
    events: list[Event] = []
    streaming_stage: str | None = pydantic.Field(
        default=None, alias="streamingStage"
    )
    language_code: str = pydantic.Field(default="", alias="languageCode")
    project_id: str = pydantic.Field(default="", alias="projectId")
    project_number: int = pydantic.Field(default=0, alias="projectNumber")
    location: str = pydantic.Field(default="", alias="location")
    app_id: str = pydantic.Field(default="", alias="appId")
    turn_index: int = pydantic.Field(default=0, alias="turnIndex")

    # Alias to state. `variables` is the legacy CES name for state.
    @property
    def variables(self) -> dict[str, Any]:
        return self.state

    @variables.setter
    def variables(self, value: dict[str, Any]):
        self.state = value

    # Shortcut function for getting a variable from the state.
    def get_variable(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    # Shortcut function for setting a variable in the state.
    def set_variable(self, key: str, value: Any) -> None:
        self.state[key] = value

    # Shortcut function for removing a variable from the state.
    def remove_variable(self, key: str) -> None:
        del self.state[key]

    def get_last_user_input(self) -> list[Part]:
        parts = []
        for event in reversed(self.events):
            if parts and not event.is_user():
                break
            parts += event.parts()
        parts.reverse()
        return parts

    def get_last_agent_output(self) -> list[Part]:
        parts = []
        for event in reversed(self.events):
            if parts and not event.is_agent():
                break
            parts += event.parts()
        parts.reverse()
        return parts

    def parts(self) -> list[Part]:
        parts = []
        for event in self.events:
            parts.extend(event.parts())
        return parts


class ToolContext(CallbackContext):
    """Tool context that could be used by the user code."""

    function_call_id: str | None = pydantic.Field(
        default=None, alias="functionCallId"
    )


class Tool(BaseModel):
    """Datamodel for the tool."""

    name: str
    description: str


class LlmRequest(BaseModel):
    """Datamodel for the LLM request."""

    model: str | None = None
    contents: list[Content] = pydantic.Field(default_factory=list)
    config: GenerateContentConfig | None = None


class LlmResponse(BaseModel):
    """Datamodel for the LLM response."""

    content: Content | None = None
    partial: bool | None = None
    turn_complete: bool | None = pydantic.Field(
        default=None, alias="turnComplete"
    )

    @classmethod
    def from_parts(cls, parts: list[Part]) -> "LlmResponse":
        return cls(content=Content(parts=parts, role="model"))
