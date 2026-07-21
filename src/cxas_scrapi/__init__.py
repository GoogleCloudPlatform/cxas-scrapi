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

import ast as _ast
import importlib as _importlib
import inspect as _inspect
import pkgutil as _pkgutil
import sys as _sys
import threading as _threading
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Any as _Any

_SYMBOL_CACHE: dict[str, tuple[str, str | None]] | None = None
_LOCK = _threading.Lock()


def _discover_exports() -> dict[str, tuple[str, str | None]]:
    global _SYMBOL_CACHE  # noqa: PLW0603
    if _SYMBOL_CACHE is not None:
        return _SYMBOL_CACHE

    with _LOCK:
        if _SYMBOL_CACHE is not None:
            return _SYMBOL_CACHE

        source: str | None = None
        try:
            source_bytes = _pkgutil.get_data(__package__, "__init__.py")
            if source_bytes:
                source = source_bytes.decode("utf-8")
        except Exception:
            pass

        if not source:
            try:
                mod = _sys.modules.get(__name__)
                if mod and hasattr(mod, "__file__") and mod.__file__:
                    with open(mod.__file__, encoding="utf-8") as f:
                        source = f.read()
            except Exception:
                pass

        if not source:
            try:
                mod = _sys.modules.get(__name__)
                if mod:
                    source = _inspect.getsource(mod)
            except Exception:
                pass

        if not source:
            raise ImportError("Unable to read package source for AST discovery")

        cache: dict[str, tuple[str, str | None]] = {}
        try:
            tree = _ast.parse(source)
            for node in _ast.walk(tree):
                if isinstance(node, _ast.If):
                    is_tc = False
                    if isinstance(node.test, _ast.Name) and node.test.id in (
                        "TYPE_CHECKING",
                        "_TYPE_CHECKING",
                    ):
                        is_tc = True
                    elif isinstance(
                        node.test, _ast.Attribute
                    ) and node.test.attr in (
                        "TYPE_CHECKING",
                        "_TYPE_CHECKING",
                    ):
                        is_tc = True

                    if is_tc:
                        for stmt in node.body:
                            for sub_node in _ast.walk(stmt):
                                if isinstance(sub_node, _ast.Import):
                                    for alias in sub_node.names:
                                        export_name = (
                                            alias.asname
                                            or alias.name.split(".")[-1]
                                        )
                                        cache[export_name] = (alias.name, None)
                                elif isinstance(sub_node, _ast.ImportFrom):
                                    if sub_node.level == 0:
                                        base_mod = sub_node.module or ""
                                    elif sub_node.module:
                                        submod = sub_node.module.lstrip(".")
                                        base_mod = f"{__name__}.{submod}"
                                    else:
                                        base_mod = __name__

                                    for alias in sub_node.names:
                                        export_name = alias.asname or alias.name
                                        if base_mod in (
                                            "cxas_scrapi",
                                            __name__,
                                        ):
                                            cache[export_name] = (
                                                f"{__name__}.{alias.name}",
                                                None,
                                            )
                                        else:
                                            cache[export_name] = (
                                                base_mod,
                                                alias.name,
                                            )
        except Exception as err:
            raise ImportError(
                f"Failed to parse AST in __init__.py: {err}"
            ) from err

        _SYMBOL_CACHE = cache
        return cache


if _TYPE_CHECKING:
    from cxas_scrapi.core.agents import Agents
    from cxas_scrapi.core.apps import Apps
    from cxas_scrapi.core.callbacks import Callbacks
    from cxas_scrapi.core.changelogs import Changelogs
    from cxas_scrapi.core.common import Common
    from cxas_scrapi.core.conversation_history import ConversationHistory
    from cxas_scrapi.core.deployments import Deployments
    from cxas_scrapi.core.evaluations import Evaluations
    from cxas_scrapi.core.guardrails import Guardrails
    from cxas_scrapi.core.sessions import Sessions
    from cxas_scrapi.core.tools import Tools
    from cxas_scrapi.core.variables import Variables
    from cxas_scrapi.core.versions import Versions
    from cxas_scrapi.evals.callback_evals import CallbackEvals
    from cxas_scrapi.evals.guardrail_evals import GuardrailEvals
    from cxas_scrapi.evals.simulation_evals import SimulationEvals
    from cxas_scrapi.evals.tool_evals import ToolEvals
    from cxas_scrapi.evals.turn_evals import TurnEvals
    from cxas_scrapi.migration.dfcx_exporter import (
        BaseDFCXClient,
        ConversationalAgentsAPI,
        DFCXAgentExporter,
        DFCXAgents,
        DFCXGenerativeSettings,
        DFCXPlaybooks,
        DFCXTools,
    )
    from cxas_scrapi.migration.flow_visualizer import (
        FlowDependencyResolver,
        FlowTreeVisualizer,
    )
    from cxas_scrapi.migration.graph_visualizer import HighLevelGraphVisualizer
    from cxas_scrapi.migration.main_visualizer import MainVisualizer
    from cxas_scrapi.migration.playbook_visualizer import PlaybookTreeVisualizer
    from cxas_scrapi.utils.changelog_utils import ChangelogUtils
    from cxas_scrapi.utils.eval_utils import EvalUtils
    from cxas_scrapi.utils.google_sheets_utils import GoogleSheetsUtils
    from cxas_scrapi.utils.secret_manager_utils import SecretManagerUtils


def __getattr__(name: str) -> _Any:
    exports = _discover_exports()
    if name in exports:
        mod_path, target_attr = exports[name]
        if target_attr is None:
            val = _importlib.import_module(mod_path)
        else:
            mod = _importlib.import_module(mod_path)
            try:
                val = getattr(mod, target_attr)
            except AttributeError as err:
                if hasattr(mod, "__path__"):
                    submod_path = f"{mod_path}.{target_attr}"
                    try:
                        val = _importlib.import_module(submod_path)
                    except ModuleNotFoundError as mnf:
                        if mnf.name == submod_path:
                            raise err from None
                        raise
                else:
                    raise
        globals()[name] = val
        return val
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    "Agents",
    "Apps",
    "BaseDFCXClient",
    "CallbackEvals",
    "Callbacks",
    "ChangelogUtils",
    "Changelogs",
    "Common",
    "ConversationHistory",
    "ConversationalAgentsAPI",
    "DFCXAgentExporter",
    "DFCXAgents",
    "DFCXGenerativeSettings",
    "DFCXPlaybooks",
    "DFCXTools",
    "Deployments",
    "EvalUtils",
    "Evaluations",
    "FlowDependencyResolver",
    "FlowTreeVisualizer",
    "GoogleSheetsUtils",
    "GuardrailEvals",
    "Guardrails",
    "HighLevelGraphVisualizer",
    "MainVisualizer",
    "PlaybookTreeVisualizer",
    "SecretManagerUtils",
    "Sessions",
    "SimulationEvals",
    "ToolEvals",
    "Tools",
    "TurnEvals",
    "Variables",
    "Versions",
]


def __dir__() -> list[str]:
    keys = list(globals().keys())
    return sorted(
        set(__all__)
        | {
            k
            for k in keys
            if not k.startswith("_")
            or (k.startswith("__") and k.endswith("__"))
        }
    )
