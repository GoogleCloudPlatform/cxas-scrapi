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

"""Local agent instruction rendering, tool schema extraction, and Python tool
loading.
"""

from __future__ import annotations

import ast
import contextlib
import contextvars
import copy
import inspect
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from cxas_scrapi.utils import callback_libs
from cxas_scrapi.utils.callback_libs import (
    CallbackContext,
    Content,
    Event,
    ExternalResponse,
    FunctionDeclaration,
    HttpMethod,
    MockAsyncTools,
    MockExternalResponse,
    MockTools,
    Part,
    StatusError,
    ToolContext,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from cxas_scrapi.evals.callback_chain.runner import CallbackChainRunner

_ACTIVE_EXECUTION_CTX: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "_ACTIVE_EXECUTION_CTX", default=None
)
_ACTIVE_TOOL_CALL_LOG: contextvars.ContextVar[list[dict[str, Any]] | None] = (
    contextvars.ContextVar("_ACTIVE_TOOL_CALL_LOG", default=None)
)


def get_active_execution_context() -> Any:
    """Returns the active `CallbackContext` during callback or tool execution,
    or `None`.
    """
    return _ACTIVE_EXECUTION_CTX.get()


def get_active_tool_call_log() -> list[dict[str, Any]] | None:
    """Returns the active chronological tool call log during callback or tool
    execution, or `None`.
    """
    return _ACTIVE_TOOL_CALL_LOG.get()


@dataclass
class HydratedAgentConfig:
    """Hydrated agent configuration including auto-loaded local Python and
    environment tools.
    """

    agent_name: str = ""
    agent_dir: Path | None = None
    app_dir: Path | None = None
    agent_config: dict[str, Any] = field(default_factory=dict)
    app_config: dict[str, Any] = field(default_factory=dict)
    local_python_tools: dict[str, Callable[..., Any]] = field(
        default_factory=dict
    )
    env_tools: dict[str, Callable[..., Any]] = field(default_factory=dict)


StateUpdateMode = Literal["rearrange", "in_place", "current_state", "none"]
STATE_REMOVED_SENTINEL = "__ADK_SENTINEL_REMOVED__"
_REMOVED_SENTINELS = frozenset(
    {STATE_REMOVED_SENTINEL, "__CES_STATE_REMOVED__"}
)
_UNDECLARED_VARIABLE_PREFIX = "[undeclared] "
_EXCLUDED_STATE_KEYS = frozenset(
    {
        "_commerce_last_injected_activity_time",
        "_commerce_last_injected_page_location",
        "ada_blackboard_state",
        "ada_blackboard_v2_state",
        "__product_id_counter__",
        "__product_id_mapping__",
        "__product_id_reverse_mapping__",
    }
)
_EXCLUDED_STATE_PREFIXES = (
    "ada_v2_blackboard_state",
    "products_search_result_",
    "products_detail_result_",
    "stores_search_result_",
    "_adk_tool_call_security_states",
    "__gecx_",
)

MODALITY_MAP: dict[str, str] = {
    "TEXT": "TEXT",
    "Text": "TEXT",
    "CHAT": "CHAT",
    "Chat": "CHAT",
    "VOICE": "VOICE",
    "Voice": "VOICE",
}

CHANNEL_MAP: dict[str, str] = {
    "API": "API",
    "GoogleTelephonyPlatform": "GOOGLE_TELEPHONY_PLATFORM",
    "GOOGLE_TELEPHONY_PLATFORM": "GOOGLE_TELEPHONY_PLATFORM",
    "GoogleCCaaSVoice": "CONTACT_CENTER_AS_A_SERVICE",
    "CONTACT_CENTER_AS_A_SERVICE": "CONTACT_CENTER_AS_A_SERVICE",
    "Twilio": "TWILIO",
    "TWILIO": "TWILIO",
    "Five9": "FIVE9",
    "FIVE9": "FIVE9",
    "AudioCodes": "CONTACT_CENTER_INTEGRATION",
    "CONTACT_CENTER_INTEGRATION": "CONTACT_CENTER_INTEGRATION",
    "Whatsapp": "WHATSAPP",
    "WhatsApp": "WHATSAPP",
    "WHATSAPP": "WHATSAPP",
    "Instagram": "INSTAGRAM",
    "INSTAGRAM": "INSTAGRAM",
    "GoogleCCaaSChat": "CONTACT_CENTER_AS_A_SERVICE_CHAT",
    "CONTACT_CENTER_AS_A_SERVICE_CHAT": "CONTACT_CENTER_AS_A_SERVICE_CHAT",
    "WEB_UI": "WEB_UI",
    "WEB": "WEB_UI",
    "WebWidget": "WEB_UI",
    "MOBILE": "MOBILE",
}

_MODALITY_BLOCK_RE = re.compile(
    r"\{@startModality_?([A-Za-z0-9_]+)\}((?:(?!\{@(?:start|end)Modality).)*?)\{@endModality_?\1\}",
    re.DOTALL,
)
_CHANNEL_BLOCK_RE = re.compile(
    r"\{@startChannel_?([A-Za-z0-9_]+)\}((?:(?!\{@(?:start|end)Channel).)*?)\{@endChannel_?\1\}",
    re.DOTALL,
)
TOOL_TAG_RE = re.compile(r"\{@TOOL:\s*([A-Za-z0-9_.-]+)\s*\}")
AGENT_TAG_RE = re.compile(r"\{@AGENT:\s*([A-Za-z0-9_.-]+)\s*\}")
_DOUBLE_BRACE_VAR_RE = re.compile(
    r"\{\{\s*((?:(?:app|user|temp):)?[a-zA-Z_][a-zA-Z0-9_.-]*)(\?)?\s*\}\}"
)
_SINGLE_BRACE_VAR_RE = re.compile(
    r"(?<!\{)\{((?:(?:app|user|temp):)?[a-zA-Z_][a-zA-Z0-9_.-]*)(\?)?\}(?!\})"
)
_LENIENT_STATIC_VAR_RE = re.compile(r"\{\{(.+?)\}\}", re.DOTALL)
_STATIC_BLOCK_RE = re.compile(r"\{\{%(.*?)%\}\}", re.DOTALL)
_DYNAMIC_VAR_RE = re.compile(r"\{([^\{\}]+)\}")
_DYNAMIC_BLOCK_RE = re.compile(r"(?<!\{)\{%(.*?)%\}(?!\})", re.DOTALL)
_IDENTIFIER_RE = re.compile(
    r"^(?:(?:app|user|temp):)?[a-zA-Z_][a-zA-Z0-9_-]*(?:\.[a-zA-Z_][a-zA-Z0-9_-]*)*\??$"
)
_FULL_ENGINE_KEYWORDS = frozenset(
    {
        "if",
        "elif",
        "else",
        "endif",
        "for",
        "in",
        "endfor",
        "and",
        "or",
        "not",
        "is",
        "true",
        "false",
        "none",
        "null",
        "defined",
        "undefined",
        "default",
        "length",
        "upper",
        "loop",
        "macro",
        "endmacro",
    }
)


def coerce_runner_inputs(
    runner: CallbackChainRunner | None = None,
    *,
    agent_dir: str | Path | None = None,
    app_dir: str | Path | None = None,
    agent_config: dict[str, Any] | None = None,
    app_config: dict[str, Any] | None = None,
) -> tuple[
    Path | None,
    Path | None,
    dict[str, Any],
    dict[str, Any],
    str,
    str,
    dict[str, Any],
]:
    """Normalizes `runner` or explicit path/config arguments."""
    if runner is not None:
        ag_dir = runner.agent_dir
        ap_dir = _resolve_app_dir(runner)
        ag_cfg = dict(runner.agent_config or {})
        ap_cfg = dict(runner.app_config or {})
        mod = getattr(runner, "modality", "TEXT") or "TEXT"
        chan = getattr(runner, "channel", "API") or "API"
        mock_t = dict(getattr(runner, "mock_tools", {}) or {})
        return ag_dir, ap_dir, ag_cfg, ap_cfg, mod, chan, mock_t

    ag_dir = Path(agent_dir).resolve() if agent_dir is not None else None
    ap_dir = Path(app_dir).resolve() if app_dir is not None else None
    if ap_dir is None and ag_dir is not None and ag_dir.parent.name == "agents":
        ap_dir = ag_dir.parent.parent

    ag_cfg = dict(agent_config or {})
    if not ag_cfg and ag_dir is not None:
        ag_json = ag_dir / f"{ag_dir.name}.json"
        if ag_json.is_file():
            with contextlib.suppress(Exception):
                ag_cfg = json.loads(ag_json.read_text(encoding="utf-8"))

    ap_cfg = dict(app_config or {})
    if not ap_cfg and ap_dir is not None:
        ap_json = ap_dir / "app.json"
        if ap_json.is_file():
            with contextlib.suppress(Exception):
                ap_cfg = json.loads(ap_json.read_text(encoding="utf-8"))

    return ag_dir, ap_dir, ag_cfg, ap_cfg, "TEXT", "API", {}


def _resolve_app_dir(runner: CallbackChainRunner) -> Path | None:
    if runner.app_dir is not None:
        return runner.app_dir
    if (
        runner.agent_dir is not None
        and runner.agent_dir.parent.name == "agents"
    ):
        return runner.agent_dir.parent.parent
    return None


def read_instruction_source(
    raw_value: str | None,
    *,
    primary_dir: Path | None,
    secondary_dir: Path | None,
    default_filename: str,
) -> str:
    if raw_value:
        val_str = str(raw_value).strip()
        for base in (primary_dir, secondary_dir):
            if base is not None:
                candidate = base / val_str
                if candidate.is_file():
                    return candidate.read_text(encoding="utf-8")
        # If it is not a file path (or does not end with .txt/.md), treat as
        # inline instruction
        if not val_str.endswith((".txt", ".md")) or (
            primary_dir is None and secondary_dir is None
        ):
            return val_str
    for base in (primary_dir, secondary_dir):
        if base is not None:
            fallback = base / default_filename
            if fallback.is_file():
                return fallback.read_text(encoding="utf-8")
    return ""


def _lookup_state_path(path: str, state: dict[str, Any]) -> tuple[bool, Any]:
    if path in state:
        return True, state[path]
    lookup_key = path
    if ":" in path:
        prefix, rest = path.split(":", 1)
        if prefix in ("app", "user", "temp"):
            if prefix in state and isinstance(state[prefix], dict):
                found_sub, val_sub = _lookup_state_path(rest, state[prefix])
                if found_sub:
                    return True, val_sub
            lookup_key = rest
            if lookup_key in state:
                return True, state[lookup_key]
    parts = lookup_key.split(".")
    curr: Any = state
    for part in parts:
        if isinstance(curr, dict) and part in curr:
            curr = curr[part]
        else:
            return False, None
    return True, curr


def _format_template_value(val: Any) -> str:
    """Formats a state value for instruction substitution aligned with CES
    runtime.
    """
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, dict):
        items = [f"{k}={_format_template_value(v)}" for k, v in val.items()]
        return "{" + ", ".join(items) + "}"
    if isinstance(val, list):
        items = [_format_template_value(v) for v in val]
        return "[" + ", ".join(items) + "]"
    return str(val)


def _resolve_template_engine(
    template_engine: Literal["variables_only", "full"] | None,
    state: dict[str, Any],
    agent_config: dict[str, Any],
    app_config: dict[str, Any],
) -> Literal["variables_only", "full"]:
    """Determines whether to use `variables_only` (CES default) or `full`
    engine.
    """
    if template_engine in ("variables_only", "full"):
        return template_engine
    candidates: list[Any] = [
        state.get("template_engine_type"),
        agent_config.get("templateEngineType"),
        agent_config.get("template_engine_type"),
        app_config.get("templateEngineType"),
        app_config.get("template_engine_type"),
    ]
    var_decls = (
        app_config.get("variableDeclarations")
        or app_config.get("variable_declarations")
        or []
    )
    for decl in var_decls:
        if (
            isinstance(decl, dict)
            and decl.get("name") == "template_engine_type"
        ):
            candidates.append(decl.get("default"))
            schema = decl.get("schema")
            if isinstance(schema, dict):
                candidates.append(schema.get("default"))
    for cand in candidates:
        if isinstance(cand, str):
            norm = cand.strip().lower()
            if norm == "full":
                return "full"
            if norm == "variables_only":
                return "variables_only"
    return "variables_only"


def render_agent_instruction(
    runner: CallbackChainRunner | None = None,
    state: dict[str, Any] | None = None,
    *,
    instruction_text: str | None = None,
    agent_dir: str | Path | None = None,
    app_dir: str | Path | None = None,
    agent_config: dict[str, Any] | None = None,
    app_config: dict[str, Any] | None = None,
    template_engine: Literal["variables_only", "full"] | None = None,
    resolve_tags: bool = True,
    substitute_variables: bool = True,
    substitute_single_brace: bool | None = None,
) -> str:
    """Loads and renders `global_instruction.txt` + `instruction.txt` for an
    agent.
    """
    eff_state = state if state is not None else {}
    ag_dir, ap_dir, ag_cfg, ap_cfg, def_mod, def_chan, _ = coerce_runner_inputs(
        runner,
        agent_dir=agent_dir,
        app_dir=app_dir,
        agent_config=agent_config,
        app_config=app_config,
    )

    raw_global = ap_cfg.get("globalInstruction") or ap_cfg.get(
        "global_instruction"
    )
    global_text = read_instruction_source(
        raw_global,
        primary_dir=ap_dir,
        secondary_dir=ag_dir,
        default_filename="global_instruction.txt",
    ).strip()

    raw_agent = (
        instruction_text
        if instruction_text is not None
        else ag_cfg.get("instruction")
    )
    agent_text = (
        str(instruction_text).strip()
        if instruction_text is not None
        else read_instruction_source(
            raw_agent,
            primary_dir=ap_dir,
            secondary_dir=ag_dir,
            default_filename="instruction.txt",
        ).strip()
    )

    pieces = [p for p in (global_text, agent_text) if p]
    rendered = "\n\n".join(pieces)
    if not rendered:
        return ""

    eff_engine = _resolve_template_engine(
        template_engine, eff_state, ag_cfg, ap_cfg
    )
    eff_sub_single = (
        False
        if substitute_single_brace is None
        else bool(substitute_single_brace)
    )

    sess_meta = (
        eff_state.get("_session")
        if isinstance(eff_state.get("_session"), dict)
        else {}
    )
    raw_mod = str(
        sess_meta.get("modality") or eff_state.get("modality") or def_mod
    )
    active_modality = MODALITY_MAP.get(raw_mod, raw_mod.upper())

    raw_chan = str(
        sess_meta.get("channel") or eff_state.get("channel") or def_chan
    )
    active_channel = CHANNEL_MAP.get(raw_chan, raw_chan.upper())

    def _replace_modality(match: re.Match[str]) -> str:
        start_key = match.group(1)
        body = match.group(2)
        if start_key not in MODALITY_MAP:
            return match.group(0)
        target_mod = MODALITY_MAP[start_key]
        if eff_engine == "variables_only":
            return (
                f"{{{{% if _session.modality == '{target_mod}' %}}}}"
                f"{body}"
                f"{{{{% endif %}}}}"
            )
        return body if target_mod == active_modality else ""

    def _replace_channel(match: re.Match[str]) -> str:
        start_key = match.group(1)
        body = match.group(2)
        if start_key not in CHANNEL_MAP:
            return match.group(0)
        target_chan = CHANNEL_MAP[start_key]
        if eff_engine == "variables_only":
            return (
                f"{{{{% if _session.channel == '{target_chan}' %}}}}"
                f"{body}"
                f"{{{{% endif %}}}}"
            )
        return body if target_chan == active_channel else ""

    # Resolve nested modality and channel blocks from innermost to outermost
    while True:
        next_rendered = _MODALITY_BLOCK_RE.sub(_replace_modality, rendered)
        if next_rendered == rendered:
            break
        rendered = next_rendered

    while True:
        next_rendered = _CHANNEL_BLOCK_RE.sub(_replace_channel, rendered)
        if next_rendered == rendered:
            break
        rendered = next_rendered

    if resolve_tags:
        rendered = TOOL_TAG_RE.sub(r"\1", rendered)
        rendered = AGENT_TAG_RE.sub(r"\1", rendered)

    if substitute_variables:

        def _sub_double_var(match: re.Match[str]) -> str:
            var_name = match.group(1).strip()
            is_opt = bool(match.group(2))
            if is_opt and eff_engine == "variables_only":
                return match.group(0)
            found, val = _lookup_state_path(var_name, eff_state)
            if not found or val is None:
                return "" if is_opt else match.group(0)
            return _format_template_value(val)

        def _sub_single_var(match: re.Match[str]) -> str:
            var_name = match.group(1).strip()
            is_opt = bool(match.group(2))
            found, val = _lookup_state_path(var_name, eff_state)
            if not found or val is None:
                return "" if is_opt else match.group(0)
            return _format_template_value(val)

        rendered = _DOUBLE_BRACE_VAR_RE.sub(_sub_double_var, rendered)
        if eff_sub_single:
            rendered = _SINGLE_BRACE_VAR_RE.sub(_sub_single_var, rendered)

    return rendered.strip()


def _translate_ces_tags_for_scope(instruction: str) -> str:
    """Translates modality/channel/tool/agent tags prior to extracting in-scope
    params.
    """
    text = instruction

    def _mod_to_static(m: re.Match[str]) -> str:
        start_key = m.group(1)
        body = m.group(2)
        if start_key not in MODALITY_MAP:
            return m.group(0)
        target_mod = MODALITY_MAP[start_key]
        return (
            f"{{{{% if _session.modality == '{target_mod}' %}}}}"
            f"{body}"
            f"{{{{% endif %}}}}"
        )

    def _chan_to_static(m: re.Match[str]) -> str:
        start_key = m.group(1)
        body = m.group(2)
        if start_key not in CHANNEL_MAP:
            return m.group(0)
        target_chan = CHANNEL_MAP[start_key]
        return (
            f"{{{{% if _session.channel == '{target_chan}' %}}}}"
            f"{body}"
            f"{{{{% endif %}}}}"
        )

    while True:
        nxt = _MODALITY_BLOCK_RE.sub(_mod_to_static, text)
        if nxt == text:
            break
        text = nxt
    while True:
        nxt = _CHANNEL_BLOCK_RE.sub(_chan_to_static, text)
        if nxt == text:
            break
        text = nxt
    text = TOOL_TAG_RE.sub(r"\1", text)
    text = AGENT_TAG_RE.sub(r"\1", text)
    return text


def extract_in_scope_params(
    instruction: str,
    template_engine: Literal["variables_only", "full"] = "variables_only",
) -> set[str]:
    """Extracts root dynamic variable names (`{var}`) referenced in
    `instruction`.

    Excludes static `{{var}}` and `{{% ... %}}` ranges. In `"full"` mode, also
    extracts variables referenced inside dynamic `{% ... %}` control-flow
    blocks.
    """
    if not instruction:
        return set()

    translated = _translate_ces_tags_for_scope(instruction)
    excluded_spans: list[tuple[int, int]] = []
    for m in _LENIENT_STATIC_VAR_RE.finditer(translated):
        excluded_spans.append((m.start(), m.end()))
    for m in _STATIC_BLOCK_RE.finditer(translated):
        excluded_spans.append((m.start(), m.end()))

    def _is_in_excluded(start: int, end: int) -> bool:
        return any(s <= start and end <= e for s, e in excluded_spans)

    variables: set[str] = set()

    def _add_root_var(raw_name: str) -> None:
        cleaned = raw_name[:-1] if raw_name.endswith("?") else raw_name
        root_key = cleaned.split(".", 1)[0]
        if ":" in root_key:
            prefix, unprefixed = root_key.split(":", 1)
            if prefix in ("app", "user", "temp") and unprefixed:
                root_key = unprefixed
        if root_key:
            variables.add(root_key)

    for m in _DYNAMIC_VAR_RE.finditer(translated):
        if _is_in_excluded(m.start(), m.end()):
            continue
        raw_match = m.group(1).strip()
        if _IDENTIFIER_RE.match(raw_match):
            _add_root_var(raw_match)

    if template_engine == "full":
        loop_vars: set[str] = set()
        for m in _DYNAMIC_BLOCK_RE.finditer(translated):
            if _is_in_excluded(m.start(), m.end()):
                continue
            tag_body = m.group(1).strip()
            for_match = re.match(
                r"^for\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+in\s+(.+)$",
                tag_body,
                re.DOTALL,
            )
            if for_match:
                loop_vars.add(for_match.group(1))
                expr_part = for_match.group(2)
            elif tag_body.startswith("endfor"):
                continue
            else:
                expr_part = tag_body
            # Strip quoted string literals, filter names (`| filter`), and `is
            # [not] test`
            expr_clean = re.sub(r"'[^']*'|\"[^\"]*\"", " ", expr_part)
            expr_clean = re.sub(r"\|\s*[a-zA-Z_][a-zA-Z0-9_]*", " ", expr_clean)
            expr_clean = re.sub(
                r"\bis\s+(?:not\s+)?[a-zA-Z_][a-zA-Z0-9_]*", " ", expr_clean
            )
            for token_match in re.finditer(
                r"(?:(?:app|user|temp):)?[a-zA-Z_][a-zA-Z0-9_-]*(?:\.[a-zA-Z_][a-zA-Z0-9_-]*)*",
                expr_clean,
            ):
                tok = token_match.group(0)
                root_tok = tok.split(".", 1)[0]
                if (
                    root_tok.lower() in _FULL_ENGINE_KEYWORDS
                    or root_tok in loop_vars
                ):
                    continue
                _add_root_var(tok)

    return variables


def get_agent_in_scope_params(
    runner: CallbackChainRunner | None = None,
    state: dict[str, Any] | None = None,
    *,
    instruction_text: str | None = None,
    agent_dir: str | Path | None = None,
    app_dir: str | Path | None = None,
    agent_config: dict[str, Any] | None = None,
    app_config: dict[str, Any] | None = None,
    template_engine: Literal["variables_only", "full"] | None = None,
) -> set[str]:
    """Loads `global_instruction.txt` + `instruction.txt` and extracts
    `in_scope_params`.
    """
    eff_state = state if state is not None else {}
    ag_dir, ap_dir, ag_cfg, ap_cfg, _, _, _ = coerce_runner_inputs(
        runner,
        agent_dir=agent_dir,
        app_dir=app_dir,
        agent_config=agent_config,
        app_config=app_config,
    )
    raw_global = ap_cfg.get("globalInstruction") or ap_cfg.get(
        "global_instruction"
    )
    global_text = read_instruction_source(
        raw_global,
        primary_dir=ap_dir,
        secondary_dir=ag_dir,
        default_filename="global_instruction.txt",
    ).strip()

    raw_agent = (
        instruction_text
        if instruction_text is not None
        else ag_cfg.get("instruction")
    )
    agent_text = (
        str(instruction_text).strip()
        if instruction_text is not None
        else read_instruction_source(
            raw_agent,
            primary_dir=ap_dir,
            secondary_dir=ag_dir,
            default_filename="instruction.txt",
        ).strip()
    )
    combined = "\n\n".join(p for p in (global_text, agent_text) if p)
    eff_engine = _resolve_template_engine(
        template_engine, eff_state, ag_cfg, ap_cfg
    )
    return extract_in_scope_params(combined, template_engine=eff_engine)


def sanitize_state_map(state_map: dict[str, Any] | None) -> dict[str, str]:
    """Filters undeclared/internal state keys and formats values (`REMOVED` ->
    `'null'`).
    """
    if not state_map:
        return {}
    sanitized: dict[str, str] = {}
    for key, val in state_map.items():
        if not isinstance(key, str):
            continue
        if (
            key.startswith(_UNDECLARED_VARIABLE_PREFIX)
            or key in _EXCLUDED_STATE_KEYS
            or any(key.startswith(pref) for pref in _EXCLUDED_STATE_PREFIXES)
        ):
            continue
        if val is None or (isinstance(val, str) and val in _REMOVED_SENTINELS):
            sanitized[key] = "null"
        else:
            sanitized[key] = _format_template_value(val)
    return sanitized


def compute_state_delta(
    before_state: dict[str, Any] | None,
    after_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Computes the per-stage `actions.state_delta` between `before_state` and
    `after_state`.
    """
    prev = before_state or {}
    curr = after_state or {}
    delta: dict[str, Any] = {}
    for k, v in curr.items():
        if k not in prev or prev[k] != v or type(prev[k]) is not type(v):
            delta[k] = copy.deepcopy(v)
    for k in prev:
        if k not in curr:
            delta[k] = STATE_REMOVED_SENTINEL
    return delta


def _build_state_update_content(
    pending_state_updates: dict[str, str],
) -> Content:
    parts: list[Part] = [Part.from_text("<state_update>")]
    for key, value in pending_state_updates.items():
        parts.append(Part.from_text(f"{key}: {value}"))
    parts.append(Part.from_text("</state_update>"))
    return Content(role="user", parts=parts)


def build_contents_with_state_updates(
    events: Sequence[Event],
    in_scope_params: set[str] | Sequence[str],
    *,
    mode: StateUpdateMode = "rearrange",
) -> list[Content]:
    """Constructs `LlmRequest.contents` from `events` with `<state_update>`
    injection.

    Modes:
    - `"rearrange"` (default): Reverses `events`, deduplicating in-scope state
      updates per user turn (keeping the latest value) and inserting
      `<state_update>`
      blocks right before each user turn (`author == 'user'`).
    - `"in_place"`: Appends `<state_update>` immediately after each event whose
      `actions.state_delta` modifies an in-scope variable.
    - `"current_state"`: Accumulates the latest values of all in-scope variables
      across `events` and inserts a single `<current_state>` user content block
      immediately before the last user event (`author == 'user'`).
    - `"none"`: Returns `[ev.content for ev in events if ev.content is not
      None]`
      without injecting state updates.
    """
    scope_set = set(in_scope_params or ())
    if mode == "none" or not scope_set:
        return [
            copy.deepcopy(ev.content) for ev in events if ev.content is not None
        ]

    if mode == "in_place":
        result_in_place: list[Content] = []
        for event in events:
            if event.content is not None:
                result_in_place.append(copy.deepcopy(event.content))
            raw_delta = (
                event.actions.state_delta if event.actions is not None else None
            )
            in_scope_delta = {
                k: v
                for k, v in sanitize_state_map(raw_delta).items()
                if k in scope_set
            }
            if in_scope_delta:
                result_in_place.append(
                    _build_state_update_content(in_scope_delta)
                )
        return result_in_place

    if mode == "current_state":
        latest_state: dict[str, str] = {}
        for event in events:
            raw_delta = (
                event.actions.state_delta if event.actions is not None else None
            )
            in_scope_delta = {
                k: v
                for k, v in sanitize_state_map(raw_delta).items()
                if k in scope_set
            }
            latest_state.update(in_scope_delta)
        if not latest_state:
            return [
                copy.deepcopy(ev.content)
                for ev in events
                if ev.content is not None
            ]
        last_user_idx = -1
        for idx in range(len(events) - 1, -1, -1):
            if events[idx].is_user():
                last_user_idx = idx
                break
        cs_parts: list[Part] = [Part.from_text("\n<current_state>\n")]
        for key, value in latest_state.items():
            cs_parts.append(Part.from_text(f"{key}: {value}\n"))
        cs_parts.append(Part.from_text("</current_state>\n"))
        cs_content = Content(role="user", parts=cs_parts)
        result_cs: list[Content] = []
        for idx, ev in enumerate(events):
            if idx == last_user_idx:
                result_cs.append(cs_content)
            if ev.content is not None:
                result_cs.append(copy.deepcopy(ev.content))
        if last_user_idx == -1:
            result_cs.append(cs_content)
        return result_cs

    # Default: mode == "rearrange"
    result_rearrange: list[Content] = []
    pending_state_updates: dict[str, str] = {}
    last_event: Event | None = None
    for event in reversed(events):
        if (
            not event.is_user()
            and last_event is not None
            and last_event.is_user()
            and pending_state_updates
        ):
            result_rearrange.append(
                _build_state_update_content(pending_state_updates)
            )
            pending_state_updates.clear()
        if event.content is not None:
            result_rearrange.append(copy.deepcopy(event.content))
        raw_delta = (
            event.actions.state_delta if event.actions is not None else None
        )
        in_scope_delta = {
            k: v
            for k, v in sanitize_state_map(raw_delta).items()
            if k in scope_set
        }
        for key, value in in_scope_delta.items():
            if key not in pending_state_updates:
                pending_state_updates[key] = value
        last_event = event

    if pending_state_updates:
        result_rearrange.append(
            _build_state_update_content(pending_state_updates)
        )
        pending_state_updates.clear()
    result_rearrange.reverse()
    return result_rearrange


def _parse_google_docstring_args(
    docstring: str | None,
) -> tuple[str, dict[str, str]]:
    """Extracts the summary description and per-argument descriptions from a
    docstring.
    """
    if not docstring:
        return "", {}
    lines = inspect.cleandoc(docstring).splitlines()
    summary_lines: list[str] = []
    arg_descs: dict[str, str] = {}
    in_summary = True
    in_args = False
    current_arg: str | None = None

    section_headers = {
        "Args:",
        "Arguments:",
        "Parameters:",
        "Returns:",
        "Raises:",
        "Yields:",
        "Examples:",
        "Example:",
        "Note:",
        "Notes:",
        "Attributes:",
    }

    for line in lines:
        stripped = line.strip()
        if stripped in section_headers:
            in_summary = False
            if stripped in ("Args:", "Arguments:", "Parameters:"):
                in_args = True
                current_arg = None
            else:
                in_args = False
                current_arg = None
            continue

        if not in_args:
            if in_summary and stripped:
                summary_lines.append(stripped)
            continue

        if not stripped:
            continue

        # Match `param_name (optional type): description` or `param_name:
        # description`
        arg_match = re.match(
            r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\([^)]*\))?\s*:\s*(.*)$",
            stripped,
        )
        if arg_match and (line.startswith(" ") or line.startswith("\t")):
            current_arg = arg_match.group(1)
            arg_descs[current_arg] = arg_match.group(2).strip()
        elif current_arg is not None and (
            line.startswith("    ") or line.startswith("\t")
        ):
            arg_descs[current_arg] = (
                f"{arg_descs[current_arg]} {stripped}".strip()
            )

    return (" ".join(summary_lines).strip(), arg_descs)


def _flatten_bit_or(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _flatten_bit_or(node.left) + _flatten_bit_or(node.right)
    return [node]


def _annotation_to_schema(node: ast.AST | None) -> tuple[dict[str, Any], bool]:
    """Converts a Python AST type annotation into `(json_schema_dict,
    is_optional)`.
    """
    if node is None:
        return {"type": "STRING"}, False

    if isinstance(node, ast.Name):
        ident = node.id
        mapping = {
            "str": "STRING",
            "int": "INTEGER",
            "float": "NUMBER",
            "bool": "BOOLEAN",
            "list": "ARRAY",
            "Sequence": "ARRAY",
            "dict": "OBJECT",
            "Mapping": "OBJECT",
            "Any": "STRING",
        }
        return {"type": mapping.get(ident, "STRING")}, False

    if isinstance(node, ast.Attribute):
        return _annotation_to_schema(ast.Name(id=node.attr))

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        elts = _flatten_bit_or(node)
        non_none = [
            elt
            for elt in elts
            if not (isinstance(elt, ast.Constant) and elt.value is None)
        ]
        has_none = len(non_none) < len(elts)
        if not non_none:
            return {"type": "STRING", "nullable": True}, True
        if len(non_none) == 1:
            schema, inner_opt = _annotation_to_schema(non_none[0])
            if has_none or inner_opt:
                schema = {**schema, "nullable": True}
            return schema, (has_none or inner_opt)
        branch_schemas = [_annotation_to_schema(e)[0] for e in non_none]
        out_schema: dict[str, Any] = {"anyOf": branch_schemas}
        if has_none:
            out_schema["nullable"] = True
        return out_schema, has_none

    if isinstance(node, ast.Subscript):
        base_name = ""
        if isinstance(node.value, ast.Name):
            base_name = node.value.id
        elif isinstance(node.value, ast.Attribute):
            base_name = node.value.attr

        slice_node = node.slice
        if base_name == "Optional":
            schema, _ = _annotation_to_schema(slice_node)
            return {**schema, "nullable": True}, True

        if base_name == "Union" and isinstance(slice_node, ast.Tuple):
            non_none = [
                elt
                for elt in slice_node.elts
                if not (isinstance(elt, ast.Constant) and elt.value is None)
            ]
            has_none = len(non_none) < len(slice_node.elts)
            if not non_none:
                return {"type": "STRING", "nullable": True}, True
            if len(non_none) == 1:
                schema, inner_opt = _annotation_to_schema(non_none[0])
                if has_none or inner_opt:
                    schema = {**schema, "nullable": True}
                return schema, (has_none or inner_opt)
            branch_schemas = [_annotation_to_schema(e)[0] for e in non_none]
            out_schema = {"anyOf": branch_schemas}
            if has_none:
                out_schema["nullable"] = True
            return out_schema, has_none

        if base_name == "Literal":
            elts = (
                slice_node.elts
                if isinstance(slice_node, ast.Tuple)
                else [slice_node]
            )
            enum_vals = [
                elt.value
                for elt in elts
                if isinstance(elt, ast.Constant) and elt.value is not None
            ]
            return {"type": "STRING", "enum": enum_vals}, False

        if base_name in ("list", "List", "Sequence"):
            item_schema, _ = _annotation_to_schema(slice_node)
            return {"type": "ARRAY", "items": item_schema}, False

        if base_name in ("dict", "Dict", "Mapping"):
            return {"type": "OBJECT"}, False

    return {"type": "STRING"}, False


def _extract_python_tool_schema(
    code_path: Path, target_func_name: str = "run"
) -> tuple[str, dict[str, Any] | None]:
    """Parses `code_path` via AST to extract `(docstring_summary,
    parameters_schema)`.
    """
    if not code_path.is_file():
        return "", None
    try:
        source = code_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(code_path))
    except Exception:
        return "", None

    fn_node: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    public_candidates: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == target_func_name:
                fn_node = node
                break
            if not node.name.startswith("_"):
                public_candidates.append(node)

    if fn_node is None and len(public_candidates) == 1:
        fn_node = public_candidates[0]
    if fn_node is None:
        return "", None

    doc_summary, arg_descs = _parse_google_docstring_args(
        ast.get_docstring(fn_node)
    )

    pos_args = list(fn_node.args.posonlyargs) + list(fn_node.args.args)
    defaults = list(fn_node.args.defaults)
    num_required = len(pos_args) - len(defaults)

    properties: dict[str, Any] = {}
    required: list[str] = []

    skipped_names = {
        "self",
        "cls",
        "context",
        "tool_context",
        "callback_context",
    }

    for idx, arg in enumerate(pos_args):
        if arg.arg in skipped_names:
            continue
        prop_schema, is_optional = _annotation_to_schema(arg.annotation)
        if arg_descs.get(arg.arg):
            prop_schema["description"] = arg_descs[arg.arg]
        properties[arg.arg] = prop_schema

        has_default = idx >= num_required
        if not has_default and not is_optional:
            required.append(arg.arg)

    for kw_arg, kw_def in zip(
        fn_node.args.kwonlyargs, fn_node.args.kw_defaults, strict=False
    ):
        if kw_arg.arg in skipped_names:
            continue
        prop_schema, is_optional = _annotation_to_schema(kw_arg.annotation)
        if arg_descs.get(kw_arg.arg):
            prop_schema["description"] = arg_descs[kw_arg.arg]
        properties[kw_arg.arg] = prop_schema
        if kw_def is None and not is_optional:
            required.append(kw_arg.arg)

    schema: dict[str, Any] = {
        "type": "OBJECT",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return doc_summary, schema


def _resolve_tool_files(
    app_dir: Path | None, tool_name: str
) -> tuple[dict[str, Any], Path | None, str]:
    """Locates `<tool_name>.json` and `python_code.py` for `tool_name`."""
    if app_dir is None:
        return {}, None, tool_name

    tool_dir = app_dir / "tools" / tool_name
    tool_cfg: dict[str, Any] = {}
    for cand_json in (
        tool_dir / f"{tool_name}.json",
        tool_dir / "tool.json",
    ):
        if cand_json.is_file():
            try:
                tool_cfg = json.loads(cand_json.read_text(encoding="utf-8"))
                break
            except Exception:
                pass

    py_fn = (
        tool_cfg.get("pythonFunction") or tool_cfg.get("python_function") or {}
    )
    fn_name = str(py_fn.get("name") or tool_cfg.get("name") or tool_name)

    rel_code = py_fn.get("pythonCode") or py_fn.get("python_code")
    code_path: Path | None = None
    if rel_code:
        cand = app_dir / str(rel_code)
        if cand.is_file():
            code_path = cand
    if code_path is None:
        for cand_py in (
            tool_dir / "python_code" / "python_code.py",
            tool_dir / "python_function" / "python_code.py",
            tool_dir / "python_code.py",
        ):
            if cand_py.is_file():
                code_path = cand_py
                break

    return tool_cfg, code_path, fn_name


def build_agent_tool_declarations(
    runner: CallbackChainRunner | None = None,
    *,
    agent_dir: str | Path | None = None,
    app_dir: str | Path | None = None,
    agent_config: dict[str, Any] | None = None,
    app_config: dict[str, Any] | None = None,
    include_end_session: bool | None = None,
) -> list[FunctionDeclaration]:
    """Builds `FunctionDeclaration` schemas for an agent's tools and
    `childAgents`.
    """
    eff_ag_dir, eff_app_dir, eff_agent_cfg, _, _, _, _ = coerce_runner_inputs(
        runner,
        agent_dir=agent_dir,
        app_dir=app_dir,
        agent_config=agent_config,
        app_config=app_config,
    )
    raw_tools = eff_agent_cfg.get("tools") or []
    child_agents_raw = (
        eff_agent_cfg.get("childAgents")
        or eff_agent_cfg.get("child_agents")
        or []
    )
    child_agents: list[str] = [
        str(c).split("/")[-1] for c in child_agents_raw if c
    ]

    declarations: list[FunctionDeclaration] = []
    seen_names: set[str] = set()
    explicit_transfer_tool = False
    explicit_end_session_tool = False

    for item in raw_tools:
        if isinstance(item, str):
            tool_name = item.split("/")[-1].strip()
        elif isinstance(item, dict):
            tool_name = str(
                item.get("name") or item.get("displayName") or ""
            ).strip()
        else:
            continue

        if not tool_name or tool_name in seen_names:
            continue

        if tool_name == "end_session":
            explicit_end_session_tool = True
            continue

        if tool_name == "transfer_to_agent":
            explicit_transfer_tool = True
            continue

        tool_cfg, code_path, fn_name = _resolve_tool_files(
            eff_app_dir, tool_name
        )
        py_fn = (
            tool_cfg.get("pythonFunction")
            or tool_cfg.get("python_function")
            or {}
        )
        openapi_cfg = (
            tool_cfg.get("openApiTool") or tool_cfg.get("open_api_tool") or {}
        )
        ds_cfg = (
            tool_cfg.get("dataStoreTool")
            or tool_cfg.get("data_store_tool")
            or {}
        )

        doc_summary, ast_schema = (
            _extract_python_tool_schema(code_path, fn_name)
            if code_path is not None
            else ("", None)
        )

        description = (
            py_fn.get("description")
            or openapi_cfg.get("description")
            or ds_cfg.get("description")
            or tool_cfg.get("description")
            or doc_summary
            or f"Executes tool {tool_name}."
        )
        ds_default_params = (
            {
                "type": "OBJECT",
                "properties": {
                    "query": {
                        "type": "STRING",
                        "description": "Search query string.",
                    }
                },
                "required": ["query"],
            }
            if ds_cfg
            else None
        )
        parameters = (
            py_fn.get("parameters")
            or py_fn.get("inputSchema")
            or py_fn.get("input_schema")
            or openapi_cfg.get("parameters")
            or openapi_cfg.get("inputSchema")
            or openapi_cfg.get("input_schema")
            or ds_cfg.get("parameters")
            or tool_cfg.get("parameters")
            or tool_cfg.get("inputSchema")
            or tool_cfg.get("input_schema")
            or ast_schema
            or ds_default_params
            or {"type": "OBJECT", "properties": {}}
        )

        seen_names.add(tool_name)
        declarations.append(
            FunctionDeclaration(
                name=tool_name,
                description=str(description),
                parameters=copy.deepcopy(parameters),
            )
        )

    ds_tool = eff_agent_cfg.get("dataStoreTool") or eff_agent_cfg.get(
        "data_store_tool"
    )
    if isinstance(ds_tool, dict):
        ds_name = str(
            ds_tool.get("name") or ds_tool.get("displayName") or "data_store"
        ).strip()
        if ds_name and ds_name not in seen_names:
            seen_names.add(ds_name)
            declarations.append(
                FunctionDeclaration(
                    name=ds_name,
                    description=str(
                        ds_tool.get("description")
                        or f"Searches data store {ds_name}."
                    ),
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "query": {
                                "type": "STRING",
                                "description": "Search query string.",
                            }
                        },
                        "required": ["query"],
                    },
                )
            )

    if (child_agents or explicit_transfer_tool) and (
        "transfer_to_agent" not in seen_names
    ):
        seen_names.add("transfer_to_agent")
        child_desc_parts: list[str] = []
        if eff_app_dir is not None:
            for child_name in child_agents:
                child_json = (
                    eff_app_dir / "agents" / child_name / f"{child_name}.json"
                )
                if child_json.is_file():
                    try:
                        c_cfg = json.loads(
                            child_json.read_text(encoding="utf-8")
                        )
                        c_desc = c_cfg.get("description")
                        if c_desc:
                            child_desc_parts.append(f"{child_name}: {c_desc}")
                    except Exception:
                        pass

        agent_param_desc = "Target child agent name."
        if child_desc_parts:
            agent_param_desc = (
                "Target child agent name. Available child agents: "
                + "; ".join(child_desc_parts)
            )

        agent_prop: dict[str, Any] = {
            "type": "STRING",
            "description": agent_param_desc,
        }
        if child_agents:
            agent_prop["enum"] = list(child_agents)

        declarations.append(
            FunctionDeclaration(
                name="transfer_to_agent",
                description=(
                    "Transfers the conversation to a specialized child agent."
                ),
                parameters={
                    "type": "OBJECT",
                    "properties": {
                        "agent_name": agent_prop,
                    },
                    "required": ["agent_name"],
                },
            )
        )

    should_include_end_session = explicit_end_session_tool
    if include_end_session is True:
        should_include_end_session = True
    elif include_end_session is False:
        should_include_end_session = False
    elif not should_include_end_session:
        raw_inst = read_instruction_source(
            eff_agent_cfg.get("instruction"),
            primary_dir=eff_app_dir,
            secondary_dir=eff_ag_dir,
            default_filename="instruction.txt",
        )
        if "end_session" in raw_inst:
            should_include_end_session = True

    if should_include_end_session and "end_session" not in seen_names:
        seen_names.add("end_session")
        declarations.append(
            FunctionDeclaration(
                name="end_session",
                description="Ends the current conversation session.",
                parameters={
                    "type": "OBJECT",
                    "properties": {
                        "reason": {
                            "type": "STRING",
                            "description": "Reason for ending the session.",
                        },
                        "session_escalated": {
                            "type": "BOOLEAN",
                            "description": (
                                "Whether the session was escalated to a human "
                                "agent."
                            ),
                        },
                    },
                },
            )
        )

    return declarations


def _snake_to_camel_alias(name: str) -> str:
    parts = re.split(r"[_\s-]+", name)
    if not parts:
        return name
    return parts[0] + "".join(
        p[:1].upper() + p[1:] if p else "" for p in parts[1:]
    )


def _camel_to_snake_alias(name: str) -> str:
    norm = name.replace("-", "_").replace(" ", "_")
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", norm)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _wrap_local_python_tool_return(result: Any) -> Any:
    """Wrap local Python tool return values into `MockExternalResponse` /
    `ExternalResponse`.
    """
    if isinstance(result, (ExternalResponse, MockExternalResponse)):
        return result
    if isinstance(result, dict):
        return MockExternalResponse(result)
    return MockExternalResponse({"result": result})


class _ToolFunctionResolutionError(ValueError):
    """Raised when a local Python tool's target function cannot be unambiguously
    resolved.
    """


def _compile_local_tool_wrapper(
    tool_name: str,
    fn_name: str,
    code_path: Path,
    runner: CallbackChainRunner | None = None,
    *,
    mock_tools: dict[str, Any] | None = None,
) -> Callable[..., Any] | None:
    """Compiles `code_path` and returns a callable injecting `context` & `tools`
    globals.
    """
    try:
        source = code_path.read_text(encoding="utf-8")
        compiled = compile(source, str(code_path), "exec")
    except Exception:
        return None

    try:
        tree = ast.parse(source, filename=str(code_path))
        top_fn_names = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if top_fn_names and fn_name not in top_fn_names:
            pub_fn_names = [n for n in top_fn_names if not n.startswith("_")]
            if len(pub_fn_names) > 1:
                raise _ToolFunctionResolutionError(
                    f"Function '{fn_name}' not found in tool file "
                    f"'{code_path}', "
                    "and multiple public candidate functions exist: "
                    f"{pub_fn_names}."
                )
    except _ToolFunctionResolutionError:
        raise
    except Exception:
        pass

    def _wrapped_tool(*args: Any, **kwargs: Any) -> Any:
        call_kwargs: dict[str, Any] = {}
        active_ctx: ToolContext | CallbackContext | None = None

        if (
            len(args) == 2
            and isinstance(args[0], dict)
            and isinstance(args[1], (CallbackContext, ToolContext))
            and not kwargs
        ):
            call_kwargs = copy.deepcopy(args[0])
            active_ctx = args[1]
            pos_args: tuple[Any, ...] = ()
        elif len(args) == 1 and isinstance(args[0], dict) and not kwargs:
            call_kwargs = copy.deepcopy(args[0])
            pos_args = ()
        else:
            pos_args = args
            call_kwargs = dict(kwargs)
            if isinstance(
                call_kwargs.get("context"), (CallbackContext, ToolContext)
            ):
                active_ctx = call_kwargs.pop("context")

        if active_ctx is None:
            ctx_from_var = _ACTIVE_EXECUTION_CTX.get()
            if ctx_from_var is not None:
                active_ctx = ctx_from_var
            else:
                default_img = (
                    runner.image_version
                    if runner is not None and hasattr(runner, "image_version")
                    else "v1"
                )
                active_ctx = ToolContext(state={}, _image_version=default_img)

        active_log = _ACTIVE_TOOL_CALL_LOG.get()
        tool_log: list[dict[str, Any]] = (
            active_log if active_log is not None else []
        )
        # Precedence: runner.mock_tools (env < local-py < ctor user mocks) <
        # call-level mock_tools
        mock_map: dict[str, Any] = {
            **(
                runner.mock_tools
                if runner is not None and getattr(runner, "mock_tools", None)
                else {}
            ),
            **(mock_tools or {}),
        }
        sync_tools = MockTools(mock_map, tool_log)
        ns: dict[str, Any] = {
            "__builtins__": __builtins__,
            "__name__": f"cxas_local_tool_{tool_name}",
            "__file__": str(code_path),
            "context": active_ctx,
            "tool_context": active_ctx,
            "callback_context": active_ctx,
            "tools": sync_tools,
            "async_tools": MockAsyncTools(sync_tools),
            "StatusError": StatusError,
            "ExternalResponse": ExternalResponse,
            "HttpMethod": HttpMethod,
        }
        for attr_name in dir(callback_libs):
            if not attr_name.startswith("_"):
                ns.setdefault(attr_name, getattr(callback_libs, attr_name))

        exec(compiled, ns)
        target_fn = ns.get(fn_name)
        if not callable(target_fn):
            candidate_fns = [
                val
                for val in ns.values()
                if inspect.isfunction(val)
                and val.__module__ == f"cxas_local_tool_{tool_name}"
                and not val.__name__.startswith("_")
            ]
            if len(candidate_fns) == 1:
                target_fn = candidate_fns[0]
            elif len(candidate_fns) > 1:
                cand_names = [fn.__name__ for fn in candidate_fns]
                raise _ToolFunctionResolutionError(
                    f"Function '{fn_name}' not found in tool file "
                    f"'{code_path}', "
                    "and multiple public candidate functions exist: "
                    f"{cand_names}."
                )
        if not callable(target_fn):
            raise _ToolFunctionResolutionError(
                f"Function '{fn_name}' not found in tool file '{code_path}'."
            )

        sig = inspect.signature(target_fn)
        params = list(sig.parameters.values())
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
        pos_params = [
            p
            for p in params
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        required_pos_params = [
            p for p in pos_params if p.default is inspect.Parameter.empty
        ]

        if pos_args:
            result = target_fn(*pos_args, **call_kwargs)
        elif has_varkw:
            result = target_fn(**call_kwargs)
        else:
            accepted = {
                p.name
                for p in params
                if p.kind
                in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
            }
            filtered_kwargs = {
                k: v for k, v in call_kwargs.items() if k in accepted
            }
            if filtered_kwargs or not call_kwargs or not params:
                result = target_fn(**filtered_kwargs)
            elif len(pos_params) >= 1 and len(required_pos_params) <= 1:
                result = target_fn(call_kwargs)
            else:
                result = target_fn(**call_kwargs)

        if isinstance(ns.get("context"), (CallbackContext, ToolContext)):
            active_ctx.state = ns["context"].state
        return _wrap_local_python_tool_return(result)

    _wrapped_tool.__name__ = tool_name
    _wrapped_tool._cxas_tool_source = "local-python"  # type: ignore[attr-defined]
    return _wrapped_tool


def load_local_python_tools(
    runner: CallbackChainRunner | None = None,
    *,
    agent_dir: str | Path | None = None,
    app_dir: str | Path | None = None,
    agent_config: dict[str, Any] | None = None,
    app_config: dict[str, Any] | None = None,
    mock_tools: dict[str, Any] | None = None,
) -> dict[str, Callable[..., Any]]:
    """Loads local Python tool implementations from `app_dir / 'tools'`."""
    ag_dir, ap_dir, ag_cfg, _, _, _, _ = coerce_runner_inputs(
        runner,
        agent_dir=agent_dir,
        app_dir=app_dir,
        agent_config=agent_config,
        app_config=app_config,
    )
    if ap_dir is None and ag_dir is not None and ag_dir.parent.name == "agents":
        ap_dir = ag_dir.parent.parent
    if ap_dir is None:
        return {}

    candidate_names: list[str] = []
    for item in ag_cfg.get("tools") or []:
        if isinstance(item, str):
            t_name = item.split("/")[-1].strip()
        elif isinstance(item, dict):
            t_name = str(
                item.get("name") or item.get("displayName") or ""
            ).strip()
        else:
            continue
        if t_name and t_name not in ("end_session", "transfer_to_agent"):
            candidate_names.append(t_name)

    tools_root = ap_dir / "tools"
    if tools_root.is_dir():
        for sub in sorted(tools_root.iterdir()):
            if sub.is_dir() and sub.name not in candidate_names:
                candidate_names.append(sub.name)

    loaded: dict[str, Callable[..., Any]] = {}
    for tool_name in candidate_names:
        tool_cfg, code_path, fn_name = _resolve_tool_files(ap_dir, tool_name)
        if code_path is None:
            continue
        wrapper = _compile_local_tool_wrapper(
            tool_name,
            fn_name,
            code_path,
            runner,
            mock_tools=mock_tools,
        )
        if wrapper is not None:
            disp_name = str(tool_cfg.get("displayName") or "").strip()
            names_to_register = [tool_name, fn_name]
            if disp_name:
                names_to_register.append(disp_name)
            for base_name in names_to_register:
                if not base_name:
                    continue
                loaded.setdefault(base_name, wrapper)
                snake_alias = _camel_to_snake_alias(base_name)
                camel_alias = _snake_to_camel_alias(base_name)
                if snake_alias:
                    loaded.setdefault(snake_alias, wrapper)
                if camel_alias:
                    loaded.setdefault(camel_alias, wrapper)

    return loaded


def load_agent_from_app_dir(
    app_dir: str | Path,
    agent_name: str | None = None,
    *,
    runner: CallbackChainRunner | None = None,
    mock_tools: dict[str, Any] | None = None,
    env_tools: Mapping[str, Callable[..., Any]] | None = None,
) -> HydratedAgentConfig:
    """Hydrate agent configuration, environment tools, and local Python tools
    from `app_dir`.
    """
    ap_path = Path(app_dir).resolve()
    app_cfg: dict[str, Any] = {}
    app_json = ap_path / "app.json"
    if app_json.is_file():
        try:
            app_cfg = json.loads(app_json.read_text(encoding="utf-8"))
        except Exception:
            app_cfg = {}

    target_agent = (
        agent_name
        or str(app_cfg.get("rootAgent") or "").strip()
        or "root_agent"
    )
    ag_dir = ap_path / "agents" / target_agent
    ag_cfg: dict[str, Any] = {}
    ag_json = ag_dir / f"{target_agent}.json"
    if ag_json.is_file():
        try:
            ag_cfg = json.loads(ag_json.read_text(encoding="utf-8"))
        except Exception:
            ag_cfg = {}

    env_tools_map = dict(env_tools or {})
    merged_mocks = {**env_tools_map, **(mock_tools or {})}
    local_py_tools = load_local_python_tools(
        runner,
        agent_dir=ag_dir if ag_dir.is_dir() else None,
        app_dir=ap_path,
        agent_config=ag_cfg,
        app_config=app_cfg,
        mock_tools=merged_mocks,
    )
    return HydratedAgentConfig(
        agent_name=target_agent,
        agent_dir=ag_dir if ag_dir.is_dir() else None,
        app_dir=ap_path,
        agent_config=ag_cfg,
        app_config=app_cfg,
        local_python_tools=local_py_tools,
        env_tools=env_tools_map,
    )
