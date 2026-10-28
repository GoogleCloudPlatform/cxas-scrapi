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

"""Utility for running unit tests and chains for CES agent callbacks."""

from __future__ import annotations

import copy
import glob
import io
import json
import logging
import os
import sys
import tempfile
import time
import typing
from collections.abc import Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import pytest

from cxas_scrapi.core.agents import Agents
from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.common import Common
from cxas_scrapi.evals.callback_chain import (
    CHAIN_LLM_ENV,
    CHAIN_MODE_ENV,
    STAGE_TO_FUNC_NAME,
    STATE_REMOVED_SENTINEL,
    VALID_CHANNELS,
    VALID_MODALITIES,
    AgentAssertionError,
    BenchmarkComparisonReport,
    CallbackChainLintFinding,
    CallbackChainRunner,
    CallbackSpec,
    CallbackStepTrace,
    CallbackTrace,
    ErrorPhase,
    ExecutionMode,
    HydratedAgentConfig,
    ImageVersion,
    LlmStepTrace,
    MockLlm,
    ModeDiffReport,
    SessionExpectation,
    SessionMetricsSummary,
    SessionSimulator,
    SnapshotManager,
    StageExpectation,
    StageType,
    StateUpdateMode,
    ToolSpy,
    ToolStepTrace,
    TurnExpectation,
    TurnTrace,
    UsageMetadata,
    build_agent_tool_declarations,
    build_contents_with_state_updates,
    compare_execution_metrics,
    compute_state_delta,
    expect,
    expect_session,
    expect_stage,
    expect_turn,
    extract_in_scope_params,
    get_agent_in_scope_params,
    lint_stage_chain,
    load_agent_from_app_dir,
    load_local_python_tools,
    normalize_image_version,
    normalize_stage,
    render_agent_instruction,
    sanitize_state_map,
    serialize_session_snapshot,
    serialize_stage_snapshot,
    serialize_turn_snapshot,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CHAIN_LLM_ENV",
    "CHAIN_MODE_ENV",
    "STATE_REMOVED_SENTINEL",
    "VALID_CHANNELS",
    "VALID_MODALITIES",
    "AgentAssertionError",
    "BenchmarkComparisonReport",
    "CallbackChainLintFinding",
    "CallbackChainRunner",
    "CallbackEvals",
    "CallbackSpec",
    "CallbackStepTrace",
    "CallbackTrace",
    "ErrorPhase",
    "ExecutionMode",
    "HydratedAgentConfig",
    "ImageVersion",
    "LlmStepTrace",
    "MockLlm",
    "ModeDiffReport",
    "SessionExpectation",
    "SessionMetricsSummary",
    "SessionSimulator",
    "SnapshotManager",
    "StageExpectation",
    "StageType",
    "StateUpdateMode",
    "ToolSpy",
    "ToolStepTrace",
    "TurnExpectation",
    "TurnTrace",
    "UsageMetadata",
    "_synthesize_chained_callback_module",
    "build_agent_tool_declarations",
    "build_contents_with_state_updates",
    "compare_execution_metrics",
    "compute_state_delta",
    "expect",
    "expect_session",
    "expect_stage",
    "expect_turn",
    "extract_in_scope_params",
    "get_agent_in_scope_params",
    "lint_stage_chain",
    "load_agent_from_app_dir",
    "load_local_python_tools",
    "normalize_image_version",
    "normalize_stage",
    "render_agent_instruction",
    "sanitize_state_map",
    "serialize_session_snapshot",
    "serialize_stage_snapshot",
    "serialize_turn_snapshot",
]


def _proto_to_dict(obj: Any) -> dict[str, Any] | None:
    """Converts a proto-plus message or dict to a plain dict."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return copy.deepcopy(obj)
    cls = type(obj)
    if hasattr(cls, "to_dict") and callable(cls.to_dict):
        for kwargs in (
            {
                "use_integers_for_enums": False,
                "always_print_fields_with_no_presence": False,
            },
            {
                "use_integers_for_enums": False,
                "including_default_value_fields": False,
            },
            {"use_integers_for_enums": False},
            {},
        ):
            try:
                res = cls.to_dict(obj, **kwargs)
                if isinstance(res, dict):
                    return res
            except TypeError:
                continue
    return None


def _extract_app_config_from_proto(
    app_proto: Any,
    explicit_app_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Constructs `app_config` from an `App` proto and explicit config."""
    extracted: dict[str, Any] = {}

    if app_proto is not None:
        proto_dict = _proto_to_dict(app_proto)
        if isinstance(proto_dict, dict):
            var_decls = (
                proto_dict.get("variable_declarations")
                or proto_dict.get("variableDeclarations")
                or []
            )
            normalized_decls: list[dict[str, Any]] = []
            for vd in var_decls:
                if not isinstance(vd, dict):
                    continue
                vd_copy = copy.deepcopy(vd)
                schema = vd_copy.get("schema")
                if isinstance(schema, dict):
                    if "type" not in schema and "type_" in schema:
                        schema["type"] = schema["type_"]
                    if (
                        str(schema.get("type", "")).upper() == "INTEGER"
                        and isinstance(schema.get("default"), float)
                        and schema["default"].is_integer()
                    ):
                        schema["default"] = int(schema["default"])
                normalized_decls.append(vd_copy)
            if normalized_decls:
                extracted["variable_declarations"] = normalized_decls

            ehs = proto_dict.get("error_handling_settings") or proto_dict.get(
                "errorHandlingSettings"
            )
            if isinstance(ehs, dict) and ehs:
                extracted["error_handling_settings"] = copy.deepcopy(ehs)

            tz_settings = proto_dict.get(
                "time_zone_settings"
            ) or proto_dict.get("timeZoneSettings")
            if isinstance(tz_settings, dict) and tz_settings:
                extracted["time_zone_settings"] = copy.deepcopy(tz_settings)

    if explicit_app_config:
        extracted.update(copy.deepcopy(explicit_app_config))

    return extracted if extracted else None


def _synthesize_chained_callback_module(
    stage: StageType,
    callback_codes: Sequence[str | tuple[str, str] | dict[str, Any]],
    app_config: dict[str, Any] | None = None,
) -> str:
    """Synthesizes a Python module executing multiple callbacks sequentially.

    Avoids executing callback bodies into a shared top-level namespace at import
    time (preventing helper collisions and import-time side effects, T2), and
    uses `runner`'s `new_session` and `_ces_session_initialized` mechanism.
    Exposes:
    - `runner`: the configured `CallbackChainRunner` (`raise_on_error=True`)
    - `patch_globals(**kwargs)` / `PATCHED_GLOBALS`: explicit global patching
    - `callbacks[name]`: isolated per-callback `SimpleNamespace` loaded on
      demand
    - automatic forwarding of any attributes patched onto the module after
      import
    """
    norm_stage = normalize_stage(stage)
    func_name = STAGE_TO_FUNC_NAME[norm_stage]
    code_literals = ",\n    ".join(repr(c) for c in callback_codes)
    return (
        "import types as _types\n"
        "from cxas_scrapi.evals.callback_evals import CallbackChainRunner\n"
        f"_CHAIN_STAGE = {norm_stage!r}\n"
        f"_CHAIN_FUNC_NAME = {func_name!r}\n"
        f"_CHAIN_CODES = [\n    {code_literals}\n]\n"
        f"_CHAIN_APP_CONFIG = {app_config!r}\n"
        "runner = CallbackChainRunner.from_callbacks(\n"
        "    {_CHAIN_STAGE: _CHAIN_CODES},\n"
        "    app_config=_CHAIN_APP_CONFIG,\n"
        "    new_session=bool(_CHAIN_APP_CONFIG),\n"
        "    raise_on_error=True,\n"
        ")\n"
        "PATCHED_GLOBALS = {}\n\n"
        "def patch_globals(**kwargs):\n"
        "    PATCHED_GLOBALS.update(kwargs)\n\n"
        "class _CallbackNamespaceMap(dict):\n"
        "    def __missing__(self, key):\n"
        "        for spec in runner.get_callbacks(_CHAIN_STAGE):\n"
        "            if spec.name == key:\n"
        "                ns = dict(runner._build_base_namespace({}, []))\n"
        "                exec("
        "compile(spec.python_code, f'<{spec.name}>', 'exec'), ns)\n"
        "                mod_ns = _types.SimpleNamespace(**{\n"
        "                    k: v for k, v in ns.items() "
        "if not k.startswith('__')\n"
        "                })\n"
        "                self[key] = mod_ns\n"
        "                return mod_ns\n"
        "        raise KeyError("
        "f'Unknown callback {key!r} in stage {_CHAIN_STAGE!r}')\n\n"
        "callbacks = _CallbackNamespaceMap()\n"
        "_RESERVED_NAMES = frozenset({\n"
        "    _CHAIN_FUNC_NAME, 'runner', 'PATCHED_GLOBALS', 'patch_globals',\n"
        "    'callbacks', '_CallbackNamespaceMap', '_RESERVED_NAMES',\n"
        "    '_INITIAL_GLOBALS', '_collect_patched_globals',\n"
        "})\n"
        "_INITIAL_GLOBALS = dict(globals())\n\n"
        "def _collect_patched_globals():\n"
        "    patched = dict(PATCHED_GLOBALS)\n"
        "    for k, v in globals().items():\n"
        "        if ("
        "k in _RESERVED_NAMES or k.startswith('_CHAIN_') or k.startswith('_')"
        "):\n"
        "            continue\n"
        "        if ("
        "k not in _INITIAL_GLOBALS or _INITIAL_GLOBALS[k] is not v"
        "):\n"
        "            patched[k] = v\n"
        "    return patched\n\n"
        f"def {func_name}(*args, **kwargs):\n"
        "    _extra = _collect_patched_globals()\n"
        "    if _CHAIN_STAGE in ('before_agent', 'after_agent'):\n"
        "        ctx = args[0] if args else kwargs['callback_context']\n"
        "        out, _ = runner.run_stage("
        "_CHAIN_STAGE, ctx, extra_globals=_extra)\n"
        "        return out\n"
        "    if _CHAIN_STAGE == 'before_model':\n"
        "        ctx = args[0] if args else kwargs['callback_context']\n"
        "        req = args[1] if len(args) > 1 else kwargs['llm_request']\n"
        "        out, _ = runner.run_stage(\n"
        "            'before_model', ctx, "
        "llm_request=req, extra_globals=_extra\n"
        "        )\n"
        "        return out\n"
        "    if _CHAIN_STAGE == 'after_model':\n"
        "        ctx = args[0] if args else kwargs['callback_context']\n"
        "        resp = args[1] if len(args) > 1 else kwargs['llm_response']\n"
        "        out, _ = runner.run_stage(\n"
        "            'after_model', ctx, "
        "llm_response=resp, extra_globals=_extra\n"
        "        )\n"
        "        return out\n"
        "    if _CHAIN_STAGE == 'before_tool':\n"
        "        tool = args[0] if args else kwargs['tool']\n"
        "        tool_in = args[1] if len(args) > 1 else kwargs['input']\n"
        "        ctx = (\n"
        "            args[2] if len(args) > 2\n"
        "            else kwargs.get('tool_context') "
        "or kwargs.get('callback_context')\n"
        "        )\n"
        "        out, _ = runner.run_stage(\n"
        "            'before_tool', ctx, tool=tool, "
        "tool_input=tool_in, extra_globals=_extra\n"
        "        )\n"
        "        return out\n"
        "    if _CHAIN_STAGE == 'after_tool':\n"
        "        tool = args[0] if args else kwargs['tool']\n"
        "        tool_in = args[1] if len(args) > 1 else kwargs['input']\n"
        "        ctx = (\n"
        "            args[2] if len(args) > 2\n"
        "            else kwargs.get('tool_context') "
        "or kwargs.get('callback_context')\n"
        "        )\n"
        "        tool_resp = (\n"
        "            args[3] if len(args) > 3 else kwargs['tool_response']\n"
        "        )\n"
        "        out, _ = runner.run_stage(\n"
        "            'after_tool', ctx, tool=tool, tool_input=tool_in,\n"
        "            tool_response=tool_resp, extra_globals=_extra\n"
        "        )\n"
        "        return out\n"
    )


class CallbackEvals:
    """Provides methods for orchestrating and executing agent callback tests."""

    def __init__(self) -> None:
        self.last_metrics_summaries: dict[str, SessionMetricsSummary] = {}
        self.last_mode_diff_reports: dict[str, ModeDiffReport] = {}
        self.last_turn_traces: dict[str, list[TurnTrace]] = {}
        self.last_stage_traces: dict[str, list[CallbackTrace]] = {}
        self.last_lint_findings: list[CallbackChainLintFinding] = []

    @staticmethod
    def _warn_if_xdist_args(pytest_args: list[str] | None) -> None:
        """Warns when pytest-xdist worker flags are passed."""
        if not pytest_args:
            return
        for arg in pytest_args:
            if (
                arg == "-n"
                or arg.startswith("-n")
                or arg == "--numprocesses"
                or arg.startswith("--numprocesses=")
                or arg == "--dist"
                or arg.startswith("--dist=")
            ):
                logger.warning(
                    "include_metrics and last_metrics_summaries use in-process "
                    "turn collection and do not support pytest-xdist workers "
                    "(%r).",
                    arg,
                )
                break

    def _fetch_app_proto(self, app_name: str, *, strict: bool = False) -> Any:
        """Fetches the `App` proto via `Apps(...).get_app(app_name)`."""
        project_id = Common._get_project_id(app_name)
        location = Common._get_location(app_name)
        try:
            if project_id and location:
                apps_client = Apps(project_id=project_id, location=location)
            else:
                apps_client = Apps()
            return apps_client.get_app(app_name)
        except Exception as e:
            logger.warning(
                "Could not fetch App proto via Apps.get_app(%r): %s",
                app_name,
                e,
            )
            if strict:
                raise
            return None

    def test_single_callback_for_agent(
        self,
        app_name: str,
        agent_name: str,
        callback_type: str,
        test_file_path: str,
        log_file: str | None = None,
        pytest_args: list[str] | None = None,
        app_config: dict[str, Any] | None = None,
        *,
        strict: bool = False,
        include_metrics: bool = False,
    ) -> pd.DataFrame:
        """Runs test against callback(s) fetched from the agent and app protos.

        Supports both single callbacks and multi-callback chains in the same
        lifecycle stage. Automatically:
        - Fetches the `App` proto via `Apps(...).get_app(app_name)` to prepend
          `global_before_model_callbacks` / `global_after_model_callbacks` and
          extract `variable_declarations` and `error_handling_settings` into
          `app_config`.
        - Skips disabled callbacks (`disabled=True`).
        """
        self.last_metrics_summaries.clear()
        self.last_turn_traces.clear()
        self.last_stage_traces.clear()
        self._warn_if_xdist_args(pytest_args)
        agents_client = Agents(app_name=app_name)

        try:
            agents_map = agents_client.get_agents_map(reverse=True)
            agent_uri = agents_map.get(agent_name)
            if agent_uri is None:
                raise ValueError(
                    f"Agent '{agent_name}' not found in "
                    "applications agents map."
                )
            agent = agents_client.get_agent(agent_uri.split("/")[-1])
        except Exception as e:
            logger.error(f"Failed to get agent {agent_name}: {e}")
            raise ValueError(
                f"Failed to get agent {agent_name} from application to "
                f"run callback test."
            ) from e

        all_callbacks = {
            "before_model": getattr(agent, "before_model_callbacks", None),
            "before_model_callback": getattr(
                agent, "before_model_callbacks", None
            ),
            "before_model_callbacks": getattr(
                agent, "before_model_callbacks", None
            ),
            "after_model": getattr(agent, "after_model_callbacks", None),
            "after_model_callback": getattr(
                agent, "after_model_callbacks", None
            ),
            "after_model_callbacks": getattr(
                agent, "after_model_callbacks", None
            ),
            "before_tool": getattr(agent, "before_tool_callbacks", None),
            "before_tool_callback": getattr(
                agent, "before_tool_callbacks", None
            ),
            "before_tool_callbacks": getattr(
                agent, "before_tool_callbacks", None
            ),
            "after_tool": getattr(agent, "after_tool_callbacks", None),
            "after_tool_callback": getattr(agent, "after_tool_callbacks", None),
            "after_tool_callbacks": getattr(
                agent, "after_tool_callbacks", None
            ),
            "before_agent": getattr(agent, "before_agent_callbacks", None),
            "before_agent_callback": getattr(
                agent, "before_agent_callbacks", None
            ),
            "before_agent_callbacks": getattr(
                agent, "before_agent_callbacks", None
            ),
            "after_agent": getattr(agent, "after_agent_callbacks", None),
            "after_agent_callback": getattr(
                agent, "after_agent_callbacks", None
            ),
            "after_agent_callbacks": getattr(
                agent, "after_agent_callbacks", None
            ),
        }
        if callback_type not in all_callbacks:
            raise ValueError(
                f"No callback found of type {callback_type} for agent "
                f"{agent_name}"
            )
        callback = all_callbacks.get(callback_type) or []
        norm_stage = normalize_stage(callback_type)

        app_proto = self._fetch_app_proto(app_name, strict=strict)
        effective_app_config = _extract_app_config_from_proto(
            app_proto, explicit_app_config=app_config
        )

        # Collect any global before_model / after_model callbacks from App proto
        global_specs: list[dict[str, Any]] = []
        if norm_stage in ("before_model", "after_model"):
            global_attr = f"global_{norm_stage}_callbacks"
            raw_globals = (
                getattr(app_proto, global_attr, None)
                if app_proto is not None
                else None
            )
            if isinstance(raw_globals, Sequence) and not isinstance(
                raw_globals, (str, bytes)
            ):
                for idx, gcb in enumerate(raw_globals):
                    if getattr(gcb, "disabled", False) is True:
                        continue
                    gcode = getattr(gcb, "python_code", None)
                    if isinstance(gcode, str) and gcode:
                        gname = (
                            getattr(gcb, "description", "")
                            or getattr(gcb, "name", "")
                            or f"global_{norm_stage}_{idx + 1:02d}"
                        )
                        global_specs.append(
                            {
                                "name": str(gname),
                                "python_code": gcode,
                                "is_global": True,
                            }
                        )

        if not callback and not global_specs:
            raise ValueError(
                f"No callback found of type {callback_type} for agent "
                f"{agent_name}"
            )

        enabled_callbacks = [
            cb for cb in callback if getattr(cb, "disabled", False) is not True
        ]
        if not enabled_callbacks and not global_specs:
            raise ValueError(
                f"No enabled callback found of type {callback_type} for agent "
                f"{agent_name}"
            )

        agent_specs: list[dict[str, Any]] = []
        for idx, cb in enumerate(enabled_callbacks):
            cb_name = (
                getattr(cb, "description", "")
                or getattr(cb, "name", "")
                or f"{norm_stage}_{idx + 1:02d}"
            )
            agent_specs.append(
                {
                    "name": str(cb_name),
                    "python_code": cb.python_code,
                    "is_global": False,
                }
            )

        combined_specs = global_specs + agent_specs
        if (
            len(combined_specs) == 1
            and not global_specs
            and effective_app_config is None
        ):
            code_content = enabled_callbacks[0].python_code
        else:
            code_content = _synthesize_chained_callback_module(
                norm_stage, combined_specs, app_config=effective_app_config
            )

        if not os.path.exists(test_file_path):
            raise FileNotFoundError(f"Test file not found: {test_file_path}")

        with open(test_file_path, encoding="utf-8") as f:
            test_content = f.read()

        results = self._run_test(
            code_content,
            test_content,
            test_file_path,
            agent_name,
            callback_type,
            log_file,
            pytest_args,
        )
        cols = [
            "agent_name",
            "callback_type",
            "test_name",
            "status",
            "error_message",
        ]
        if include_metrics:
            cols.insert(2, "callback_name")
            cols.append("metrics_summary")
        return pd.DataFrame(results, columns=cols)

    def test_all_callbacks_in_app_dir(
        self,
        app_dir: str,
        agent_name: str = "*",
        callback_type: str = "*_callbacks",
        callback_name: str = "*",
        log_file: str | None = None,
        pytest_args: list[str] | None = None,
        *,
        mode: Literal["per_callback", "combined", "both"] = "per_callback",
        include_chain: bool = True,
        chain_only: bool = False,
        record_fixtures: bool = False,
        fixtures_dir: str | Path | None = None,
        llm: str | None = None,
        include_metrics: bool = False,
    ) -> pd.DataFrame:
        """Runs pytest against callback unit tests and chain tests in `app_dir`.

        Discovers:
        1. Per-callback unit tests:
           `agents/<agent_name>/<type>_callbacks/<callback_name>/test.py`
           (unless `chain_only=True`).
        2. Multi-callback chain tests (unless `include_chain=False`):
           - `agents/<agent_name>/<type>_callbacks/test_chain.py`
           - `agents/<agent_name>/test_chain.py`
        """
        if mode not in ("per_callback", "combined", "both"):
            raise ValueError(
                f"Invalid mode '{mode}'. Expected 'per_callback', 'combined', "
                "or 'both'."
            )
        if chain_only and not include_chain:
            raise ValueError(
                "Cannot specify both chain_only=True and include_chain=False."
            )

        self.last_metrics_summaries.clear()
        self.last_turn_traces.clear()
        self.last_stage_traces.clear()
        self._warn_if_xdist_args(pytest_args)

        agent_name = agent_name or "*"
        callback_type = callback_type or "*_callbacks"
        callback_name = callback_name or "*"

        # 1. Discover per-callback test.py files
        test_files: list[str] = []
        if not chain_only:
            search_pattern = os.path.join(
                app_dir,
                "agents",
                agent_name,
                callback_type,
                callback_name,
                "test.py",
            )
            test_files = sorted(glob.glob(search_pattern, recursive=True))

        # 2. Discover multi-callback chain test files (test_chain.py)
        chain_test_files: list[str] = []
        if include_chain and callback_name in (
            "*",
            "test_chain",
            "test_chain.py",
        ):
            stage_chain_pattern = os.path.join(
                app_dir,
                "agents",
                agent_name,
                callback_type,
                "test_chain.py",
            )
            chain_test_files.extend(
                sorted(glob.glob(stage_chain_pattern, recursive=True))
            )
            if callback_type in ("*", "*_callbacks", "chain_callbacks"):
                agent_chain_pattern = os.path.join(
                    app_dir,
                    "agents",
                    agent_name,
                    "test_chain.py",
                )
                for fpath in sorted(
                    glob.glob(agent_chain_pattern, recursive=True)
                ):
                    if fpath not in chain_test_files:
                        chain_test_files.append(fpath)

        if not test_files and not chain_test_files:
            logger.warning(f"No callback tests found in {app_dir}")
            cols = [
                "agent_name",
                "callback_type",
                "test_name",
                "status",
                "error_message",
            ]
            if include_metrics:
                cols.insert(2, "callback_name")
                cols.append("metrics_summary")
            return pd.DataFrame(columns=cols)

        logger.info(
            f"Found {len(test_files)} callback tests and "
            f"{len(chain_test_files)} chain tests."
        )

        all_results: list[dict[str, Any]] = []

        for test_file in test_files:
            test_dir = os.path.dirname(os.path.abspath(test_file))
            python_code_path = os.path.join(test_dir, "python_code.py")

            if not os.path.exists(python_code_path):
                logger.warning(
                    f"Warning: {test_file} found, but no "
                    "python_code.py exists alongside it. Skipping."
                )
                continue

            logger.debug(f"Running test for: {python_code_path}")

            with open(python_code_path, encoding="utf-8") as f:
                code_content = f.read()

            with open(test_file, encoding="utf-8") as f:
                test_content = f.read()

            cur_agent_name = self._get_agent_name(test_file)
            cur_callback_type = self._get_callback_type(test_file)
            prev_llm_single = os.environ.get(CHAIN_LLM_ENV)
            if llm is not None:
                os.environ[CHAIN_LLM_ENV] = llm
            try:
                all_results.extend(
                    self._run_test(
                        code_content,
                        test_content,
                        test_file,
                        cur_agent_name,
                        cur_callback_type,
                        log_file,
                        pytest_args,
                    )
                )
            finally:
                if prev_llm_single is None:
                    os.environ.pop(CHAIN_LLM_ENV, None)
                else:
                    os.environ[CHAIN_LLM_ENV] = prev_llm_single

        chain_modes: list[ExecutionMode] = (
            ["per_callback", "combined"] if mode == "both" else [mode]
        )
        for chain_test_file in chain_test_files:
            with open(chain_test_file, encoding="utf-8") as f:
                test_content = f.read()

            cur_agent_name = self._get_agent_name(chain_test_file)
            cur_callback_type = self._get_callback_type(chain_test_file)
            abs_app_dir = os.path.abspath(app_dir)
            abs_agent_dir = os.path.join(abs_app_dir, "agents", cur_agent_name)

            for run_mode in chain_modes:
                eff_llm = llm or os.environ.get(CHAIN_LLM_ENV) or None
                chain_bootstrap = (
                    "from cxas_scrapi.evals.callback_evals import (\n"
                    "    CHAIN_LLM_ENV, CHAIN_MODE_ENV, CallbackChainRunner,\n"
                    "    CallbackTrace, ModeDiffReport, SessionSimulator,\n"
                    "    TurnTrace, AgentAssertionError, MockLlm,\n"
                    "    SnapshotManager, ToolSpy,\n"
                    "    expect_session, expect_stage, expect_turn,\n"
                    ")\n"
                    f"APP_DIR = {abs_app_dir!r}\n"
                    f"AGENT_DIR = {abs_agent_dir!r}\n"
                    f"CHAIN_MODE = {run_mode!r}\n"
                    f"CHAIN_LLM = {eff_llm!r}\n"
                    "runner = CallbackChainRunner.from_agent_dir(\n"
                    "    AGENT_DIR, app_dir=APP_DIR, mode=CHAIN_MODE\n"
                    ")\n"
                )
                prev_env_mode = os.environ.get(CHAIN_MODE_ENV)
                prev_record_env = os.environ.get("CXAS_RECORD_FIXTURES")
                prev_fix_dir_env = os.environ.get("CXAS_FIXTURES_DIR")
                prev_llm_env = os.environ.get(CHAIN_LLM_ENV)
                os.environ[CHAIN_MODE_ENV] = run_mode
                if llm is not None:
                    os.environ[CHAIN_LLM_ENV] = llm
                if record_fixtures and run_mode == chain_modes[0]:
                    os.environ["CXAS_RECORD_FIXTURES"] = "1"
                if fixtures_dir is not None:
                    os.environ["CXAS_FIXTURES_DIR"] = str(
                        Path(fixtures_dir).resolve()
                    )
                try:
                    mode_results = self._run_test(
                        chain_bootstrap,
                        test_content,
                        chain_test_file,
                        cur_agent_name,
                        cur_callback_type,
                        log_file,
                        pytest_args,
                    )
                finally:
                    if prev_env_mode is None:
                        os.environ.pop(CHAIN_MODE_ENV, None)
                    else:
                        os.environ[CHAIN_MODE_ENV] = prev_env_mode
                    if prev_record_env is None:
                        os.environ.pop("CXAS_RECORD_FIXTURES", None)
                    else:
                        os.environ["CXAS_RECORD_FIXTURES"] = prev_record_env
                    if prev_fix_dir_env is None:
                        os.environ.pop("CXAS_FIXTURES_DIR", None)
                    else:
                        os.environ["CXAS_FIXTURES_DIR"] = prev_fix_dir_env
                    if prev_llm_env is None:
                        os.environ.pop(CHAIN_LLM_ENV, None)
                    else:
                        os.environ[CHAIN_LLM_ENV] = prev_llm_env

                if mode == "both":
                    for row in mode_results:
                        old_name = row["test_name"]
                        new_name = f"{old_name}[{run_mode}]"
                        row["test_name"] = new_name
                        cb_sub = row.get("_raw_callback_name", "")
                        old_key = _TestResultCollector._format_metrics_key(
                            row["agent_name"],
                            row["callback_type"],
                            cb_sub,
                            old_name,
                        )
                        new_key = _TestResultCollector._format_metrics_key(
                            row["agent_name"],
                            row["callback_type"],
                            cb_sub,
                            new_name,
                        )
                        if old_key in self.last_metrics_summaries:
                            summary = self.last_metrics_summaries.pop(old_key)
                            summary.label = new_key
                            self.last_metrics_summaries[new_key] = summary
                        if old_key in self.last_turn_traces:
                            self.last_turn_traces[new_key] = (
                                self.last_turn_traces.pop(old_key)
                            )
                        if old_key in self.last_stage_traces:
                            self.last_stage_traces[new_key] = (
                                self.last_stage_traces.pop(old_key)
                            )
                all_results.extend(mode_results)

        for row in all_results:
            row.pop("_raw_callback_name", None)
        cols = [
            "agent_name",
            "callback_type",
            "test_name",
            "status",
            "error_message",
        ]
        if include_metrics:
            cols.insert(2, "callback_name")
            cols.append("metrics_summary")
        return pd.DataFrame(all_results, columns=cols)

    def lint_all_callbacks_in_app_dir(
        self,
        app_dir: str,
        agent_name: str = "*",
        callback_type: str = "*",
        *,
        image_version: ImageVersion = "v1",
        include_info: bool = False,
    ) -> pd.DataFrame:
        """Runs cross-callback static linter (C011-C019) across `app_dir`.

        Returns a DataFrame with columns:
        `["agent_name", "callback_type", "test_name", "status",
        "error_message"]`.
        `ERROR`-severity findings have `status="FAILED"`, while `WARNING` and
        `INFO` findings have `status="WARNING"` and `status="INFO"`
        respectively.
        """
        self.last_lint_findings.clear()
        agent_pattern = os.path.join(app_dir, "agents", agent_name or "*")
        agent_dirs = sorted(
            p for p in glob.glob(agent_pattern) if os.path.isdir(p)
        )
        stage_filter: str | None = None
        if callback_type and callback_type not in (
            "*",
            "*_callbacks",
            "chain_callbacks",
        ):
            stage_filter = normalize_stage(callback_type)

        rows: list[dict[str, Any]] = []
        for ag_dir in agent_dirs:
            ag_name = os.path.basename(ag_dir)
            try:
                runner = CallbackChainRunner.from_agent_dir(
                    ag_dir,
                    app_dir=app_dir,
                    image_version=image_version,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to load agent '%s' during lint: %s",
                    ag_name,
                    exc,
                )
                self.last_lint_findings.append(
                    CallbackChainLintFinding(
                        rule_id="C019",
                        severity="ERROR",
                        stage="chain",
                        callback_name=ag_name,
                        message=(
                            f"Failed to load callback chain for agent "
                            f"'{ag_name}': {type(exc).__name__}: {exc}"
                        ),
                    )
                )
                rows.append(
                    {
                        "agent_name": ag_name,
                        "callback_type": "chain_callbacks",
                        "test_name": "C019:load_error",
                        "status": "FAILED",
                        "error_message": (
                            "[C019] (ERROR) Failed to load callback chain for "
                            f"agent '{ag_name}': {type(exc).__name__}: {exc}"
                        ),
                    }
                )
                continue

            findings = runner.lint(
                stage=stage_filter,
                image_version=image_version,
                include_info=include_info,
            )
            self.last_lint_findings.extend(findings)
            for f in findings:
                status = "FAILED" if f.severity == "ERROR" else f.severity
                rows.append(
                    {
                        "agent_name": ag_name,
                        "callback_type": f"{f.stage}_callbacks",
                        "test_name": (
                            f"{f.rule_id}:{f.callback_name}:{f.line or 0}"
                        ),
                        "status": status,
                        "error_message": (
                            f"[{f.rule_id}] ({f.severity}) {f.message}"
                        ),
                    }
                )

        return pd.DataFrame(
            rows,
            columns=[
                "agent_name",
                "callback_type",
                "test_name",
                "status",
                "error_message",
            ],
        )

    def compare_all_callbacks_in_app_dir(
        self,
        app_dir: str,
        agent_name: str = "*",
        callback_type: str = "*",
        *,
        image_version: ImageVersion = "v1",
        fixtures_dir: str | Path | None = None,
    ) -> pd.DataFrame:
        """Runs `compare_modes` (`per_callback` vs `combined`) across `app_dir`.

        Returns a DataFrame with columns:
        `["agent_name", "callback_type", "test_name", "status",
        "error_message"]`.

        Status classification (`A2`):
        - `FAILED`: `report.has_diff` is `True` (mode divergence detected).
        - `ERROR`: One or both modes raised a runtime error (or agent failed to
          load).
        - `RISK`: No runtime diff on current input, but static `C012`
          shared-reference findings or runtime aliasing warnings exist on the
          stage.
        - `INCONCLUSIVE`: Synthetic empty input produced no state change,
          request/input mutation, or override in either mode (and no fixtures
          were provided).
        - `PASSED`: Both modes succeeded identically and exercised callback
          state/output changes (or passed user-supplied fixtures).
        """
        from cxas_scrapi.utils.callback_libs import (  # noqa: PLC0415
            CallbackContext,
            LlmRequest,
            LlmResponse,
            Part,
            Tool,
            ToolContext,
        )

        self.last_mode_diff_reports.clear()
        self.last_lint_findings.clear()
        agent_pattern = os.path.join(app_dir, "agents", agent_name or "*")
        agent_dirs = sorted(
            p for p in glob.glob(agent_pattern) if os.path.isdir(p)
        )
        stages_to_check: list[StageType] = list(STAGE_TO_FUNC_NAME.keys())
        if callback_type and callback_type not in (
            "*",
            "*_callbacks",
            "chain_callbacks",
        ):
            stages_to_check = [normalize_stage(callback_type)]

        eff_fixtures_root = fixtures_dir or os.environ.get("CXAS_FIXTURES_DIR")

        rows: list[dict[str, Any]] = []
        for ag_dir in agent_dirs:
            ag_name = os.path.basename(ag_dir)
            ag_path = Path(ag_dir)
            try:
                runner = CallbackChainRunner.from_agent_dir(
                    ag_dir,
                    app_dir=app_dir,
                    image_version=image_version,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to load agent '%s' during compare_modes: %s",
                    ag_name,
                    exc,
                )
                self.last_lint_findings.append(
                    CallbackChainLintFinding(
                        rule_id="C019",
                        severity="ERROR",
                        stage="chain",
                        callback_name=ag_name,
                        message=(
                            f"Failed to load callback chain for agent "
                            f"'{ag_name}': {type(exc).__name__}: {exc}"
                        ),
                    )
                )
                rows.append(
                    {
                        "agent_name": ag_name,
                        "callback_type": "chain_callbacks",
                        "test_name": "C019:load_error",
                        "status": "ERROR",
                        "error_message": (
                            f"[C019] Failed to load callback chain for agent "
                            f"'{ag_name}': {type(exc).__name__}: {exc}"
                        ),
                    }
                )
                continue

            for stage in stages_to_check:
                specs = runner.get_callbacks(stage)
                if not specs:
                    continue

                c012_findings = [
                    f
                    for f in runner.lint(stage, image_version=image_version)
                    if f.rule_id == "C012"
                ]
                for cf in c012_findings:
                    if cf not in self.last_lint_findings:
                        self.last_lint_findings.append(cf)

                fixture_files: list[Path] = []
                candidate_stage_dirs = [ag_path / "compare_fixtures" / stage]
                candidate_root_dirs = [ag_path / "compare_fixtures"]
                if eff_fixtures_root is not None:
                    ext_root = Path(eff_fixtures_root)
                    candidate_stage_dirs.extend(
                        [ext_root / ag_name / stage, ext_root / stage]
                    )
                    candidate_root_dirs.extend([ext_root / ag_name, ext_root])

                for sdir in candidate_stage_dirs:
                    if sdir.is_dir():
                        for fp in sorted(sdir.glob("*.json")):
                            if fp not in fixture_files:
                                fixture_files.append(fp)
                for rdir in candidate_root_dirs:
                    if rdir.is_dir():
                        for candidate in sorted(rdir.glob("*.json")):
                            if candidate in fixture_files:
                                continue
                            try:
                                cdata = json.loads(
                                    candidate.read_text(encoding="utf-8")
                                )
                                if (
                                    isinstance(cdata, dict)
                                    and cdata.get("stage")
                                    and normalize_stage(str(cdata["stage"]))
                                    == stage
                                ):
                                    fixture_files.append(candidate)
                            except Exception:
                                pass

                runs_for_stage: list[tuple[str, str, ModeDiffReport, bool]] = []
                if fixture_files:
                    for fpath in fixture_files:
                        rep = runner.compare_modes_from_fixture(
                            fpath,
                            stage=stage,
                            image_version=image_version,
                        )
                        runs_for_stage.append(
                            (
                                f"compare_modes:{stage}[{fpath.stem}]",
                                f"{ag_name}/{stage}#{fpath.stem}",
                                rep,
                                False,
                            )
                        )
                else:
                    if stage in ("before_tool", "after_tool"):
                        ctx: CallbackContext | ToolContext = ToolContext(
                            state={}
                        )
                    else:
                        ctx = CallbackContext(state={})
                    rep = runner.compare_modes(
                        ctx,
                        stage=stage,
                        llm_request=LlmRequest()
                        if stage == "before_model"
                        else None,
                        llm_response=LlmResponse.from_parts(
                            [Part.from_text("ok")]
                        )
                        if stage == "after_model"
                        else None,
                        tool=Tool(name="mock_tool", description="")
                        if stage in ("before_tool", "after_tool")
                        else None,
                        tool_input={}
                        if stage in ("before_tool", "after_tool")
                        else None,
                        tool_response={} if stage == "after_tool" else None,
                        image_version=image_version,
                    )
                    runs_for_stage.append(
                        (
                            f"compare_modes:{stage}",
                            f"{ag_name}/{stage}",
                            rep,
                            True,
                        )
                    )

                for (
                    test_label,
                    diff_key,
                    report,
                    is_synthetic,
                ) in runs_for_stage:
                    self.last_mode_diff_reports[diff_key] = report
                    per_tr = report.per_callback_trace
                    comb_tr = report.combined_trace
                    runtime_warnings = list(per_tr.warnings) + [
                        w for w in comb_tr.warnings if w not in per_tr.warnings
                    ]
                    risk_msgs = [
                        (
                            f"[{f.rule_id}] {f.callback_name}:{f.line or 0}: "
                            f"{f.message}"
                        )
                        for f in c012_findings
                    ] + runtime_warnings

                    if report.has_diff:
                        explanations = report.explain()
                        default_div_msg = (
                            "Mode divergence detected between 'per_callback' "
                            "and 'combined'."
                        )
                        rows.append(
                            {
                                "agent_name": ag_name,
                                "callback_type": f"{stage}_callbacks",
                                "test_name": test_label,
                                "status": "FAILED",
                                "error_message": (
                                    "; ".join(explanations)
                                    if explanations
                                    else default_div_msg
                                ),
                            }
                        )
                    elif per_tr.error is not None or comb_tr.error is not None:
                        if (
                            per_tr.error is not None
                            and comb_tr.error is not None
                        ):
                            mode_label = "both modes"
                            raw_err = per_tr.error
                        elif per_tr.error is not None:
                            mode_label = "'per_callback' mode"
                            raw_err = per_tr.error
                        else:
                            mode_label = "'combined' mode"
                            raw_err = comb_tr.error or "Runtime error"
                        first_line = raw_err.splitlines()[0]
                        rows.append(
                            {
                                "agent_name": ag_name,
                                "callback_type": f"{stage}_callbacks",
                                "test_name": test_label,
                                "status": "ERROR",
                                "error_message": (
                                    f"Callback error in {mode_label}: "
                                    f"{first_line}"
                                ),
                            }
                        )
                    elif risk_msgs:
                        rows.append(
                            {
                                "agent_name": ag_name,
                                "callback_type": f"{stage}_callbacks",
                                "test_name": test_label,
                                "status": "RISK",
                                "error_message": "; ".join(risk_msgs),
                            }
                        )
                    elif is_synthetic and (
                        per_tr.initial_state == per_tr.final_state
                        and comb_tr.initial_state == comb_tr.final_state
                        and per_tr.override_response is None
                        and comb_tr.override_response is None
                        and not per_tr.request_mutations
                        and not comb_tr.request_mutations
                        and (
                            stage != "before_tool"
                            or per_tr.final_tool_input == {}
                        )
                    ):
                        rows.append(
                            {
                                "agent_name": ag_name,
                                "callback_type": f"{stage}_callbacks",
                                "test_name": test_label,
                                "status": "INCONCLUSIVE",
                                "error_message": (
                                    "No state change or override observed on "
                                    "synthetic empty input; add fixtures under "
                                    f"agents/{ag_name}/"
                                    f"compare_fixtures/{stage}/*.json."
                                ),
                            }
                        )
                    else:
                        rows.append(
                            {
                                "agent_name": ag_name,
                                "callback_type": f"{stage}_callbacks",
                                "test_name": test_label,
                                "status": "PASSED",
                                "error_message": (
                                    "Synthetic input (exercised on default "
                                    "empty input without user fixtures)"
                                    if is_synthetic
                                    else None
                                ),
                            }
                        )

        return pd.DataFrame(
            rows,
            columns=[
                "agent_name",
                "callback_type",
                "test_name",
                "status",
                "error_message",
            ],
        )

    def _get_agent_name(self, original_file: str) -> str:
        """Extracts the agent name from the agent path."""
        parts = Path(original_file).parts
        if "agents" in parts:
            idx = len(parts) - 1 - list(reversed(parts)).index("agents")
            if idx + 1 < len(parts) - 1:
                return parts[idx + 1]
        norm = original_file.replace("\\", "/").split("/")
        if len(norm) >= 4:
            return norm[-4]
        if len(norm) >= 2:
            return norm[-2]
        return norm[0]

    def _get_callback_type(self, original_file: str) -> str:
        """Extracts the callback type from the agent path."""
        parts = Path(original_file).parts
        if "agents" in parts:
            idx = len(parts) - 1 - list(reversed(parts)).index("agents")
            if idx + 2 < len(parts) - 1:
                return parts[idx + 2]
            if idx + 2 == len(parts) - 1:
                return "chain_callbacks"
        norm = original_file.replace("\\", "/").split("/")
        if len(norm) >= 3:
            return norm[-3]
        return "chain_callbacks"

    def _get_callback_name(self, original_file: str) -> str:
        """Extracts the callback directory name from the agent path."""
        parts = Path(original_file).parts
        if "agents" in parts:
            idx = len(parts) - 1 - list(reversed(parts)).index("agents")
            if idx + 3 < len(parts) - 1:
                return parts[idx + 3]
        return ""

    def _run_test(
        self,
        callback_content: str,
        test_content: str,
        test_file_path: str,
        agent_name: str,
        callback_type: str,
        log_file: str | None = None,
        pytest_args: list[str] | None = None,
    ) -> list[dict]:
        """Runs the test for the given callback content and test content."""
        if log_file:
            log_file = os.path.abspath(log_file)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("--- Starting callback tests ---\n")

        with tempfile.TemporaryDirectory() as temp_dir:
            epoch = time.time()
            callback_path = os.path.join(temp_dir, "python_code.py")

            test_module_name = f"test_callback_{int(epoch * 1000)}"
            temp_test_path = os.path.join(temp_dir, f"{test_module_name}.py")

            with open(callback_path, "w", encoding="utf-8") as f:
                f.write(
                    "from cxas_scrapi.utils.callback_libs import *\n"
                    + "import json\n\n"
                    + callback_content
                )

            with open(temp_test_path, "w", encoding="utf-8") as f:
                f.write(test_content)

            from cxas_scrapi.evals.callback_chain.snapshots import (  # noqa: PLC0415
                _UNNAMED_SNAPSHOT_COUNTS,
            )

            original_sys_path = sys.path.copy()
            original_cwd = os.getcwd()
            abs_test_file_path = os.path.abspath(test_file_path)
            prev_orig_test_env = os.environ.get("CXAS_ORIGINAL_TEST_FILE")
            saved_unnamed_counts = dict(_UNNAMED_SNAPSHOT_COUNTS)
            try:
                sys.path.insert(0, temp_dir)
                os.chdir(temp_dir)
                os.environ["CXAS_ORIGINAL_TEST_FILE"] = abs_test_file_path

                if "python_code" in sys.modules:
                    del sys.modules["python_code"]

                collector = _TestResultCollector(
                    abs_test_file_path,
                    agent_name,
                    callback_type,
                    callback_name=self._get_callback_name(test_file_path),
                )
                args = [temp_test_path] + (pytest_args or [])
                plugin_list = [
                    collector,
                    "cxas_scrapi.evals.callback_chain.pytest_plugin",
                ]
                if log_file:
                    with open(log_file, "a", encoding="utf-8") as f:  # noqa: SIM117
                        with redirect_stdout(f), redirect_stderr(f):
                            pytest.main(args, plugins=plugin_list)
                else:
                    f = io.StringIO()
                    with redirect_stdout(f), redirect_stderr(f):
                        pytest.main(args, plugins=plugin_list)

                if hasattr(self, "last_metrics_summaries"):
                    self.last_metrics_summaries.update(
                        collector.metrics_summaries
                    )
                if hasattr(self, "last_turn_traces"):
                    self.last_turn_traces.update(collector.turn_traces)
                if hasattr(self, "last_stage_traces"):
                    self.last_stage_traces.update(collector.stage_traces)
                return collector.results
            finally:
                _UNNAMED_SNAPSHOT_COUNTS.clear()
                _UNNAMED_SNAPSHOT_COUNTS.update(saved_unnamed_counts)
                if prev_orig_test_env is None:
                    os.environ.pop("CXAS_ORIGINAL_TEST_FILE", None)
                else:
                    os.environ["CXAS_ORIGINAL_TEST_FILE"] = prev_orig_test_env
                sys.path = original_sys_path
                os.chdir(original_cwd)


class _TestResultCollector:
    """Collects execution results from pytest test runs."""

    def __init__(
        self,
        original_file: typing.Any,
        agent_name: typing.Any,
        callback_type: typing.Any,
        callback_name: str = "",
    ) -> None:
        self.results = []
        self.metrics_summaries: dict[str, SessionMetricsSummary] = {}
        self.turn_traces: dict[str, list[TurnTrace]] = {}
        self.stage_traces: dict[str, list[CallbackTrace]] = {}
        self.original_file = original_file
        self.agent_name = agent_name
        self.callback_type = callback_type
        self.callback_name = callback_name
        self._active_turns: list[TurnTrace] = []
        self._active_stages: list[CallbackTrace] = []
        self._collector_token = None
        self._stage_collector_token = None

    @staticmethod
    def _format_metrics_key(
        agent_name: str,
        callback_type: str,
        callback_name: str,
        test_name: str,
    ) -> str:
        if callback_name:
            return f"{agent_name}/{callback_type}/{callback_name}/{test_name}"
        return f"{agent_name}/{callback_type}/{test_name}"

    @staticmethod
    def _collect_turn_stage_ids(turns: Sequence[TurnTrace]) -> set[int]:
        ids: set[int] = set()
        stack = list(turns)
        while stack:
            t = stack.pop()
            if t.before_agent_trace is not None:
                ids.add(id(t.before_agent_trace))
            if t.before_model_trace is not None:
                ids.add(id(t.before_model_trace))
            if t.after_model_trace is not None:
                ids.add(id(t.after_model_trace))
            if t.after_agent_trace is not None:
                ids.add(id(t.after_agent_trace))
            for ls in t.llm_steps:
                if ls.before_model_trace is not None:
                    ids.add(id(ls.before_model_trace))
                if ls.after_model_trace is not None:
                    ids.add(id(ls.after_model_trace))
            for ts in t.tool_steps:
                if ts.before_tool_trace is not None:
                    ids.add(id(ts.before_tool_trace))
                if ts.after_tool_trace is not None:
                    ids.add(id(ts.after_tool_trace))
            if t.agent_hops:
                stack.extend(t.agent_hops)
        return ids

    def _get_error_message(self, report: typing.Any) -> typing.Any:
        if getattr(report, "longrepr", None):
            if hasattr(report.longrepr, "reprcrash") and getattr(
                report.longrepr, "reprcrash", None
            ):
                return report.longrepr.reprcrash.message
            return str(report.longrepr)
        return None

    @pytest.hookimpl(trylast=True)
    def pytest_runtest_setup(self, item: typing.Any) -> None:
        from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
            runner as _runner_mod,
        )
        from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
            turn as _turn_mod,
        )

        cxas_col = getattr(item, "_cxas_collector", None)
        if cxas_col is not None and hasattr(cxas_col, "turn_traces"):
            self._active_turns = cxas_col.turn_traces
            self._active_stages = cxas_col.stage_traces
            self._collector_token = None
            self._stage_collector_token = None
            return

        self._active_turns = []
        self._active_stages = []
        self._collector_token = _turn_mod._push_active_turn_collector(
            self._active_turns
        )
        self._stage_collector_token = _runner_mod._push_active_stage_collector(
            self._active_stages
        )

    def pytest_runtest_teardown(
        self, item: typing.Any, nextitem: typing.Any
    ) -> None:
        from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
            runner as _runner_mod,
        )
        from cxas_scrapi.evals.callback_chain import (  # noqa: PLC0415
            turn as _turn_mod,
        )

        del item, nextitem
        if self._collector_token is not None:
            _turn_mod._pop_active_turn_collector(
                self._collector_token, self._active_turns
            )
            self._collector_token = None
        if self._stage_collector_token is not None:
            _runner_mod._pop_active_stage_collector(
                self._stage_collector_token, self._active_stages
            )
            self._stage_collector_token = None

    def _record_collected_traces(self, key: str) -> None:
        if self._active_turns:
            self.turn_traces[key] = list(self._active_turns)
        if self._active_stages:
            embedded_ids = self._collect_turn_stage_ids(self._active_turns)
            standalone = [
                st for st in self._active_stages if id(st) not in embedded_ids
            ]
            if standalone:
                self.stage_traces[key] = standalone

    def pytest_runtest_logreport(self, report: typing.Any) -> None:
        if report.when == "call" or report.failed:
            test_name = report.nodeid.split("::")[-1]
            key = self._format_metrics_key(
                self.agent_name,
                self.callback_type,
                self.callback_name,
                test_name,
            )
            summary = SessionMetricsSummary.from_turns(
                list(self._active_turns), label=key
            )
            self.metrics_summaries[key] = summary
            self._record_collected_traces(key)
            self.results.append(
                {
                    "agent_name": self.agent_name,
                    "callback_type": self.callback_type,
                    "callback_name": self.callback_name or self.callback_type,
                    "_raw_callback_name": self.callback_name,
                    "test_name": test_name,
                    "status": report.outcome.upper(),
                    "error_message": self._get_error_message(report),
                    "metrics_summary": summary,
                }
            )

    def pytest_collectreport(self, report: typing.Any) -> None:
        if report.failed:
            key = self._format_metrics_key(
                self.agent_name,
                self.callback_type,
                self.callback_name,
                "collection_error",
            )
            summary = SessionMetricsSummary.from_turns([], label=key)
            self.metrics_summaries[key] = summary
            self.results.append(
                {
                    "agent_name": self.agent_name,
                    "callback_type": self.callback_type,
                    "callback_name": self.callback_name or self.callback_type,
                    "_raw_callback_name": self.callback_name,
                    "test_name": "collection_error",
                    "status": "FAILED",
                    "error_message": self._get_error_message(report),
                    "metrics_summary": summary,
                }
            )
