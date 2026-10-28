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

"""CallbackChainRunner implementation for CES multi-callback chains (V1 & V2
images).
"""

from __future__ import annotations

import contextvars
import copy
import hashlib
import io
import json
import logging
import os
import threading
import time
import traceback
import typing
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

from cxas_scrapi.evals.callback_chain.compare import execute_compare_modes
from cxas_scrapi.evals.callback_chain.errors import resolve_error_outcome
from cxas_scrapi.evals.callback_chain.hydration import (
    _ACTIVE_EXECUTION_CTX,
    _ACTIVE_TOOL_CALL_LOG,
    StateUpdateMode,
    build_agent_tool_declarations,
    build_contents_with_state_updates,
    get_agent_in_scope_params,
    load_local_python_tools,
    render_agent_instruction,
)
from cxas_scrapi.evals.callback_chain.lint import (
    CallbackChainLintFinding,
    collect_subscript_or_in_keys_in_specs,
    collect_written_state_keys_in_specs,
    lint_stage_chain,
)
from cxas_scrapi.evals.callback_chain.semantics import (
    STAGE_TO_AGENT_JSON_KEYS,
    STAGE_TO_FUNC_NAME,
    STAGE_TO_GLOBAL_JSON_KEYS,
    UNSUPPORTED_GLOBAL_JSON_KEYS,
    apply_before_model_request_updates,
    coerce_struct_numbers,
    detect_state_aliasing,
    dump_state_bytes,
    extract_tool_input_update,
    format_current_date,
    json_normalize_for_diff,
    json_round_trip,
    normalize_image_version,
    normalize_stage,
    update_state_from_snapshot,
    validate_and_normalize_return,
    validate_before_agent_result,
)
from cxas_scrapi.evals.callback_chain.traces import (
    CHAIN_MODE_ENV,
    VALID_CHANNELS,
    VALID_MODALITIES,
    CallbackSpec,
    CallbackStepTrace,
    CallbackTrace,
    ErrorPhase,
    ExecutionMode,
    ImageVersion,
    ModeDiffReport,
    StageType,
    TurnTrace,
)
from cxas_scrapi.evals.callback_chain.turn import (
    SessionSimulator,
    execute_turn,
    invoke_mock_llm,
    invoke_tool_impl,
)
from cxas_scrapi.utils import callback_libs
from cxas_scrapi.utils.callback_libs import (
    Blob,
    CallbackContext,
    Content,
    Event,
    ExternalResponse,
    FunctionCall,
    FunctionDeclaration,
    FunctionResponse,
    GenerateContentConfig,
    HttpMethod,
    LlmRequest,
    LlmResponse,
    MockAsyncTools,
    MockTools,
    Part,
    StatusError,
    Tool,
    ToolContext,
    orjson,
)

logger = logging.getLogger(__name__)

_ACTIVE_STAGE_COLLECTOR: contextvars.ContextVar[list[CallbackTrace] | None] = (
    contextvars.ContextVar("_ACTIVE_STAGE_COLLECTOR", default=None)
)
_FALLBACK_STAGE_COLLECTOR_STACK: list[list[CallbackTrace]] = []
_STAGE_COLLECTOR_LOCK = threading.Lock()


def _push_active_stage_collector(
    collector: list[CallbackTrace],
) -> contextvars.Token[list[CallbackTrace] | None]:
    """Registers an active stage trace collector in ContextVar and
    thread-fallback stack.
    """
    with _STAGE_COLLECTOR_LOCK:
        _FALLBACK_STAGE_COLLECTOR_STACK.append(collector)
    return _ACTIVE_STAGE_COLLECTOR.set(collector)


def _pop_active_stage_collector(
    token: contextvars.Token[list[CallbackTrace] | None] | None,
    collector: list[CallbackTrace] | None = None,
) -> None:
    """Restores the previous stage trace collector in ContextVar and
    thread-fallback stack.
    """
    if token is not None:
        _ACTIVE_STAGE_COLLECTOR.reset(token)
    else:
        _ACTIVE_STAGE_COLLECTOR.set(None)
    with _STAGE_COLLECTOR_LOCK:
        if collector is not None:
            for idx in range(len(_FALLBACK_STAGE_COLLECTOR_STACK) - 1, -1, -1):
                if _FALLBACK_STAGE_COLLECTOR_STACK[idx] is collector:
                    _FALLBACK_STAGE_COLLECTOR_STACK.pop(idx)
                    break
        elif _FALLBACK_STAGE_COLLECTOR_STACK:
            _FALLBACK_STAGE_COLLECTOR_STACK.pop()


def _record_collected_stage(stage_trace: CallbackTrace) -> None:
    """Appends `stage_trace` to the active ContextVar or thread-fallback
    collector.
    """
    collector = _ACTIVE_STAGE_COLLECTOR.get()
    if collector is None:
        with _STAGE_COLLECTOR_LOCK:
            if _FALLBACK_STAGE_COLLECTOR_STACK:
                collector = _FALLBACK_STAGE_COLLECTOR_STACK[-1]
    if collector is not None:
        with _STAGE_COLLECTOR_LOCK:
            collector.append(stage_trace)


def _safe_copy_state_dict(
    state: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """Deep-copies `state`, falling back to recursive structural copy if an
    entry resists `deepcopy`.
    """
    try:
        return copy.deepcopy(state), None
    except Exception as exc:

        def _copy_val(val: Any) -> Any:
            try:
                return copy.deepcopy(val)
            except Exception:
                if isinstance(val, dict):
                    return {k: _copy_val(v) for k, v in val.items()}
                if isinstance(val, list):
                    return [_copy_val(x) for x in val]
                if isinstance(val, tuple):
                    return tuple(_copy_val(x) for x in val)
                if isinstance(val, set):
                    return {_copy_val(x) for x in val}
                return val

        return (
            {k: _copy_val(v) for k, v in state.items()},
            (
                "[deepcopy] Non-deepcopyable object in state "
                f"({type(exc).__name__}: {exc}); isolated container copy used."
            ),
        )


class CallbackChainRunner:
    """Executes multi-callback chains with CES runtime dual-mode and V1/V2 image
    semantics.

    Note on concurrency and security:
    - Callback steps capture `stdout` and `stderr` via
      `contextlib.redirect_stdout` /
      `redirect_stderr` (which mutate process-global `sys.stdout` /
      `sys.stderr`), and
      traces emitted from context-less worker threads (e.g.
      `ThreadPoolExecutor`) are
      attributed to the innermost active collector on the fallback stack.
      Concurrent
      trace collectors or `CallbackChainRunner` executions across multiple
      threads in the
      same process are not supported.
    - `CallbackChainRunner` is a local developer testing and evaluation harness
      that
      executes callback and tool Python files in-process with standard Python
      `__builtins__`; it is not a security sandbox for untrusted code.
    """

    def __init__(
        self,
        agent_dir: str | Path | None = None,
        *,
        app_dir: str | Path | None = None,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion = "v1",
        new_session: bool = True,
        allow_content_update: bool = False,
        simulate_struct_numbers: bool = True,
        time_zone: str | None = None,
        modality: str = "TEXT",
        channel: str = "API",
        session_metadata: dict[str, Any] | None = None,
        mock_tools: dict[str, Any] | None = None,
        app_config: dict[str, Any] | None = None,
        agent_config: dict[str, Any] | None = None,
        callbacks: dict[
            str,
            list[CallbackSpec | dict[str, Any] | tuple[str, str] | str],
        ]
        | None = None,
        error_handling_settings: dict[str, Any] | None = None,
        error_handling_strategy: str | None = None,
        max_fallback_attempts: int | None = None,
        initial_consecutive_fallback_count: int = 0,
        enable_exception_handling: bool = True,
        force_enable_exception_handling: bool = False,
        raise_on_error: bool = False,
        strict: bool = False,
        env_tools: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        """Initializes the CallbackChainRunner.

        Args:
            agent_dir: Path to an agent directory (`agents/<agent_name>`).
            app_dir: Optional path to the app root directory containing
            `app.json`.
            mode: Default execution mode (`"per_callback"` or `"combined"`).
                Defaults to `os.environ.get("CXAS_CALLBACK_MODE")` or
                `"per_callback"`.
            image_version: Sandbox image version (`"v1"` or `"v2"`). Defaults to
            `"v1"`,
                matching the production CES sandbox default.
            new_session: Whether to inject `app.json` non-null default variables
                and predefined variables (`current_date`, `_session`) on the
                first
                stage executed for a `CallbackContext` instance.
            allow_content_update: Corresponds to CES
            `allow_before_model_content_update`.
            simulate_struct_numbers: When `True` (default), simulates Protobuf
            `Struct`
                number conversion (`int` values converted to `float`) on session
                state, `tool_input`, `tool_response`, and `per_callback`
                round-trips.
            time_zone: Optional IANA time zone for `current_date` (e.g.,
            `"Europe/Berlin"`).
                Defaults to `app.json` `timeZoneSettings.timeZone`, or UTC
                (`"Z"`).
            modality: Session modality (`"TEXT"`, `"VOICE"`, `"UNSPECIFIED"`).
            channel: Session channel (`"API"`, `"WEB_UI"`,
            `"GOOGLE_TELEPHONY_PLATFORM"`, etc.).
            session_metadata: Optional metadata dict injected as
            `session_metadata`
                when non-empty.
            mock_tools: Mapping of tool names to return values or callables.
            app_config: Optional in-memory override for `app.json`.
            agent_config: Optional in-memory override for `<agent>.json`.
            callbacks: Optional in-memory callback specifications keyed by
            stage.
            error_handling_settings: Optional override for `app.json`
            `errorHandlingSettings`.
            error_handling_strategy: Optional shorthand override for
            `errorHandlingStrategy`.
            max_fallback_attempts: Optional shorthand override for
            `maxFallbackAttempts`.
            initial_consecutive_fallback_count: Default starting
            `consecutive_fallback_count`
                for each stage run (stateless across runs unless overridden).
            enable_exception_handling: Whether runtime callback exception
            handling is enabled
                (online default: `True`).
            force_enable_exception_handling: When `True`, forces fallback
            exception handling
                regardless of strategy settings (online default: `False`).
            raise_on_error: If `True`, re-raises callback exceptions directly.
            strict: Alias/companion for `raise_on_error`. When `True`, re-raises
            any
                callback exception instead of silently returning a fallback
                response.
        """
        env_mode = os.environ.get(CHAIN_MODE_ENV)
        eff_mode: str = mode or env_mode or "per_callback"
        if eff_mode not in ("per_callback", "combined"):
            raise ValueError(
                f"Invalid mode '{eff_mode}'. Expected 'per_callback' or "
                "'combined'."
            )
        self.mode: ExecutionMode = eff_mode  # type: ignore[assignment]
        self.image_version: ImageVersion = normalize_image_version(
            image_version
        )
        self.new_session = new_session
        self.allow_content_update = allow_content_update
        self.simulate_struct_numbers = simulate_struct_numbers
        self.time_zone = time_zone

        if modality not in VALID_MODALITIES:
            logger.warning(
                "Modality '%s' is not a standard CES Modality %s.",
                modality,
                sorted(VALID_MODALITIES),
            )
        if channel not in VALID_CHANNELS:
            logger.warning(
                "Channel '%s' is not a standard CES ChannelType %s.",
                channel,
                sorted(VALID_CHANNELS),
            )
        self.modality = modality
        self.channel = channel
        self.session_metadata = copy.deepcopy(session_metadata or {})
        self._user_mock_tools: dict[str, Any] = dict(mock_tools or {})
        self.env_tools: dict[str, Any] = dict(env_tools or {})
        self.mock_tools: dict[str, Any] = {
            **self.env_tools,
            **self._user_mock_tools,
        }
        self.initial_consecutive_fallback_count = (
            initial_consecutive_fallback_count
        )
        self.enable_exception_handling = enable_exception_handling
        self.force_enable_exception_handling = force_enable_exception_handling
        self.strict = bool(strict or raise_on_error)
        self.raise_on_error = self.strict

        self.agent_dir: Path | None = None
        self.app_dir: Path | None = None
        if agent_dir is not None:
            self.agent_dir, self.app_dir = self._resolve_paths(
                agent_dir, app_dir
            )
        elif app_dir is not None:
            self.app_dir = Path(app_dir).resolve()

        self.app_config: dict[str, Any] = {}
        if app_config is not None:
            self.app_config = copy.deepcopy(app_config)
        elif self.app_dir is not None and (self.app_dir / "app.json").is_file():
            with open(self.app_dir / "app.json", encoding="utf-8") as f:
                self.app_config = json.load(f)

        self.agent_config: dict[str, Any] = {}
        if agent_config is not None:
            self.agent_config = copy.deepcopy(agent_config)
        elif self.agent_dir is not None:
            agent_json_path = self._find_agent_json(self.agent_dir)
            if agent_json_path is not None:
                with open(agent_json_path, encoding="utf-8") as f:
                    self.agent_config = json.load(f)

        self.agent_name: str = (
            self.agent_config.get("displayName")
            or self.agent_config.get("display_name")
            or self.agent_config.get("name")
            or (self.agent_dir.name if self.agent_dir else "")
        )

        # Auto-bind local Python tools and optional injected environment tools
        self.local_python_tools: dict[str, Any] = {}
        if self.app_dir is not None:
            self.local_python_tools = load_local_python_tools(
                self,
                mock_tools={**self.env_tools, **self._user_mock_tools},
            )
            self.mock_tools = {
                **self.env_tools,
                **self.local_python_tools,
                **self._user_mock_tools,
            }
        self._mock_tools = self.mock_tools

        raw_ehs = (
            copy.deepcopy(error_handling_settings)
            if error_handling_settings is not None
            else copy.deepcopy(
                self.app_config.get("errorHandlingSettings")
                or self.app_config.get("error_handling_settings")
            )
        )
        if (
            error_handling_strategy is not None
            or max_fallback_attempts is not None
        ):
            if raw_ehs is None:
                raw_ehs = {}
            if error_handling_strategy is not None:
                raw_ehs["errorHandlingStrategy"] = error_handling_strategy
            if max_fallback_attempts is not None:
                fb_cfg = raw_ehs.setdefault("fallbackResponseConfig", {})
                fb_cfg["maxFallbackAttempts"] = max_fallback_attempts
        self.error_handling_settings: dict[str, Any] | None = raw_ehs

        self._stage_callbacks: dict[StageType, list[CallbackSpec]] = {
            s: [] for s in STAGE_TO_FUNC_NAME
        }
        self._load_configured_callbacks()
        if callbacks:
            for raw_stage, specs in callbacks.items():
                norm_stage = normalize_stage(raw_stage)
                parsed_specs: list[CallbackSpec] = []
                for idx, item in enumerate(specs, 1):
                    if isinstance(item, CallbackSpec):
                        parsed_specs.append(item)
                    elif isinstance(item, tuple) and len(item) == 2:
                        parsed_specs.append(
                            CallbackSpec(
                                name=item[0],
                                stage=norm_stage,
                                python_code=item[1],
                            )
                        )
                    elif isinstance(item, dict):
                        parsed_specs.append(
                            CallbackSpec(
                                name=item.get(
                                    "name", f"{norm_stage}_callbacks_{idx:02d}"
                                ),
                                stage=norm_stage,
                                python_code=item.get("python_code")
                                or item.get("pythonCode")
                                or item.get("inlineCode")
                                or item.get("code", ""),
                                path=item.get("path"),
                                description=item.get("description", ""),
                                disabled=bool(item.get("disabled", False)),
                                is_global=bool(item.get("is_global", False)),
                            )
                        )
                    elif isinstance(item, str):
                        parsed_specs.append(
                            CallbackSpec(
                                name=f"{norm_stage}_callbacks_{idx:02d}",
                                stage=norm_stage,
                                python_code=item,
                            )
                        )
                    else:
                        raise TypeError(
                            f"Unsupported callback specification: {item!r}"
                        )
                self._stage_callbacks[norm_stage] = parsed_specs

    def reset(self) -> None:
        """Resets any runner-level initial fallback count configuration to 0."""
        self.initial_consecutive_fallback_count = 0

    @classmethod
    def from_agent_dir(
        cls,
        agent_dir: str | Path,
        *,
        app_dir: str | Path | None = None,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion = "v1",
        new_session: bool = True,
        allow_content_update: bool = False,
        simulate_struct_numbers: bool = True,
        time_zone: str | None = None,
        modality: str = "TEXT",
        channel: str = "API",
        session_metadata: dict[str, Any] | None = None,
        mock_tools: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> CallbackChainRunner:
        """Constructs a CallbackChainRunner from an agent directory path."""
        return cls(
            agent_dir=agent_dir,
            app_dir=app_dir,
            mode=mode,
            image_version=image_version,
            new_session=new_session,
            allow_content_update=allow_content_update,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            modality=modality,
            channel=channel,
            session_metadata=session_metadata,
            mock_tools=mock_tools,
            **kwargs,
        )

    @classmethod
    def from_app_dir(
        cls,
        app_dir: str | Path,
        agent_name: str | None = None,
        *,
        env_tools: Mapping[str, Callable[..., Any]] | None = None,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion = "v1",
        new_session: bool = True,
        allow_content_update: bool = False,
        simulate_struct_numbers: bool = True,
        time_zone: str | None = None,
        modality: str = "TEXT",
        channel: str = "API",
        session_metadata: dict[str, Any] | None = None,
        mock_tools: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> CallbackChainRunner:
        """Constructs a CallbackChainRunner from an application root directory
        and optional agent name.
        """
        ap_path = Path(app_dir).resolve()
        target_agent = agent_name
        if not target_agent:
            app_json = ap_path / "app.json"
            if app_json.is_file():
                try:
                    app_cfg = json.loads(app_json.read_text(encoding="utf-8"))
                    target_agent = str(app_cfg.get("rootAgent") or "").strip()
                except Exception:
                    target_agent = None
            if not target_agent:
                target_agent = "root_agent"

        ag_cand = ap_path / "agents" / target_agent
        resolved_ag_dir: Path | str | None = (
            ag_cand if ag_cand.is_dir() else target_agent
        )
        return cls(
            agent_dir=resolved_ag_dir,
            app_dir=ap_path,
            env_tools=env_tools,
            mode=mode,
            image_version=image_version,
            new_session=new_session,
            allow_content_update=allow_content_update,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            modality=modality,
            channel=channel,
            session_metadata=session_metadata,
            mock_tools=mock_tools,
            **kwargs,
        )

    @classmethod
    def from_callbacks(
        cls,
        callbacks: dict[
            str,
            list[CallbackSpec | dict[str, Any] | tuple[str, str] | str],
        ],
        *,
        app_config: dict[str, Any] | None = None,
        agent_config: dict[str, Any] | None = None,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion = "v1",
        new_session: bool = True,
        allow_content_update: bool = False,
        simulate_struct_numbers: bool = True,
        time_zone: str | None = None,
        mock_tools: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> CallbackChainRunner:
        """Constructs a CallbackChainRunner directly from in-memory callback
        specs.
        """
        return cls(
            callbacks=callbacks,
            app_config=app_config,
            agent_config=agent_config,
            mode=mode,
            image_version=image_version,
            new_session=new_session,
            allow_content_update=allow_content_update,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            **kwargs,
        )

    @staticmethod
    def _resolve_paths(
        agent_dir: str | Path, app_dir: str | Path | None = None
    ) -> tuple[Path, Path | None]:
        """Resolves `agent_dir` and `app_dir` using explicit paths and
        `CXAS_SEARCH_PATHS`.
        """
        raw_path = Path(agent_dir)
        candidates: list[Path] = [raw_path]

        if app_dir is not None:
            app_p = Path(app_dir)
            candidates.append(app_p / raw_path)
            candidates.append(app_p / "agents" / raw_path.name)

        if not raw_path.is_absolute():
            env_roots = os.environ.get("CXAS_SEARCH_PATHS", "")
            extra_roots = [
                p.strip() for p in env_roots.split(os.pathsep) if p.strip()
            ]
            for root in extra_roots:
                root_p = Path(root)
                candidates.append(root_p / raw_path)
                candidates.append(root_p / "agents" / raw_path)

        resolved_agent: Path | None = None
        for cand in candidates:
            if cand.is_dir():
                resolved_agent = cand.resolve()
                break

        if resolved_agent is None:
            raise FileNotFoundError(
                f"Agent directory not found: '{agent_dir}'. "
                "Provide an existing path or set CXAS_SEARCH_PATHS."
            )

        resolved_app: Path | None = None
        if app_dir is not None and Path(app_dir).is_dir():
            resolved_app = Path(app_dir).resolve()
        else:
            curr = resolved_agent.parent
            for _ in range(4):
                if (curr / "app.json").is_file():
                    resolved_app = curr
                    break
                if curr.parent == curr:
                    break
                curr = curr.parent
            if resolved_app is None and resolved_agent.parent.name == "agents":
                resolved_app = resolved_agent.parent.parent

        return resolved_agent, resolved_app

    @staticmethod
    def _find_agent_json(agent_dir: Path) -> Path | None:
        """Finds `<agent_dir.name>.json` inside `agent_dir`."""
        direct = agent_dir / f"{agent_dir.name}.json"
        if direct.is_file():
            return direct
        json_files = sorted(agent_dir.glob("*.json"))
        if not json_files:
            return None
        raise FileNotFoundError(
            f"Expected agent config '{direct.name}' in '{agent_dir}', "
            f"found {[p.name for p in json_files]}"
        )

    def _load_configured_callbacks(self) -> None:
        """Loads global callbacks (before_model/after_model) followed by agent
        callbacks.
        """
        for bad_key in UNSUPPORTED_GLOBAL_JSON_KEYS:
            if bad_key in self.app_config:
                logger.warning(
                    "Ignoring unsupported global callback key '%s' in "
                    "app.json. "
                    "CES App proto only supports globalBeforeModelCallbacks "
                    "and "
                    "globalAfterModelCallbacks.",
                    bad_key,
                )

        for stage in STAGE_TO_FUNC_NAME:
            specs: list[CallbackSpec] = []
            for key in STAGE_TO_GLOBAL_JSON_KEYS.get(stage, ()):
                entries = self.app_config.get(key)
                if entries:
                    specs.extend(
                        self._parse_config_entries(
                            entries, stage=stage, is_global=True
                        )
                    )
                    break

            for key in STAGE_TO_AGENT_JSON_KEYS[stage]:
                entries = self.agent_config.get(key)
                if entries:
                    specs.extend(
                        self._parse_config_entries(
                            entries, stage=stage, is_global=False
                        )
                    )
                    break

            self._stage_callbacks[stage] = specs

    def _parse_config_entries(
        self,
        entries: list[dict[str, Any]],
        *,
        stage: StageType,
        is_global: bool,
    ) -> list[CallbackSpec]:
        specs: list[CallbackSpec] = []
        for idx, entry in enumerate(entries, 1):
            rel_code_path = entry.get("pythonCode") or entry.get(
                "python_code", ""
            )
            inline_code = entry.get("inlineCode") or entry.get("inline_code")
            disabled = bool(entry.get("disabled", False))
            description = entry.get("description", "")
            cb_name = entry.get("name")
            if not cb_name:
                if rel_code_path:
                    parent_name = Path(rel_code_path).parent.name
                    cb_name = (
                        parent_name
                        if parent_name and parent_name != "."
                        else Path(rel_code_path).stem
                    )
                else:
                    prefix = f"global_{stage}" if is_global else stage
                    cb_name = f"{prefix}_callbacks_{idx:02d}"

            code_str = ""
            resolved_file: Path | None = None
            if inline_code is not None:
                code_str = inline_code
            elif rel_code_path:
                file_candidates: list[Path] = []
                if self.app_dir is not None:
                    file_candidates.append(self.app_dir / rel_code_path)
                if self.agent_dir is not None:
                    file_candidates.append(self.agent_dir / rel_code_path)
                file_candidates.append(Path(rel_code_path))

                for cand in file_candidates:
                    if cand.is_file():
                        resolved_file = cand.resolve()
                        break

                if resolved_file is not None:
                    with open(resolved_file, encoding="utf-8") as f:
                        code_str = f.read()
                elif not disabled:
                    raise FileNotFoundError(
                        f"Callback pythonCode file not found: '{rel_code_path}'"
                    )

            specs.append(
                CallbackSpec(
                    name=cb_name,
                    stage=stage,
                    python_code=code_str,
                    path=str(resolved_file) if resolved_file else rel_code_path,
                    description=description,
                    disabled=disabled,
                    is_global=is_global,
                )
            )
        return specs

    def get_callbacks(
        self, stage: str, *, include_disabled: bool = False
    ) -> list[CallbackSpec]:
        """Returns configured callbacks for `stage` (excluding disabled by
        default).
        """
        norm_stage = normalize_stage(stage)
        all_specs = self._stage_callbacks.get(norm_stage, [])
        if include_disabled:
            return list(all_specs)
        return [s for s in all_specs if not s.disabled]

    def get_disabled_callbacks(self, stage: str) -> list[CallbackSpec]:
        """Returns callbacks marked `disabled: true` for `stage`."""
        norm_stage = normalize_stage(stage)
        return [
            s for s in self._stage_callbacks.get(norm_stage, []) if s.disabled
        ]

    def get_stage_specs(
        self, stage: str, *, include_disabled: bool = True
    ) -> list[CallbackSpec]:
        """Public accessor for `CallbackSpec` items configured for `stage`."""
        return self.get_callbacks(stage, include_disabled=include_disabled)

    def set_callbacks(
        self,
        stage: str,
        specs: Sequence[CallbackSpec | tuple[str, str] | str | Path],
    ) -> None:
        """Public mutator to replace configured callbacks for `stage`."""
        norm_stage = normalize_stage(stage)
        normalized: list[CallbackSpec] = []
        for idx, item in enumerate(specs, 1):
            if isinstance(item, CallbackSpec):
                normalized.append(item)
            elif isinstance(item, tuple) and len(item) == 2:
                cb_name, cb_code = item
                normalized.append(
                    CallbackSpec(
                        name=str(cb_name),
                        stage=norm_stage,
                        python_code=str(cb_code),
                    )
                )
            elif isinstance(item, Path):
                normalized.append(
                    CallbackSpec(
                        name=item.parent.name or item.stem,
                        stage=norm_stage,
                        python_code=item.read_text(encoding="utf-8"),
                        path=str(item),
                    )
                )
            elif isinstance(item, str):
                normalized.append(
                    CallbackSpec(
                        name=f"{norm_stage}_callbacks_{idx:02d}",
                        stage=norm_stage,
                        python_code=item,
                    )
                )
            else:
                raise TypeError(
                    "Unsupported callback spec item type: "
                    f"{type(item).__name__}"
                )
        self._stage_callbacks[norm_stage] = normalized

    def set_stage_specs(
        self,
        stage: str,
        specs: Sequence[CallbackSpec | tuple[str, str] | str | Path],
    ) -> None:
        """Alias for `set_callbacks(stage, specs)`."""
        self.set_callbacks(stage, specs)

    def record_fixture(
        self,
        ctx: CallbackContext | ToolContext,
        stage: str,
        output_path: str | Path | None = None,
        *,
        path: str | Path | None = None,
        fixtures_dir: str | Path | None = None,
        state: dict[str, Any] | None = None,
        llm_request: LlmRequest | dict[str, Any] | None = None,
        llm_response: LlmResponse | dict[str, Any] | None = None,
        tool: Tool | dict[str, Any] | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_response: dict[str, Any] | None = None,
    ) -> Path:
        """Records stage execution inputs to a JSON fixture file for replay by
        `compare_modes`.
        """
        norm_stage = normalize_stage(stage)
        eff_state = copy.deepcopy(state if state is not None else ctx.state)
        session_initialized = bool(
            getattr(ctx, "_ces_session_initialized", False)
        )
        ctx_dump = ctx.model_dump(
            mode="json",
            by_alias=True,
            exclude={"state"},
            exclude_none=True,
        )
        payload: dict[str, Any] = {
            "stage": norm_stage,
            "session_initialized": session_initialized,
            "state": json_round_trip(
                eff_state, image_version=self.image_version
            ),
        }
        if ctx_dump:
            payload["context"] = ctx_dump
        if llm_request is not None:
            payload["llm_request"] = (
                llm_request.model_dump(mode="json", exclude_none=True)
                if isinstance(llm_request, LlmRequest)
                else copy.deepcopy(llm_request)
            )
        if llm_response is not None:
            payload["llm_response"] = (
                llm_response.model_dump(mode="json", exclude_none=True)
                if isinstance(llm_response, LlmResponse)
                else copy.deepcopy(llm_response)
            )
        if tool is not None:
            payload["tool"] = (
                tool.model_dump(mode="json", exclude_none=True)
                if isinstance(tool, Tool)
                else copy.deepcopy(tool)
            )
        if tool_input is not None:
            payload["tool_input"] = json_round_trip(
                tool_input, image_version=self.image_version
            )
        if tool_response is not None:
            payload["tool_response"] = json_round_trip(
                tool_response, image_version=self.image_version
            )

        serialized = json.dumps(payload, indent=2) + "\n"
        resolved_path = output_path if output_path is not None else path
        if resolved_path is None:
            eff_fixtures_root = (
                fixtures_dir
                or getattr(self, "fixtures_dir", None)
                or os.environ.get("CXAS_FIXTURES_DIR")
            )
            if eff_fixtures_root is not None:
                ag_label = (
                    self.agent_dir.name
                    if self.agent_dir is not None
                    else (self.agent_name or "default_agent")
                )
                target_dir = Path(eff_fixtures_root) / ag_label / norm_stage
            elif self.agent_dir is not None:
                target_dir = self.agent_dir / "compare_fixtures" / norm_stage
            else:
                raise ValueError(
                    "record_fixture requires `output_path` (or `path` / "
                    "`fixtures_dir`) "
                    "when `runner.agent_dir` is not set."
                )
            target_dir.mkdir(parents=True, exist_ok=True)
            canonical_bytes = json.dumps(payload, sort_keys=True).encode(
                "utf-8"
            )
            digest = hashlib.sha256(canonical_bytes).hexdigest()[:12]
            resolved_path = target_dir / f"recorded_{digest}.json"

        out_file = Path(resolved_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(serialized, encoding="utf-8")
        return out_file

    def compare_modes_from_fixture(
        self,
        fixture_path: str | Path,
        *,
        stage: str | None = None,
        **kwargs: Any,
    ) -> ModeDiffReport:
        """Loads a JSON fixture file and runs `compare_modes` with its
        inputs.
        """
        fpath = Path(fixture_path)
        data = json.loads(fpath.read_text(encoding="utf-8"))
        eff_stage = normalize_stage(
            stage or data.get("stage") or fpath.parent.name
        )
        raw_state = copy.deepcopy(data.get("state") or {})
        ctx_meta = copy.deepcopy(data.get("context") or {})
        ctx_meta["state"] = raw_state
        fx_ctx = {"fixture_base64": True}
        ctx: CallbackContext | ToolContext
        if eff_stage in ("before_tool", "after_tool"):
            ctx = ToolContext.model_validate(ctx_meta, context=fx_ctx)
        else:
            ctx = CallbackContext.model_validate(ctx_meta, context=fx_ctx)
        session_initialized = bool(data.get("session_initialized", False))
        if session_initialized:
            ctx._ces_session_initialized = True
            kwargs.setdefault("new_session", False)

        llm_req = (
            LlmRequest.model_validate(data["llm_request"], context=fx_ctx)
            if isinstance(data.get("llm_request"), dict)
            else None
        )
        llm_resp = (
            LlmResponse.model_validate(data["llm_response"], context=fx_ctx)
            if isinstance(data.get("llm_response"), dict)
            else None
        )
        tool_obj = (
            Tool.model_validate(data["tool"], context=fx_ctx)
            if isinstance(data.get("tool"), dict)
            else None
        )
        tool_in = (
            copy.deepcopy(data["tool_input"])
            if isinstance(data.get("tool_input"), dict)
            else None
        )
        tool_resp = (
            copy.deepcopy(data["tool_response"])
            if isinstance(data.get("tool_response"), dict)
            else None
        )
        prev_suppress = getattr(self, "_suppress_fixture_recording", False)
        self._suppress_fixture_recording = True
        try:
            return self.compare_modes(
                ctx,
                eff_stage,
                llm_request=llm_req,
                llm_response=llm_resp,
                tool=tool_obj,
                tool_input=tool_in,
                tool_response=tool_resp,
                **kwargs,
            )
        finally:
            self._suppress_fixture_recording = prev_suppress

    def lint(
        self,
        stage: str | None = None,
        *,
        image_version: ImageVersion | None = None,
        include_info: bool = False,
    ) -> list[CallbackChainLintFinding]:
        """Runs cross-callback AST static analysis (C011-C019) on configured
        stage(s).
        """
        eff_img = (
            self.image_version
            if image_version is None
            else normalize_image_version(image_version)
        )
        stages: list[StageType] = (
            [normalize_stage(stage)]
            if stage is not None
            else list(STAGE_TO_FUNC_NAME.keys())
        )
        per_stage_written: dict[StageType, set[str]] = {
            st_key: collect_written_state_keys_in_specs(st_key, st_specs)
            for st_key, st_specs in self._stage_callbacks.items()
        }
        all_sub_or_in_keys: set[str] = set()
        for st_key, st_specs in self._stage_callbacks.items():
            all_sub_or_in_keys.update(
                collect_subscript_or_in_keys_in_specs(st_key, st_specs)
            )
        findings: list[CallbackChainLintFinding] = []
        for st in stages:
            other_stage_keys: set[str] = set()
            for other_st, keys in per_stage_written.items():
                if other_st != st:
                    other_stage_keys.update(keys)
            findings.extend(
                lint_stage_chain(
                    st,
                    self.get_callbacks(st, include_disabled=True),
                    app_config=self.app_config,
                    image_version=eff_img,
                    extra_known_keys=other_stage_keys,
                    subscript_or_in_keys=all_sub_or_in_keys,
                    include_info=include_info,
                )
            )
        return findings

    def _effective_time_zone(self, time_zone: str | None = None) -> str:
        """Resolves effective time zone for `current_date`."""
        if time_zone is not None:
            return time_zone
        if self.time_zone is not None:
            return self.time_zone
        return (
            self.app_config.get("timeZoneSettings")
            or self.app_config.get("time_zone_settings")
            or {}
        ).get("timeZone") or ""

    def build_initial_state(
        self,
        user_state: dict[str, Any] | None = None,
        *,
        new_session: bool | None = None,
        simulate_struct_numbers: bool | None = None,
        time_zone: str | None = None,
        modality: str | None = None,
        channel: str | None = None,
        session_metadata: dict[str, Any] | None = None,
        fixed_current_date: str | None = None,
    ) -> dict[str, Any]:
        """Builds initial session state aligned with the CES runtime."""
        use_new_session = (
            self.new_session if new_session is None else new_session
        )
        eff_sim_numbers = (
            self.simulate_struct_numbers
            if simulate_struct_numbers is None
            else simulate_struct_numbers
        )
        eff_modality = self.modality if modality is None else modality
        eff_channel = self.channel if channel is None else channel
        eff_metadata = (
            self.session_metadata
            if session_metadata is None
            else session_metadata
        )

        state: dict[str, Any] = {}

        if use_new_session:
            var_decls = (
                self.app_config.get("variableDeclarations")
                or self.app_config.get("variable_declarations")
                or []
            )
            for decl in var_decls:
                if not isinstance(decl, dict):
                    continue
                v_name = decl.get("name")
                if not v_name:
                    continue
                schema = decl.get("schema")
                if (
                    isinstance(schema, dict)
                    and "default" in schema
                    and schema["default"] is not None
                ):
                    default_val = copy.deepcopy(schema["default"])
                    state[v_name] = default_val

            eff_tz = self._effective_time_zone(time_zone)
            state["current_date"] = (
                fixed_current_date
                if fixed_current_date is not None
                else format_current_date(eff_tz)
            )
            state["_session"] = {
                "modality": eff_modality,
                "channel": eff_channel,
            }

        if eff_metadata:
            state["session_metadata"] = copy.deepcopy(eff_metadata)

        if user_state:
            for k, v in user_state.items():
                state[k] = copy.deepcopy(v)

        if eff_sim_numbers:
            state = coerce_struct_numbers(state)

        return state

    def _build_base_namespace(
        self,
        eff_mock_tools: dict[str, Any],
        tool_call_log: list[dict[str, Any]],
        *,
        image_version: ImageVersion = "v1",
        extra_globals: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Builds the base execution namespace matching V1 (`ces_public`) or V2
        sandbox scope.

        Note: In-process execution exposes standard `__builtins__` for local
        testing
        compatibility and is not an isolated security sandbox.
        """
        sync_tools = MockTools(eff_mock_tools, call_log=tool_call_log)
        async_tools = MockAsyncTools(sync_tools)

        if image_version == "v1":
            ns: dict[str, Any] = {
                "__builtins__": __builtins__,
                "json": json,
                "Any": typing.Any,
                "List": typing.List,  # noqa: UP006
                "Optional": typing.Optional,
                # Symbols from `from ces_public import *`
                "FunctionCall": FunctionCall,
                "FunctionResponse": FunctionResponse,
                "Blob": Blob,
                "Part": Part,
                "Content": Content,
                "GenerateContentConfig": GenerateContentConfig,
                "CallbackContext": CallbackContext,
                "ToolContext": ToolContext,
                "Tool": Tool,
                "LlmRequest": LlmRequest,
                "LlmResponse": LlmResponse,
                "ExternalResponse": ExternalResponse,
                "StatusError": StatusError,
                "tools": sync_tools,
                "async_tools": async_tools,
                "HttpMethod": HttpMethod,
                "ces_requests": getattr(callback_libs, "ces_requests", None),
                "requests": getattr(callback_libs, "requests", None),
            }
        else:
            ns = {}
            for attr in dir(callback_libs):
                if not attr.startswith("__"):
                    ns[attr] = getattr(callback_libs, attr)

            ns.update(
                {
                    "__builtins__": __builtins__,
                    "json": json,
                    "orjson": orjson,
                    "base64": callback_libs.base64,
                    "Any": typing.Any,
                    "Callable": typing.Callable,
                    "Dict": typing.Dict,  # noqa: UP006
                    "List": typing.List,  # noqa: UP006
                    "Optional": typing.Optional,
                    "Set": typing.Set,  # noqa: UP006
                    "Tuple": typing.Tuple,  # noqa: UP006
                    "Union": typing.Union,
                    "StatusError": StatusError,
                    "ExternalResponse": ExternalResponse,
                    "HttpMethod": HttpMethod,
                    "tools": sync_tools,
                    "async_tools": async_tools,
                }
            )

        if extra_globals:
            ns.update(extra_globals)
            ns["_ces_extra_globals"] = extra_globals
        return ns

    @staticmethod
    def _invoke_callback_fn(
        fn: Callable[..., Any],
        stage: StageType,
        *,
        cb_context: CallbackContext | ToolContext,
        llm_request: LlmRequest | None = None,
        llm_response: LlmResponse | None = None,
        tool: Tool | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_response: dict[str, Any] | None = None,
    ) -> Any:
        """Invokes the user callback function positionally matching
        v1/v2_image_callbacks.
        """
        if stage in ("before_agent", "after_agent"):
            return fn(cb_context)
        if stage == "before_model":
            return fn(cb_context, llm_request)
        if stage == "after_model":
            return fn(cb_context, llm_response)
        if stage == "before_tool":
            return fn(tool, tool_input, cb_context)
        if stage == "after_tool":
            return fn(tool, tool_input, cb_context, tool_response)
        raise ValueError(f"Unsupported stage: {stage}")

    def _execute_single_callback_step(
        self,
        spec: CallbackSpec,
        stage: StageType,
        func_name: str,
        base_namespace: dict[str, Any],
        *,
        cb_context: CallbackContext | ToolContext,
        llm_request: LlmRequest | None,
        llm_response: LlmResponse | None,
        tool: Tool | None,
        tool_input: dict[str, Any] | None,
        tool_response: dict[str, Any] | None,
    ) -> tuple[Any, str, str]:
        """Executes a single callback in a fresh namespace copy and captures
        stdio.
        """
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        cb_ns = base_namespace.copy()
        active_tool_log = getattr(
            base_namespace.get("tools"), "_call_log", None
        )
        ctx_token = _ACTIVE_EXECUTION_CTX.set(cb_context)
        log_token = _ACTIVE_TOOL_CALL_LOG.set(active_tool_log)

        try:
            with redirect_stdout(out_buf), redirect_stderr(err_buf):
                compiled = compile(
                    spec.python_code,
                    spec.path or f"<{spec.name}>",
                    "exec",
                )
                exec(compiled, cb_ns)
                extra_g = cb_ns.get("_ces_extra_globals")
                if isinstance(extra_g, dict):
                    for k, v in extra_g.items():
                        if k != func_name:
                            cb_ns[k] = v
                if func_name not in cb_ns or not callable(cb_ns[func_name]):
                    raise NameError(
                        f"Callback '{spec.name}' did not define callable "
                        f"'{func_name}'"
                    )
                raw_ret = self._invoke_callback_fn(
                    cb_ns[func_name],
                    stage,
                    cb_context=cb_context,
                    llm_request=llm_request,
                    llm_response=llm_response,
                    tool=tool,
                    tool_input=tool_input,
                    tool_response=tool_response,
                )
        except Exception as exc:
            exc._captured_stdout = out_buf.getvalue()  # type: ignore[attr-defined]
            exc._captured_stderr = err_buf.getvalue()  # type: ignore[attr-defined]
            raise
        finally:
            _ACTIVE_EXECUTION_CTX.reset(ctx_token)
            _ACTIVE_TOOL_CALL_LOG.reset(log_token)

        return raw_ret, out_buf.getvalue(), err_buf.getvalue()

    def _handle_chain_exception(
        self,
        exc: Exception,
        stage: StageType,
        failed_spec: CallbackSpec | None,
        skipped_specs: list[CallbackSpec],
        caller_ctx: CallbackContext | ToolContext,
        preserved_state: dict[str, Any],
        trace: CallbackTrace,
        stdout_parts: list[str],
        stderr_parts: list[str],
        *,
        consecutive_fallback_count: int,
        error_phase: ErrorPhase = "callback_execution",
        culprit_callback: str | None = None,
        diagnostic_hint: str | None = None,
        strict: bool = False,
        record_step: bool = True,
        step_duration_ms: float = 0.0,
    ) -> Any:
        """Records exception details on `trace`, resolves fallback, and updates
        `caller_ctx`.
        """
        step_stdout = getattr(exc, "_captured_stdout", "")
        step_stderr = getattr(exc, "_captured_stderr", "")
        if step_stdout:
            stdout_parts.append(step_stdout)
        if step_stderr:
            stderr_parts.append(step_stderr)

        tb_str = "".join(traceback.format_exception(exc))
        err_str = f"{type(exc).__name__}: {exc}\n{tb_str}"
        if diagnostic_hint:
            err_str = f"{err_str}\n[Diagnostic Hint] {diagnostic_hint}"

        if failed_spec is not None and record_step:
            trace.executed_callbacks.append(failed_spec.name)
            trace.steps.append(
                CallbackStepTrace(
                    name=failed_spec.name,
                    is_global=failed_spec.is_global,
                    stdout=step_stdout,
                    stderr=step_stderr,
                    error=err_str,
                    state_after=copy.deepcopy(preserved_state),
                    duration_ms=step_duration_ms,
                    python_code=failed_spec.python_code,
                    path=failed_spec.path,
                )
            )
        trace.error_phase = error_phase
        trace.failed_callback = (
            failed_spec.name if failed_spec is not None else None
        )
        trace.culprit_callback = culprit_callback or (
            failed_spec.name if failed_spec is not None else None
        )
        trace.error = err_str
        trace.raw_exception = exc
        trace.skipped_callbacks = [s.name for s in skipped_specs]
        trace.stdout = "".join(stdout_parts)
        trace.stderr = "".join(stderr_parts)
        trace.final_state = copy.deepcopy(preserved_state)
        caller_ctx.state = copy.deepcopy(preserved_state)

        outcome, outcome_resp, next_fb_count = resolve_error_outcome(
            stage,
            caller_ctx,
            error_handling_settings=self.error_handling_settings,
            app_config=self.app_config,
            consecutive_fallback_count=consecutive_fallback_count,
            enable_exception_handling=self.enable_exception_handling,
            force_enable_exception_handling=self.force_enable_exception_handling,
        )
        trace.error_outcome = outcome
        trace.consecutive_fallback_count_after = next_fb_count
        trace.override_response = outcome_resp
        trace.effective_response = outcome_resp
        trace.final_response = outcome_resp

        logger.warning(
            "Callback error in stage '%s' (mode=%s, phase=%s, failed=%s, "
            "culprit=%s, outcome=%s): %s: %s",
            stage,
            trace.mode,
            error_phase,
            trace.failed_callback,
            trace.culprit_callback,
            outcome,
            type(exc).__name__,
            exc,
        )

        if strict or self.raise_on_error:
            raise exc
        return outcome_resp

    def _finalize_chain_success(
        self,
        stage: StageType,
        final_result: Any,
        caller_ctx: CallbackContext | ToolContext,
        committed_state: dict[str, Any],
        trace: CallbackTrace,
        stdout_parts: list[str],
        stderr_parts: list[str],
        *,
        caller_llm_request: LlmRequest | None,
        updated_llm_request: LlmRequest | None,
        original_llm_response: LlmResponse | None,
        caller_tool_input: dict[str, Any] | None,
        updated_tool_input: dict[str, Any] | None,
        strict: bool = False,
    ) -> Any:
        """Commits state, validates `before_agent` post-condition, and populates
        trace outputs.
        """
        trace.consecutive_fallback_count_after = 0
        trace.stdout = "".join(stdout_parts)
        trace.stderr = "".join(stderr_parts)
        trace.final_state = copy.deepcopy(committed_state)
        caller_ctx.state = copy.deepcopy(committed_state)

        # Validate before_agent result AFTER state is committed outside the
        # callback error handler.
        if stage == "before_agent" and final_result is not None:
            try:
                validate_before_agent_result(final_result)
            except ValueError as exc:
                err_str = f"{type(exc).__name__}: {exc}"
                trace.error = err_str
                trace.raw_exception = exc
                trace.error_phase = "post_validation"
                trace.failed_callback = trace.short_circuited_by or (
                    trace.executed_callbacks[-1]
                    if trace.executed_callbacks
                    else None
                )
                trace.culprit_callback = trace.failed_callback
                trace.error_outcome = "error"
                trace.override_response = None
                trace.effective_response = None
                trace.final_response = None
                logger.warning(
                    "Invalid before_agent callback result in '%s': %s",
                    trace.failed_callback,
                    exc,
                )
                if strict or self.raise_on_error:
                    raise
                return None

        if stage == "before_model":
            if (
                caller_llm_request is not None
                and updated_llm_request is not None
            ):
                caller_llm_request.contents = copy.deepcopy(
                    updated_llm_request.contents
                )
                caller_llm_request.config = copy.deepcopy(
                    updated_llm_request.config
                )
            trace.final_request = copy.deepcopy(updated_llm_request)
            trace.override_response = final_result
            trace.effective_response = final_result
            trace.final_response = final_result
            return final_result

        if stage == "after_model":
            effective_resp = (
                final_result
                if final_result is not None
                else copy.deepcopy(original_llm_response)
            )
            trace.override_response = final_result
            trace.effective_response = effective_resp
            trace.final_response = effective_resp
            return final_result

        if stage == "before_tool":
            if caller_tool_input is not None and updated_tool_input is not None:
                caller_tool_input.clear()
                caller_tool_input.update(copy.deepcopy(updated_tool_input))
            trace.final_tool_input = copy.deepcopy(updated_tool_input)
            trace.override_response = final_result
            trace.effective_response = final_result
            trace.final_response = final_result
            return final_result

        trace.override_response = final_result
        trace.effective_response = final_result
        trace.final_response = final_result
        return final_result

    def run_stage(
        self,
        stage: str,
        ctx: CallbackContext | ToolContext,
        *,
        llm_request: LlmRequest | None = None,
        llm_response: LlmResponse | None = None,
        tool: Tool | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_response: dict[str, Any] | None = None,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion | None = None,
        new_session: bool | None = None,
        allow_content_update: bool | None = None,
        simulate_struct_numbers: bool | None = None,
        time_zone: str | None = None,
        mock_tools: dict[str, Any] | None = None,
        consecutive_fallback_count: int | None = None,
        fixed_current_date: str | None = None,
        extra_globals: dict[str, Any] | None = None,
        strict: bool | None = None,
    ) -> tuple[Any, CallbackTrace]:
        """Executes all enabled callbacks for `stage` in configured order."""
        stage_t0 = time.perf_counter()
        norm_stage = normalize_stage(stage)
        eff_mode: ExecutionMode = self.mode if mode is None else mode
        if eff_mode not in ("per_callback", "combined"):
            raise ValueError(
                f"Invalid mode '{eff_mode}'. Expected 'per_callback' or "
                "'combined'."
            )
        eff_image_version: ImageVersion = (
            self.image_version
            if image_version is None
            else normalize_image_version(image_version)
        )
        eff_allow_content = (
            self.allow_content_update
            if allow_content_update is None
            else allow_content_update
        )
        eff_sim_numbers = (
            self.simulate_struct_numbers
            if simulate_struct_numbers is None
            else simulate_struct_numbers
        )
        eff_mock_tools = {**self.mock_tools, **(mock_tools or {})}
        eff_fb_count = (
            self.initial_consecutive_fallback_count
            if consecutive_fallback_count is None
            else consecutive_fallback_count
        )
        eff_strict = self.strict if strict is None else strict

        if not ctx.agent_name and self.agent_name:
            ctx.agent_name = self.agent_name
        prev_image_version = getattr(ctx, "_image_version", "v1")
        ctx._image_version = eff_image_version
        try:
            return self._run_stage_impl(
                norm_stage,
                ctx,
                stage_t0=stage_t0,
                eff_mode=eff_mode,
                eff_image_version=eff_image_version,
                eff_allow_content=eff_allow_content,
                eff_sim_numbers=eff_sim_numbers,
                eff_mock_tools=eff_mock_tools,
                eff_fb_count=eff_fb_count,
                eff_strict=eff_strict,
                llm_request=llm_request,
                llm_response=llm_response,
                tool=tool,
                tool_input=tool_input,
                tool_response=tool_response,
                new_session=new_session,
                time_zone=time_zone,
                fixed_current_date=fixed_current_date,
                extra_globals=extra_globals,
            )
        finally:
            ctx._image_version = prev_image_version

    def _run_stage_impl(
        self,
        norm_stage: StageType,
        ctx: CallbackContext | ToolContext,
        *,
        stage_t0: float,
        eff_mode: ExecutionMode,
        eff_image_version: ImageVersion,
        eff_allow_content: bool,
        eff_sim_numbers: bool,
        eff_mock_tools: dict[str, Any],
        eff_fb_count: int,
        eff_strict: bool,
        llm_request: LlmRequest | None,
        llm_response: LlmResponse | None,
        tool: Tool | None,
        tool_input: dict[str, Any] | None,
        tool_response: dict[str, Any] | None,
        new_session: bool | None,
        time_zone: str | None,
        fixed_current_date: str | None,
        extra_globals: dict[str, Any] | None,
    ) -> tuple[Any, CallbackTrace]:
        if (
            os.environ.get("CXAS_RECORD_FIXTURES") == "1"
            and not getattr(self, "_suppress_fixture_recording", False)
            and (
                self.agent_dir is not None
                or os.environ.get("CXAS_FIXTURES_DIR")
                or getattr(self, "fixtures_dir", None)
            )
        ):
            self.record_fixture(
                ctx,
                norm_stage,
                llm_request=llm_request,
                llm_response=llm_response,
                tool=tool,
                tool_input=tool_input,
                tool_response=tool_response,
            )

        # Only inject session defaults on the first stage for this `ctx`.
        already_initialized = getattr(ctx, "_ces_session_initialized", False)
        if new_session is None:
            eff_new_session = self.new_session and not already_initialized
        else:
            eff_new_session = new_session

        if eff_new_session or already_initialized:
            ctx._ces_session_initialized = True

        initial_state = self.build_initial_state(
            ctx.state,
            new_session=eff_new_session,
            simulate_struct_numbers=eff_sim_numbers,
            time_zone=time_zone,
            fixed_current_date=fixed_current_date,
        )
        committed_state = json_round_trip(
            initial_state, image_version=eff_image_version
        )
        if eff_sim_numbers:
            committed_state = coerce_struct_numbers(committed_state)

        enabled_specs = self.get_callbacks(norm_stage, include_disabled=False)
        disabled_names = [
            s.name for s in self.get_disabled_callbacks(norm_stage)
        ]

        tool_call_log: list[dict[str, Any]] = []
        trace = CallbackTrace(
            stage=norm_stage,
            mode=eff_mode,
            image_version=eff_image_version,
            disabled_callbacks=disabled_names,
            initial_state=copy.deepcopy(committed_state),
            final_state=copy.deepcopy(committed_state),
            consecutive_fallback_count_after=eff_fb_count,
            tool_calls=tool_call_log,
        )
        object.__setattr__(trace, "_runner", self)

        if norm_stage == "before_model" and llm_request is None:
            llm_request = LlmRequest()
        if norm_stage == "after_model" and llm_response is None:
            llm_response = LlmResponse.from_parts([])
        if norm_stage in ("before_tool", "after_tool"):
            if tool is None:
                tool = Tool(name="mock_tool", description="")
            if tool_input is None:
                tool_input = {}
            elif eff_sim_numbers:
                coerced_in = coerce_struct_numbers(
                    json_round_trip(tool_input, image_version=eff_image_version)
                )
                tool_input.clear()
                tool_input.update(coerced_in)
        if norm_stage == "after_tool":
            if tool_response is None:
                tool_response = {}
            elif eff_sim_numbers:
                tool_response = coerce_struct_numbers(
                    json_round_trip(
                        tool_response, image_version=eff_image_version
                    )
                )

        if not enabled_specs:
            ctx.state = copy.deepcopy(committed_state)
            if norm_stage == "before_model":
                trace.final_request = copy.deepcopy(llm_request)
            elif norm_stage == "after_model":
                cp_resp = copy.deepcopy(llm_response)
                trace.effective_response = cp_resp
                trace.final_response = cp_resp
            elif norm_stage == "before_tool":
                trace.final_tool_input = copy.deepcopy(tool_input)
            trace.duration_ms = (time.perf_counter() - stage_t0) * 1000.0
            _record_collected_stage(trace)
            return None, trace

        base_namespace = self._build_base_namespace(
            eff_mock_tools,
            tool_call_log,
            image_version=eff_image_version,
            extra_globals=extra_globals,
        )
        func_name = STAGE_TO_FUNC_NAME[norm_stage]

        try:
            if eff_mode == "per_callback":
                final_output = self._run_per_callback_mode(
                    norm_stage,
                    enabled_specs,
                    func_name,
                    base_namespace,
                    ctx,
                    committed_state,
                    trace,
                    llm_request=llm_request,
                    llm_response=llm_response,
                    tool=tool,
                    tool_input=tool_input,
                    tool_response=tool_response,
                    allow_content_update=eff_allow_content,
                    simulate_struct_numbers=eff_sim_numbers,
                    image_version=eff_image_version,
                    consecutive_fallback_count=eff_fb_count,
                    strict=eff_strict,
                )
            else:
                final_output = self._run_combined_mode(
                    norm_stage,
                    enabled_specs,
                    func_name,
                    base_namespace,
                    ctx,
                    committed_state,
                    trace,
                    llm_request=llm_request,
                    llm_response=llm_response,
                    tool=tool,
                    tool_input=tool_input,
                    tool_response=tool_response,
                    allow_content_update=eff_allow_content,
                    simulate_struct_numbers=eff_sim_numbers,
                    image_version=eff_image_version,
                    consecutive_fallback_count=eff_fb_count,
                    strict=eff_strict,
                )
        finally:
            trace.duration_ms = (time.perf_counter() - stage_t0) * 1000.0
            _record_collected_stage(trace)

        return final_output, trace

    def _run_per_callback_mode(
        self,
        stage: StageType,
        specs: list[CallbackSpec],
        func_name: str,
        base_namespace: dict[str, Any],
        caller_ctx: CallbackContext | ToolContext,
        committed_state: dict[str, Any],
        trace: CallbackTrace,
        *,
        llm_request: LlmRequest | None,
        llm_response: LlmResponse | None,
        tool: Tool | None,
        tool_input: dict[str, Any] | None,
        tool_response: dict[str, Any] | None,
        allow_content_update: bool,
        simulate_struct_numbers: bool,
        image_version: ImageVersion,
        consecutive_fallback_count: int,
        strict: bool,
    ) -> Any:
        current_request = (
            copy.deepcopy(llm_request) if llm_request is not None else None
        )
        original_response = (
            copy.deepcopy(llm_response) if llm_response is not None else None
        )
        current_tool_input = (
            copy.deepcopy(tool_input) if tool_input is not None else None
        )
        original_tool_response = (
            copy.deepcopy(tool_response) if tool_response is not None else None
        )

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        final_result: Any = None

        for idx, spec in enumerate(specs):
            state_snapshot = copy.deepcopy(committed_state)
            step_ctx = copy.deepcopy(caller_ctx)
            step_ctx.state = copy.deepcopy(committed_state)

            work_req = (
                copy.deepcopy(current_request)
                if current_request is not None
                else None
            )
            work_resp = (
                copy.deepcopy(original_response)
                if original_response is not None
                else None
            )
            work_tool = copy.deepcopy(tool) if tool is not None else None
            work_tool_in = (
                copy.deepcopy(current_tool_input)
                if current_tool_input is not None
                else None
            )
            work_tool_resp = (
                copy.deepcopy(original_tool_response)
                if original_tool_response is not None
                else None
            )

            step_t0 = time.perf_counter()
            try:
                raw_ret, step_stdout, step_stderr = (
                    self._execute_single_callback_step(
                        spec,
                        stage,
                        func_name,
                        base_namespace,
                        cb_context=step_ctx,
                        llm_request=work_req,
                        llm_response=work_resp,
                        tool=work_tool,
                        tool_input=work_tool_in,
                        tool_response=work_tool_resp,
                    )
                )

                step_aliasing = detect_state_aliasing(step_ctx.state)
                step_warnings = [
                    f"[aliasing] In '{spec.name}': {w}" for w in step_aliasing
                ]
                for w in step_warnings:
                    if w not in trace.warnings:
                        trace.warnings.append(w)

                normalized_ret = validate_and_normalize_return(
                    stage, raw_ret, image_version=image_version
                )

                committed_state = update_state_from_snapshot(
                    committed_state,
                    state_snapshot,
                    step_ctx.state,
                    simulate_struct_numbers=simulate_struct_numbers,
                    image_version=image_version,
                )

                if (
                    stage == "before_model"
                    and current_request is not None
                    and work_req is not None
                ):
                    if normalized_ret is None:
                        if not allow_content_update and json_normalize_for_diff(
                            current_request.contents,
                            image_version=image_version,
                        ) != json_normalize_for_diff(
                            work_req.contents, image_version=image_version
                        ):
                            trace.request_mutations.append(
                                f"Callback '{spec.name}' mutated "
                                "llm_request.contents "
                                "in-place with allow_content_update=False "
                                "(discarded)."
                            )
                        if getattr(current_request, "model", None) != getattr(
                            work_req, "model", None
                        ):
                            trace.request_mutations.append(
                                f"Callback '{spec.name}' mutated "
                                "llm_request.model "
                                "in-place (not persisted by CES)."
                            )
                    current_request = apply_before_model_request_updates(
                        current_request,
                        work_req,
                        allow_content_update=allow_content_update,
                        image_version=image_version,
                    )

                if (
                    stage == "after_model"
                    and normalized_ret is None
                    and original_response is not None
                    and work_resp is not None
                    and json_normalize_for_diff(
                        original_response, image_version=image_version
                    )
                    != json_normalize_for_diff(
                        work_resp, image_version=image_version
                    )
                ):
                    trace.response_mutations.append(
                        f"Callback '{spec.name}' mutated llm_response in-place "
                        "and returned None (discarded in per_callback mode)."
                    )

                if (
                    stage == "before_tool"
                    and current_tool_input is not None
                    and work_tool_in is not None
                ):
                    current_tool_input = extract_tool_input_update(
                        current_tool_input,
                        work_tool_in,
                        simulate_struct_numbers=simulate_struct_numbers,
                        image_version=image_version,
                    )

            except Exception as exc:
                step_dur_ms = (time.perf_counter() - step_t0) * 1000.0
                return self._handle_chain_exception(
                    exc,
                    stage,
                    spec,
                    specs[idx + 1 :],
                    caller_ctx,
                    committed_state,
                    trace,
                    stdout_parts,
                    stderr_parts,
                    consecutive_fallback_count=consecutive_fallback_count,
                    error_phase="callback_execution",
                    culprit_callback=spec.name,
                    strict=strict,
                    step_duration_ms=step_dur_ms,
                )

            step_dur_ms = (time.perf_counter() - step_t0) * 1000.0
            if step_stdout:
                stdout_parts.append(step_stdout)
            if step_stderr:
                stderr_parts.append(step_stderr)

            trace.executed_callbacks.append(spec.name)
            trace.steps.append(
                CallbackStepTrace(
                    name=spec.name,
                    is_global=spec.is_global,
                    returned_override=normalized_ret is not None,
                    stdout=step_stdout,
                    stderr=step_stderr,
                    state_after=copy.deepcopy(committed_state),
                    warnings=step_warnings,
                    duration_ms=step_dur_ms,
                    python_code=spec.python_code,
                    path=spec.path,
                )
            )

            if normalized_ret is not None:
                final_result = normalized_ret
                trace.short_circuited = True
                trace.short_circuited_by = spec.name
                trace.skipped_callbacks = [s.name for s in specs[idx + 1 :]]
                break

        return self._finalize_chain_success(
            stage,
            final_result,
            caller_ctx,
            committed_state,
            trace,
            stdout_parts,
            stderr_parts,
            caller_llm_request=llm_request,
            updated_llm_request=current_request,
            original_llm_response=original_response,
            caller_tool_input=tool_input,
            updated_tool_input=current_tool_input,
            strict=strict,
        )

    def _run_combined_mode(
        self,
        stage: StageType,
        specs: list[CallbackSpec],
        func_name: str,
        base_namespace: dict[str, Any],
        caller_ctx: CallbackContext | ToolContext,
        committed_state: dict[str, Any],
        trace: CallbackTrace,
        *,
        llm_request: LlmRequest | None,
        llm_response: LlmResponse | None,
        tool: Tool | None,
        tool_input: dict[str, Any] | None,
        tool_response: dict[str, Any] | None,
        allow_content_update: bool,
        simulate_struct_numbers: bool,
        image_version: ImageVersion,
        consecutive_fallback_count: int,
        strict: bool,
    ) -> Any:
        state_snapshot = copy.deepcopy(committed_state)
        shared_ctx = copy.deepcopy(caller_ctx)
        shared_ctx.state = copy.deepcopy(committed_state)

        base_request = (
            copy.deepcopy(llm_request) if llm_request is not None else None
        )
        shared_request = (
            copy.deepcopy(llm_request) if llm_request is not None else None
        )
        original_response = (
            copy.deepcopy(llm_response) if llm_response is not None else None
        )
        shared_response = (
            copy.deepcopy(llm_response) if llm_response is not None else None
        )
        shared_tool = copy.deepcopy(tool) if tool is not None else None
        shared_tool_input = (
            copy.deepcopy(tool_input) if tool_input is not None else None
        )
        shared_tool_response = (
            copy.deepcopy(tool_response) if tool_response is not None else None
        )

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        raw_final_result: Any = None

        for idx, spec in enumerate(specs):
            pre_step_req = (
                copy.deepcopy(shared_request)
                if stage == "before_model" and shared_request is not None
                else None
            )
            pre_step_resp = (
                copy.deepcopy(shared_response)
                if stage == "after_model" and shared_response is not None
                else None
            )
            step_t0 = time.perf_counter()
            try:
                raw_ret, step_stdout, step_stderr = (
                    self._execute_single_callback_step(
                        spec,
                        stage,
                        func_name,
                        base_namespace,
                        cb_context=shared_ctx,
                        llm_request=shared_request,
                        llm_response=shared_response,
                        tool=shared_tool,
                        tool_input=shared_tool_input,
                        tool_response=shared_tool_response,
                    )
                )
            except Exception as exc:
                step_dur_ms = (time.perf_counter() - step_t0) * 1000.0
                return self._handle_chain_exception(
                    exc,
                    stage,
                    spec,
                    specs[idx + 1 :],
                    caller_ctx,
                    state_snapshot,
                    trace,
                    stdout_parts,
                    stderr_parts,
                    consecutive_fallback_count=consecutive_fallback_count,
                    error_phase="callback_execution",
                    culprit_callback=spec.name,
                    strict=strict,
                    step_duration_ms=step_dur_ms,
                )

            step_dur_ms = (time.perf_counter() - step_t0) * 1000.0
            if step_stdout:
                stdout_parts.append(step_stdout)
            if step_stderr:
                stderr_parts.append(step_stderr)

            trace.executed_callbacks.append(spec.name)

            # Runtime aliasing detection across distinct top-level keys in
            # shared_ctx.state
            step_aliasing = detect_state_aliasing(shared_ctx.state)
            step_warnings = [
                f"[aliasing] In '{spec.name}': {w}" for w in step_aliasing
            ]
            for w in step_warnings:
                if w not in trace.warnings:
                    trace.warnings.append(w)

            # In both v1_image_callbacks and v2_image_callbacks, the Python
            # sandbox loop checks `if <ret> is not None: break` for ALL stages
            # (including tool stages).
            loop_breaks = raw_ret is not None

            if (
                stage == "before_model"
                and not loop_breaks
                and pre_step_req is not None
                and shared_request is not None
            ):
                if not allow_content_update and json_normalize_for_diff(
                    pre_step_req.contents, image_version=image_version
                ) != json_normalize_for_diff(
                    shared_request.contents, image_version=image_version
                ):
                    trace.request_mutations.append(
                        f"Callback '{spec.name}' mutated llm_request.contents "
                        "in-place with allow_content_update=False."
                    )
                if getattr(pre_step_req, "model", None) != getattr(
                    shared_request, "model", None
                ):
                    trace.request_mutations.append(
                        f"Callback '{spec.name}' mutated llm_request.model "
                        "in-place (not persisted by CES)."
                    )

            if (
                stage == "after_model"
                and not loop_breaks
                and pre_step_resp is not None
                and shared_response is not None
                and json_normalize_for_diff(
                    pre_step_resp, image_version=image_version
                )
                != json_normalize_for_diff(
                    shared_response, image_version=image_version
                )
            ):
                trace.response_mutations.append(
                    f"Callback '{spec.name}' mutated llm_response in-place "
                    "and returned None (discarded at end of chain if None "
                    "returned)."
                )

            step_state_copy, copy_warn = _safe_copy_state_dict(shared_ctx.state)
            if copy_warn:
                step_warnings.append(copy_warn)
                if copy_warn not in trace.warnings:
                    trace.warnings.append(copy_warn)

            trace.steps.append(
                CallbackStepTrace(
                    name=spec.name,
                    is_global=spec.is_global,
                    returned_override=loop_breaks,
                    stdout=step_stdout,
                    stderr=step_stderr,
                    state_after=step_state_copy,
                    warnings=step_warnings,
                    duration_ms=step_dur_ms,
                    python_code=spec.python_code,
                    path=spec.path,
                )
            )

            if loop_breaks:
                raw_final_result = raw_ret
                trace.short_circuited = True
                trace.short_circuited_by = spec.name
                trace.skipped_callbacks = [s.name for s in specs[idx + 1 :]]
                break

        # End-of-chain single JSON round-trip & validation (combined mode)
        try:
            normalized_final = validate_and_normalize_return(
                stage, raw_final_result, image_version=image_version
            )
            committed_state = update_state_from_snapshot(
                committed_state,
                state_snapshot,
                shared_ctx.state,
                simulate_struct_numbers=simulate_struct_numbers,
                image_version=image_version,
            )

            updated_request = base_request
            if (
                stage == "before_model"
                and base_request is not None
                and shared_request is not None
            ):
                updated_request = apply_before_model_request_updates(
                    base_request,
                    shared_request,
                    allow_content_update=allow_content_update,
                    image_version=image_version,
                )

            updated_tool_input = tool_input
            if (
                stage == "before_tool"
                and tool_input is not None
                and shared_tool_input is not None
            ):
                updated_tool_input = extract_tool_input_update(
                    tool_input,
                    shared_tool_input,
                    simulate_struct_numbers=simulate_struct_numbers,
                    image_version=image_version,
                )
        except Exception as exc:
            # Identify which step first introduced an unserializable state value
            culprit: str | None = None
            hint: str | None = None
            for step in trace.steps:
                try:
                    dump_state_bytes(
                        step.state_after, image_version=image_version
                    )
                except Exception:
                    culprit = step.name
                    hint = (
                        "End-of-chain state serialization failed in combined "
                        "mode; "
                        "unserializable state was first introduced in "
                        f"callback '{culprit}'."
                    )
                    break
            if culprit is None:
                culprit = trace.short_circuited_by or (
                    trace.executed_callbacks[-1]
                    if trace.executed_callbacks
                    else None
                )
                hint = (
                    "End-of-chain return/request serialization failed in "
                    "combined mode "
                    f"(likely returned by '{culprit}')."
                )
            if trace.steps:
                tb_str = "".join(traceback.format_exception(exc))
                trace.steps[-1].error = f"{type(exc).__name__}: {exc}\n{tb_str}"
            return self._handle_chain_exception(
                exc,
                stage,
                None,
                [],
                caller_ctx,
                state_snapshot,
                trace,
                stdout_parts,
                stderr_parts,
                consecutive_fallback_count=consecutive_fallback_count,
                error_phase="end_of_chain_serialization",
                culprit_callback=culprit,
                diagnostic_hint=hint,
                strict=strict,
                record_step=False,
            )

        return self._finalize_chain_success(
            stage,
            normalized_final,
            caller_ctx,
            committed_state,
            trace,
            stdout_parts,
            stderr_parts,
            caller_llm_request=llm_request,
            updated_llm_request=updated_request,
            original_llm_response=original_response,
            caller_tool_input=tool_input,
            updated_tool_input=updated_tool_input,
            strict=strict,
        )

    # Stage convenience runners
    def run_before_agent(
        self,
        ctx: CallbackContext,
        *,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion | None = None,
        new_session: bool | None = None,
        simulate_struct_numbers: bool | None = None,
        time_zone: str | None = None,
        mock_tools: dict[str, Any] | None = None,
        extra_globals: dict[str, Any] | None = None,
        consecutive_fallback_count: int | None = None,
        strict: bool | None = None,
    ) -> tuple[Content | None, CallbackTrace]:
        """Executes the `before_agent` callback chain."""
        return self.run_stage(
            "before_agent",
            ctx,
            mode=mode,
            image_version=image_version,
            new_session=new_session,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            extra_globals=extra_globals,
            consecutive_fallback_count=consecutive_fallback_count,
            strict=strict,
        )

    def run_after_agent(
        self,
        ctx: CallbackContext,
        *,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion | None = None,
        new_session: bool | None = None,
        simulate_struct_numbers: bool | None = None,
        time_zone: str | None = None,
        mock_tools: dict[str, Any] | None = None,
        extra_globals: dict[str, Any] | None = None,
        consecutive_fallback_count: int | None = None,
        strict: bool | None = None,
    ) -> tuple[Content | None, CallbackTrace]:
        """Executes the `after_agent` callback chain."""
        return self.run_stage(
            "after_agent",
            ctx,
            mode=mode,
            image_version=image_version,
            new_session=new_session,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            extra_globals=extra_globals,
            consecutive_fallback_count=consecutive_fallback_count,
            strict=strict,
        )

    def run_before_model(
        self,
        ctx: CallbackContext,
        llm_request: LlmRequest | None = None,
        *,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion | None = None,
        new_session: bool | None = None,
        allow_content_update: bool | None = None,
        simulate_struct_numbers: bool | None = None,
        time_zone: str | None = None,
        mock_tools: dict[str, Any] | None = None,
        extra_globals: dict[str, Any] | None = None,
        consecutive_fallback_count: int | None = None,
        strict: bool | None = None,
    ) -> tuple[LlmResponse | None, CallbackTrace]:
        """Executes the `before_model` callback chain."""
        return self.run_stage(
            "before_model",
            ctx,
            llm_request=llm_request,
            mode=mode,
            image_version=image_version,
            new_session=new_session,
            allow_content_update=allow_content_update,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            extra_globals=extra_globals,
            consecutive_fallback_count=consecutive_fallback_count,
            strict=strict,
        )

    def run_after_model(
        self,
        ctx: CallbackContext,
        llm_response: LlmResponse,
        *,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion | None = None,
        new_session: bool | None = None,
        simulate_struct_numbers: bool | None = None,
        time_zone: str | None = None,
        mock_tools: dict[str, Any] | None = None,
        extra_globals: dict[str, Any] | None = None,
        consecutive_fallback_count: int | None = None,
        strict: bool | None = None,
    ) -> tuple[LlmResponse, CallbackTrace]:
        """Executes the `after_model` callback chain and returns
        `(effective_response, trace)`.
        """
        override_resp, trace = self.run_stage(
            "after_model",
            ctx,
            llm_response=llm_response,
            mode=mode,
            image_version=image_version,
            new_session=new_session,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            extra_globals=extra_globals,
            consecutive_fallback_count=consecutive_fallback_count,
            strict=strict,
        )
        effective_resp = (
            override_resp
            if override_resp is not None
            else trace.effective_response
        )
        return effective_resp, trace

    def run_before_tool(
        self,
        ctx: ToolContext | CallbackContext,
        tool: Tool,
        tool_input: dict[str, Any],
        *,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion | None = None,
        new_session: bool | None = None,
        simulate_struct_numbers: bool | None = None,
        time_zone: str | None = None,
        mock_tools: dict[str, Any] | None = None,
        extra_globals: dict[str, Any] | None = None,
        consecutive_fallback_count: int | None = None,
        strict: bool | None = None,
    ) -> tuple[dict[str, Any] | None, CallbackTrace]:
        """Executes the `before_tool` callback chain."""
        return self.run_stage(
            "before_tool",
            ctx,
            tool=tool,
            tool_input=tool_input,
            mode=mode,
            image_version=image_version,
            new_session=new_session,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            extra_globals=extra_globals,
            consecutive_fallback_count=consecutive_fallback_count,
            strict=strict,
        )

    def run_after_tool(
        self,
        ctx: ToolContext | CallbackContext,
        tool: Tool,
        tool_input: dict[str, Any],
        tool_response: dict[str, Any],
        *,
        mode: ExecutionMode | None = None,
        image_version: ImageVersion | None = None,
        new_session: bool | None = None,
        simulate_struct_numbers: bool | None = None,
        time_zone: str | None = None,
        mock_tools: dict[str, Any] | None = None,
        extra_globals: dict[str, Any] | None = None,
        consecutive_fallback_count: int | None = None,
        strict: bool | None = None,
    ) -> tuple[dict[str, Any] | None, CallbackTrace]:
        """Executes the `after_tool` callback chain."""
        return self.run_stage(
            "after_tool",
            ctx,
            tool=tool,
            tool_input=tool_input,
            tool_response=tool_response,
            mode=mode,
            image_version=image_version,
            new_session=new_session,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            extra_globals=extra_globals,
            consecutive_fallback_count=consecutive_fallback_count,
            strict=strict,
        )

    @staticmethod
    def _invoke_tool_impl(
        impl: Callable[..., Any],
        working_tin: dict[str, Any],
        tool_ctx: ToolContext,
    ) -> Any:
        """Invokes a tool implementation function with `(input, tool_ctx)` or
        `(input,)`.
        """
        return invoke_tool_impl(impl, working_tin, tool_ctx)

    @staticmethod
    def _invoke_mock_llm(
        mock_llm: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | None,
        ctx: CallbackContext,
        eff_request: LlmRequest,
        call_index: int = 0,
    ) -> LlmResponse:
        """Invokes `mock_llm` and validates that it returns an `LlmResponse`."""
        return invoke_mock_llm(
            mock_llm, ctx, eff_request, call_index=call_index
        )

    def render_instruction(
        self,
        state: dict[str, Any] | None = None,
        *,
        template_engine: typing.Literal["variables_only", "full"] | None = None,
        resolve_tags: bool = True,
        substitute_variables: bool = True,
        substitute_single_brace: bool | None = None,
    ) -> str:
        """Renders the agent's instruction template with CES tags and state
        variables.
        """
        return render_agent_instruction(
            self,
            state=state,
            agent_dir=self.agent_dir,
            app_dir=self.app_dir,
            agent_config=self.agent_config,
            app_config=self.app_config,
            template_engine=template_engine,
            resolve_tags=resolve_tags,
            substitute_variables=substitute_variables,
            substitute_single_brace=substitute_single_brace,
        )

    def build_tool_declarations(
        self,
        *,
        include_end_session: bool | None = None,
    ) -> list[FunctionDeclaration]:
        """Builds FunctionDeclaration schemas for the agent's tools and child
        agents.
        """
        return build_agent_tool_declarations(
            self,
            agent_dir=self.agent_dir,
            app_dir=self.app_dir,
            agent_config=self.agent_config,
            app_config=self.app_config,
            include_end_session=include_end_session,
        )

    def hydrate_llm_request(
        self,
        llm_request: LlmRequest | None = None,
        *,
        state: dict[str, Any] | None = None,
        events: Sequence[Event] | None = None,
        state_update_mode: StateUpdateMode = "rearrange",
        template_engine: typing.Literal["variables_only", "full"] | None = None,
        substitute_single_brace: bool | None = None,
    ) -> LlmRequest:
        """Populates `system_instruction`, `tools`, and `<state_update>` on
        `llm_request`.
        """
        req = (
            copy.deepcopy(llm_request)
            if llm_request is not None
            else LlmRequest()
        )
        if events is not None and state_update_mode != "none":
            in_scope = get_agent_in_scope_params(
                self, state=state, template_engine=template_engine
            )
            if in_scope:
                req.contents = build_contents_with_state_updates(
                    events, in_scope, mode=state_update_mode
                )
        if req.config is None:
            req.config = GenerateContentConfig()
        has_sys = bool(
            req.config.system_instruction is not None
            and (
                isinstance(req.config.system_instruction, str)
                or req.config.system_instruction.parts
            )
        )
        if not has_sys:
            rendered = self.render_instruction(
                state=state,
                template_engine=template_engine,
                substitute_single_brace=substitute_single_brace,
            )
            if rendered:
                req.config.system_instruction = Content(
                    role="system",
                    parts=[Part.from_text(rendered)],
                )
        existing_tools = req.config.tools or []
        has_decls = any(bool(t.function_declarations) for t in existing_tools)
        if not has_decls:
            decls = self.build_tool_declarations()
            if decls:
                req.config.tools = [
                    callback_libs.ToolDeclaration(function_declarations=decls)
                ]
        if req.config.temperature is None:
            for cfg_src in (self.agent_config, self.app_config):
                if not isinstance(cfg_src, dict):
                    continue
                ms = cfg_src.get("modelSettings") or cfg_src.get(
                    "model_settings"
                )
                if isinstance(ms, dict) and ms.get("temperature") is not None:
                    try:
                        req.config.temperature = float(ms["temperature"])
                        break
                    except (TypeError, ValueError):
                        pass
        return req

    def load_local_tool_impls(self) -> dict[str, Callable[..., Any]]:
        """Loads local Python tool `run()` callables from the app's tools
        directory.
        """
        return load_local_python_tools(
            self,
            agent_dir=self.agent_dir,
            app_dir=self.app_dir,
            agent_config=self.agent_config,
            app_config=self.app_config,
        )

    def run_turn(
        self,
        ctx: CallbackContext | str | Content | None = None,
        llm_request: LlmRequest | None = None,
        mock_llm: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        *,
        user_input: str | Content | None = None,
        initial_state: dict[str, Any] | None = None,
        llm_callable: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        llm: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        hydrate_from_agent: bool | None = None,
        auto_load_tools: bool = True,
        state_update_mode: StateUpdateMode = "rearrange",
        mode: ExecutionMode | None = None,
        image_version: ImageVersion | None = None,
        new_session: bool | None = None,
        allow_content_update: bool | None = None,
        simulate_struct_numbers: bool | None = None,
        time_zone: str | None = None,
        mock_tools: dict[str, Any] | None = None,
        execute_tools: bool | None = None,
        tool_impls: dict[str, Callable[..., Any] | dict[str, Any]]
        | None = None,
        include_agent_callbacks: bool | None = None,
        max_llm_calls: int | None = None,
        consecutive_fallback_count: int | None = None,
        strict: bool | None = None,
    ) -> tuple[LlmResponse | None, TurnTrace]:
        """Executes a single-turn callback and tool pipeline
        (`execute_turn`).
        """
        return execute_turn(
            self,
            ctx,
            llm_request,
            mock_llm,
            user_input=user_input,
            initial_state=initial_state,
            llm_callable=llm_callable,
            llm=llm,
            hydrate_from_agent=hydrate_from_agent,
            auto_load_tools=auto_load_tools,
            mode=mode,
            image_version=image_version,
            new_session=new_session,
            allow_content_update=allow_content_update,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            execute_tools=execute_tools,
            tool_impls=tool_impls,
            include_agent_callbacks=include_agent_callbacks,
            max_llm_calls=max_llm_calls,
            consecutive_fallback_count=consecutive_fallback_count,
            strict=strict,
            state_update_mode=state_update_mode,
        )

    def session(
        self,
        *,
        state: dict[str, Any] | None = None,
        initial_state: dict[str, Any] | None = None,
        ctx: CallbackContext | None = None,
        session_id: str = "sim_session",
        language_code: str = "en-US",
        llm: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        mock_llm: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        llm_callable: LlmResponse
        | Sequence[LlmResponse]
        | Callable[..., LlmResponse]
        | str
        | None = None,
        hydrate_from_agent: bool | None = None,
        auto_load_tools: bool = True,
        auto_follow_transfers: bool = False,
        max_agent_transfers: int = 3,
        include_agent_callbacks: bool = True,
        execute_tools: bool = True,
        tool_impls: dict[str, Callable[..., Any] | dict[str, Any]]
        | None = None,
        state_update_mode: StateUpdateMode = "rearrange",
        mode: ExecutionMode | None = None,
        image_version: ImageVersion | None = None,
        strict: bool | None = None,
    ) -> SessionSimulator:
        """Creates a multi-turn `SessionSimulator` bound to this runner."""
        return SessionSimulator(
            self,
            state=state,
            initial_state=initial_state,
            ctx=ctx,
            session_id=session_id,
            language_code=language_code,
            llm=llm,
            mock_llm=mock_llm,
            llm_callable=llm_callable,
            hydrate_from_agent=hydrate_from_agent,
            auto_load_tools=auto_load_tools,
            auto_follow_transfers=auto_follow_transfers,
            max_agent_transfers=max_agent_transfers,
            include_agent_callbacks=include_agent_callbacks,
            execute_tools=execute_tools,
            tool_impls=tool_impls,
            state_update_mode=state_update_mode,
            mode=mode,
            image_version=image_version,
            strict=strict,
        )

    simulate_session = session

    def compare_modes(
        self,
        ctx: CallbackContext | ToolContext,
        stage: str,
        *,
        llm_request: LlmRequest | None = None,
        llm_response: LlmResponse | None = None,
        tool: Tool | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_response: dict[str, Any] | None = None,
        image_version: ImageVersion | None = None,
        new_session: bool | None = None,
        allow_content_update: bool | None = None,
        simulate_struct_numbers: bool | None = None,
        time_zone: str | None = None,
        mock_tools: dict[str, Any] | None = None,
        consecutive_fallback_count: int | None = None,
    ) -> ModeDiffReport:
        """Runs `stage` under both `per_callback` and `combined` modes and diffs
        results.
        """
        return execute_compare_modes(
            self,
            ctx,
            stage,
            llm_request=llm_request,
            llm_response=llm_response,
            tool=tool,
            tool_input=tool_input,
            tool_response=tool_response,
            image_version=image_version,
            new_session=new_session,
            allow_content_update=allow_content_update,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            consecutive_fallback_count=consecutive_fallback_count,
        )

    def compare_stage_modes(
        self,
        stage: str,
        *,
        initial_state: dict[str, Any] | None = None,
        ctx: CallbackContext | ToolContext | None = None,
        llm_request: LlmRequest | None = None,
        llm_response: LlmResponse | None = None,
        tool: Tool | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_response: dict[str, Any] | None = None,
        image_version: ImageVersion | None = None,
        new_session: bool | None = None,
        allow_content_update: bool | None = None,
        simulate_struct_numbers: bool | None = None,
        time_zone: str | None = None,
        mock_tools: dict[str, Any] | None = None,
        consecutive_fallback_count: int | None = None,
    ) -> ModeDiffReport:
        """Runs `stage` under both `per_callback` and `combined` modes with
        stage-first ergonomics.

        Builds a fresh `CallbackContext` (or `ToolContext` for tool stages) from
        `initial_state` when `ctx` is omitted.
        """
        if ctx is not None and initial_state is not None:
            raise ValueError(
                "compare_stage_modes accepts either `ctx` or `initial_state`, "
                "not both."
            )
        norm_stage = normalize_stage(stage)
        if ctx is None:
            state_copy = copy.deepcopy(initial_state or {})
            if norm_stage in ("before_tool", "after_tool"):
                eff_ctx: CallbackContext | ToolContext = ToolContext(
                    state=state_copy
                )
            else:
                eff_ctx = CallbackContext(state=state_copy)
        else:
            eff_ctx = ctx
        return self.compare_modes(
            eff_ctx,
            norm_stage,
            llm_request=llm_request,
            llm_response=llm_response,
            tool=tool,
            tool_input=tool_input,
            tool_response=tool_response,
            image_version=image_version,
            new_session=new_session,
            allow_content_update=allow_content_update,
            simulate_struct_numbers=simulate_struct_numbers,
            time_zone=time_zone,
            mock_tools=mock_tools,
            consecutive_fallback_count=consecutive_fallback_count,
        )
