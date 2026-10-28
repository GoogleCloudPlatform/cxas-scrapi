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
import dataclasses
import datetime
import enum
import inspect
import json as json_lib
import math
import types
import uuid
from typing import (  # noqa: F401, UP035
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

import pydantic


def _ces_encoder(obj: Any) -> Any:
    """Custom JSON encoder aligned with the CES sandbox system library."""
    if isinstance(obj, bytes):
        return obj.decode()
    if isinstance(obj, set):
        return list(obj)
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    if isinstance(obj, pydantic.BaseModel):
        return obj.model_dump()
    raise TypeError(
        f"Object of type {type(obj).__name__} is not JSON serializable"
    )


class _OrjsonShim(types.ModuleType):
    """Compatibility shim for callbacks using orjson in environments without it.

    Enforces real orjson's strictness:
    - Raises `TypeError("Dict key must be str")` on non-string dict keys unless
      `OPT_NON_STR_KEYS` is set.
    - Serializes `NaN`, `Infinity`, and `-Infinity` as `null`.
    - Requires `default` callback for custom types
      (`bytes`, `set`, `BaseModel`).
    """

    OPT_NON_STR_KEYS = 1
    OPT_INDENT_2 = 2
    OPT_SORT_KEYS = 4

    def _prepare(
        self,
        val: Any,
        default: Callable[[Any], Any] | None,
        allow_non_str_keys: bool,
    ) -> Any:
        if val is None or isinstance(val, (str, bool, int)):
            return val
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val):
                return None
            return val
        if isinstance(val, datetime.datetime):
            iso = val.isoformat()
            if iso.endswith("+00:00"):
                return iso[:-6] + "Z"
            return iso
        if isinstance(val, (datetime.date, datetime.time)):
            return val.isoformat()
        if isinstance(val, uuid.UUID):
            return str(val)
        if isinstance(val, enum.Enum):
            return self._prepare(val.value, default, allow_non_str_keys)
        if dataclasses.is_dataclass(val) and not isinstance(val, type):
            return self._prepare(
                dataclasses.asdict(val), default, allow_non_str_keys
            )
        if isinstance(val, dict):
            out: dict[str, Any] = {}
            for k, v in val.items():
                if not isinstance(k, str):
                    if not allow_non_str_keys:
                        raise TypeError("Dict key must be str")
                    k_str = str(k)
                else:
                    k_str = k
                out[k_str] = self._prepare(v, default, allow_non_str_keys)
            return out
        if isinstance(val, (list, tuple)):
            return [
                self._prepare(item, default, allow_non_str_keys) for item in val
            ]
        if default is not None:
            converted = default(val)
            return self._prepare(converted, default, allow_non_str_keys)
        raise TypeError(f"Type is not JSON serializable: {type(val).__name__}")

    def dumps(
        self,
        obj: Any,
        default: Callable[[Any], Any] | None = None,
        option: int | None = None,
    ) -> bytes:
        allow_non_str_keys = bool(option and (option & self.OPT_NON_STR_KEYS))
        prepared = self._prepare(obj, default, allow_non_str_keys)
        indent = 2 if (option and (option & self.OPT_INDENT_2)) else None
        sort_keys = bool(option and (option & self.OPT_SORT_KEYS))
        return json_lib.dumps(
            prepared,
            indent=indent,
            sort_keys=sort_keys,
            allow_nan=False,
        ).encode("utf-8")

    def loads(self, data: bytes | str) -> Any:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json_lib.loads(data)


try:
    import orjson  # type: ignore[import-not-found]
except ImportError:
    orjson = _OrjsonShim("orjson")


# Util
def is_json_serializable(value: Any) -> bool:
    """Returns true if it can get a JSON dump of the value."""
    try:
        json_lib.dumps(value, default=_ces_encoder)
        return True
    except (TypeError, OverflowError, ValueError):
        return False


class StatusError(Exception):
    """Exception matching CES sandbox StatusError."""

    def __init__(
        self,
        message: str,
        code: int | None = None,
        *,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        resolved = code if code is not None else status_code
        self.code = resolved
        self.status_code = resolved


class HttpMethod(enum.Enum):
    """HTTP methods matching CES sandbox HttpMethod."""

    GET = "GET"
    HEAD = "HEAD"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    CONNECT = "CONNECT"
    OPTIONS = "OPTIONS"
    TRACE = "TRACE"
    PATCH = "PATCH"


class ExternalResponse:
    """Response from an external HTTP or tool call in the CES sandbox."""

    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes | str | dict[str, Any] = b"",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        if isinstance(body, dict):
            self._json_data: Any = body
            self.body = json_lib.dumps(body, default=_ces_encoder).encode(
                "utf-8"
            )
        elif isinstance(body, str):
            self._json_data = None
            self.body = body.encode("utf-8")
        else:
            self._json_data = None
            self.body = body

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        return json_lib.loads(self.text())

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding)


class MockExternalResponse(dict, ExternalResponse):
    """Dict-compatible ExternalResponse returned by mock_tools for dicts."""

    def __init__(
        self,
        data: Any,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._is_dict_payload = isinstance(data, dict)
        self._raw_payload = data
        if isinstance(data, dict):
            dict.__init__(self, data)
            body_arg: bytes | str | dict[str, Any] = data
        elif isinstance(data, (list, bool, int, float)):
            dict.__init__(self, {"result": data})
            body_arg = json_lib.dumps(data, default=_ces_encoder)
        elif data is None:
            dict.__init__(self, {"result": None})
            self._is_dict_payload = True
            body_arg = {"result": None}
        elif isinstance(data, bytes):
            dict.__init__(self)
            body_arg = data
        else:
            body_str = str(data)
            try:
                parsed = json_lib.loads(body_str)
                if isinstance(parsed, dict):
                    dict.__init__(self, parsed)
                    self._is_dict_payload = True
                else:
                    dict.__init__(self)
            except Exception:
                dict.__init__(self)
            body_arg = body_str

        ExternalResponse.__init__(
            self, status_code=status_code, headers=headers, body=body_arg
        )

    def json(self) -> Any:
        if self._is_dict_payload:
            return dict(self)
        if isinstance(self._raw_payload, (list, bool, int, float)):
            return self._raw_payload
        return ExternalResponse.json(self)


def _wrap_mock_tool_output(raw: Any) -> Any:
    """Wraps mock tool output so dict access and .json() work seamlessly."""
    if isinstance(raw, ExternalResponse):
        return raw
    if isinstance(raw, dict):
        return MockExternalResponse(raw)
    if isinstance(raw, (str, bytes)):
        return ExternalResponse(status_code=200, body=raw)
    return raw


class MockTools:
    """Mock `tools` namespace injected into callback execution scope."""

    def __init__(
        self,
        mock_tools: dict[str, Any] | None = None,
        call_log: list[dict[str, Any]] | None = None,
    ) -> None:
        self._mock_tools = mock_tools or {}
        self._call_log = call_log if call_log is not None else []

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._mock_tools:
            raise AttributeError(
                f"Tool '{name}' is not configured in mock_tools."
            )
        target = self._mock_tools[name]

        def _invoke(*args: Any, **kwargs: Any) -> Any:
            source = getattr(target, "_cxas_tool_source", "user-mock")
            log_entry: dict[str, Any] = {
                "tool": name,
                "args": args,
                "kwargs": kwargs,
                "source": source,
                "status_code": 200,
            }
            self._call_log.append(log_entry)
            try:
                if callable(target):
                    try:
                        raw = target(*args, **kwargs)
                    except TypeError:
                        sig = inspect.signature(target)
                        if len(sig.parameters) == 0:
                            raw = target()
                        elif not args and kwargs:
                            raw = target(kwargs)
                        else:
                            raise
                else:
                    raw = target
            except Exception as exc:
                err_code = (
                    getattr(exc, "status_code", None)
                    or getattr(exc, "code", None)
                    or 500
                )
                log_entry["status_code"] = err_code
                raise
            wrapped = _wrap_mock_tool_output(raw)
            log_entry["status_code"] = getattr(wrapped, "status_code", 200)
            return wrapped

        return _invoke


class MockAsyncTools:
    """Mock `async_tools` namespace injected into callback execution scope."""

    def __init__(self, sync_tools: MockTools) -> None:
        self._sync_tools = sync_tools

    def __getattr__(self, name: str) -> Callable[..., Any]:
        sync_fn = getattr(self._sync_tools, name)

        async def _async_invoke(*args: Any, **kwargs: Any) -> Any:
            return sync_fn(*args, **kwargs)

        return _async_invoke


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

    @pydantic.field_serializer("data", when_used="json")
    def _serialize_data_json(self, value: bytes | None) -> str | None:
        if value is None:
            return None
        return base64.b64encode(value).decode("ascii")

    @pydantic.field_validator("data", mode="before")
    @classmethod
    def _validate_data(
        cls, value: Any, info: pydantic.ValidationInfo
    ) -> bytes | None:
        if value is None or isinstance(value, bytes):
            return value
        if isinstance(value, str):
            if info.context and info.context.get("fixture_base64") is True:
                return base64.b64decode(value.encode("ascii"))
            return value.encode("utf-8")
        return value

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
    thought: bool | None = pydantic.Field(default=None)
    thought_signature: bytes | str | None = pydantic.Field(
        default=None, alias="thoughtSignature"
    )

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

    @classmethod
    def from_text(cls, text: str, role: str | None = None) -> "Content":
        return cls(parts=[Part.from_text(text)], role=role)


class FunctionDeclaration(BaseModel):
    """Datamodel for the function declaration."""

    name: str | None = pydantic.Field(
        default=None,
    )
    description: str | None = pydantic.Field(
        default=None,
    )
    parameters: dict[str, Any] | None = pydantic.Field(
        default=None,
    )
    response: dict[str, Any] | None = pydantic.Field(
        default=None,
    )


class UsageMetadata(BaseModel):
    """Token usage statistics returned by an LLM invocation."""

    prompt_token_count: int = pydantic.Field(
        default=0, alias="promptTokenCount"
    )
    candidates_token_count: int = pydantic.Field(
        default=0, alias="candidatesTokenCount"
    )
    total_token_count: int = pydantic.Field(default=0, alias="totalTokenCount")
    cached_content_token_count: int = pydantic.Field(
        default=0, alias="cachedContentTokenCount"
    )
    thoughts_token_count: int = pydantic.Field(
        default=0, alias="thoughtsTokenCount"
    )

    @pydantic.model_validator(mode="after")
    def _populate_total_token_count(self) -> "UsageMetadata":
        if self.total_token_count == 0 and (
            self.prompt_token_count
            or self.candidates_token_count
            or self.thoughts_token_count
        ):
            self.total_token_count = (
                self.prompt_token_count
                + self.candidates_token_count
                + self.thoughts_token_count
            )
        return self


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
    temperature: float | None = pydantic.Field(
        default=None,
    )

    @pydantic.field_validator("system_instruction", mode="before")
    @classmethod
    def _coerce_system_instruction(cls, value: Any) -> Any:
        if isinstance(value, str):
            return Content(role="system", parts=[Part.from_text(value)])
        return value

    @pydantic.field_validator("tools", mode="before")
    @classmethod
    def _coerce_tools(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        out: list[Any] = []
        for item in value:
            if isinstance(item, ToolDeclaration):
                out.append(item)
            elif hasattr(item, "function_declarations"):
                out.append(
                    ToolDeclaration(
                        function_declarations=getattr(
                            item, "function_declarations", None
                        )
                    )
                )
            else:
                out.append(item)
        return out

    def hide_tool(self, tool_name: str) -> None:
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


class _CallablePartList(list):
    """A `list[Part]` that is also callable as `event.parts()`."""

    def __call__(self) -> list["Part"]:
        return list(self)


class Event(BaseModel):
    """Representation of an event in the session in the tool context."""

    id: str = pydantic.Field(
        default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}"
    )
    author: str = "user"
    timestamp: int = pydantic.Field(
        default_factory=lambda: int(
            datetime.datetime.now(datetime.timezone.utc).timestamp()
        )
    )
    invocation_id: str = pydantic.Field(default="", alias="invocationId")
    content: Content | None = None
    actions: EventActions = pydantic.Field(default_factory=EventActions)
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

    @property
    def role(self) -> str | None:
        if self.content and self.content.role:
            return self.content.role
        return "user" if self.author == "user" else "model"

    @property
    def parts(self) -> _CallablePartList:
        if self.content and self.content.parts:
            return _CallablePartList(self.content.parts)
        return _CallablePartList()


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
    _ces_session_initialized: bool = pydantic.PrivateAttr(default=False)
    _image_version: str = pydantic.PrivateAttr(default="v1")
    _image_version_explicit: bool = pydantic.PrivateAttr(default=False)

    def __init__(self, **data: Any) -> None:
        raw_img = data.pop("_image_version", data.pop("image_version", None))
        super().__init__(**data)
        if raw_img is not None:
            norm_img = str(raw_img).strip().lower()
            if norm_img not in ("v1", "v2"):
                raise ValueError(
                    f"Invalid image_version {raw_img!r}. Expected 'v1' or 'v2'."
                )
            self._image_version = norm_img
            self._image_version_explicit = True

    # Alias to state. `variables` is the legacy CES name for state.
    @property
    def variables(self) -> dict[str, Any]:
        return self.state

    @variables.setter
    def variables(self, value: dict[str, Any]) -> None:
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
        if getattr(self, "_image_version", "v1") == "v1":
            parts: list[Part] = []
            for event in reversed(self.events):
                if parts and not event.is_user():
                    break
                parts += event.parts()
            parts.reverse()
            return parts
        user_events: list[Event] = []
        for event in reversed(self.events):
            if event.is_user():
                user_events.append(event)
            elif user_events:
                break
        parts = []
        for event in reversed(user_events):
            parts.extend(event.parts())
        return parts

    def get_last_agent_output(self) -> list[Part]:
        if getattr(self, "_image_version", "v1") == "v1":
            parts: list[Part] = []
            for event in reversed(self.events):
                if parts and not event.is_agent():
                    break
                parts += event.parts()
            parts.reverse()
            return parts
        agent_events: list[Event] = []
        for event in reversed(self.events):
            if event.is_agent():
                agent_events.append(event)
            elif agent_events:
                break
        parts = []
        for event in reversed(agent_events):
            parts.extend(event.parts())
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

    name: str = ""
    description: str = ""
    function_declarations: list[FunctionDeclaration] | None = pydantic.Field(
        default=None, alias="functionDeclarations"
    )


class LlmRequest(BaseModel):  # noqa: PLW1641
    """Datamodel for the LLM request."""

    model: str | None = None
    contents: list[Content] = pydantic.Field(default_factory=list)
    config: GenerateContentConfig | None = None
    _excluded_tools_applied: list[str] = pydantic.PrivateAttr(
        default_factory=list
    )
    _initial_tools_meta: list[tuple[str, str]] = pydantic.PrivateAttr(
        default_factory=list
    )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LlmRequest):
            return NotImplemented
        return (
            self.model == other.model
            and self.contents == other.contents
            and self.config == other.config
        )


class LlmResponse(BaseModel):
    """Datamodel for the LLM response."""

    content: Content | None = None
    partial: bool | None = None
    turn_complete: bool | None = pydantic.Field(
        default=None, alias="turnComplete"
    )
    usage_metadata: UsageMetadata | None = pydantic.Field(
        default=None, alias="usageMetadata"
    )
    model_version: str | None = pydantic.Field(
        default=None, alias="modelVersion"
    )
    finish_reason: str | None = pydantic.Field(
        default=None, alias="finishReason"
    )
    block_reason: str | None = pydantic.Field(default=None, alias="blockReason")

    @classmethod
    def from_parts(
        cls,
        parts: list[Part],
        partial: bool | None = None,
        *,
        usage_metadata: UsageMetadata | None = None,
        model_version: str | None = None,
        finish_reason: str | None = None,
        block_reason: str | None = None,
    ) -> "LlmResponse":
        return cls(
            content=Content(parts=parts, role="model"),
            partial=partial,
            usage_metadata=usage_metadata,
            model_version=model_version,
            finish_reason=finish_reason,
            block_reason=block_reason,
        )
