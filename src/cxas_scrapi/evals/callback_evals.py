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


"""Utility for running tests for CES agent callbacks."""

import glob
import io
import json
import logging
import os
import sys
import tempfile
import time
import typing
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd
import pytest

from cxas_scrapi.core.agents import Agents

logger = logging.getLogger(__name__)

_CALLBACK_TYPE_ALIASES: dict[str, tuple[str, str]] = {
    "before_model": ("before_model_callbacks", "beforeModelCallbacks"),
    "before_model_callback": ("before_model_callbacks", "beforeModelCallbacks"),
    "before_model_callbacks": (
        "before_model_callbacks",
        "beforeModelCallbacks",
    ),
    "after_model": ("after_model_callbacks", "afterModelCallbacks"),
    "after_model_callback": ("after_model_callbacks", "afterModelCallbacks"),
    "after_model_callbacks": ("after_model_callbacks", "afterModelCallbacks"),
    "before_tool": ("before_tool_callbacks", "beforeToolCallbacks"),
    "before_tool_callback": ("before_tool_callbacks", "beforeToolCallbacks"),
    "before_tool_callbacks": ("before_tool_callbacks", "beforeToolCallbacks"),
    "after_tool": ("after_tool_callbacks", "afterToolCallbacks"),
    "after_tool_callback": ("after_tool_callbacks", "afterToolCallbacks"),
    "after_tool_callbacks": ("after_tool_callbacks", "afterToolCallbacks"),
    "before_agent": ("before_agent_callbacks", "beforeAgentCallbacks"),
    "before_agent_callback": ("before_agent_callbacks", "beforeAgentCallbacks"),
    "before_agent_callbacks": (
        "before_agent_callbacks",
        "beforeAgentCallbacks",
    ),
    "after_agent": ("after_agent_callbacks", "afterAgentCallbacks"),
    "after_agent_callback": ("after_agent_callbacks", "afterAgentCallbacks"),
    "after_agent_callbacks": ("after_agent_callbacks", "afterAgentCallbacks"),
}


class CallbackEvals:
    """Provides methods for orchestrating and executing agent callback tests."""

    @staticmethod
    def _normalize_callback_type(callback_type: str) -> tuple[str, str]:
        """Normalizes a callback type string to (plural_snake, camel_case)."""
        cleaned = (callback_type or "").strip().lower()
        if cleaned in _CALLBACK_TYPE_ALIASES:
            return _CALLBACK_TYPE_ALIASES[cleaned]
        if cleaned.endswith("_callback"):
            plural = f"{cleaned}s"
        elif cleaned.endswith("_callbacks"):
            plural = cleaned
        else:
            plural = f"{cleaned}_callbacks"
        parts = plural.split("_")
        camel = parts[0] + "".join(p.capitalize() for p in parts[1:])
        return plural, camel

    def _resolve_local_agent_dir(
        self, app_dir: str, agent_name: str
    ) -> tuple[Path, dict[str, typing.Any]]:
        """Locates the agent directory and parsed JSON under
        <app_dir>/agents/.
        """
        app_path = Path(app_dir)
        agents_dir = app_path / "agents"
        if not agents_dir.exists() or not agents_dir.is_dir():
            raise FileNotFoundError(
                f"Agents directory not found in app_dir: {agents_dir}"
            )

        direct_dir = agents_dir / agent_name
        if direct_dir.exists() and direct_dir.is_dir():
            agent_json = self._load_agent_json(direct_dir)
            return direct_dir, agent_json

        target_lower = agent_name.strip().lower()
        for candidate in sorted(agents_dir.iterdir()):
            if not candidate.is_dir():
                continue
            agent_json = self._load_agent_json(candidate)
            display_name = str(
                agent_json.get("displayName")
                or agent_json.get("display_name")
                or ""
            ).strip()
            if (
                candidate.name.lower() == target_lower
                or display_name.lower() == target_lower
            ):
                return candidate, agent_json

        raise ValueError(f"Agent '{agent_name}' not found under {agents_dir}.")

    @staticmethod
    def _load_agent_json(agent_dir: Path) -> dict[str, typing.Any]:
        """Loads <agent>.json from an agent directory if present."""
        preferred = agent_dir / f"{agent_dir.name}.json"
        json_files = (
            [preferred]
            if preferred.exists()
            else sorted(agent_dir.glob("*.json"))
        )
        for jf in json_files:
            try:
                with open(jf, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (OSError, json.JSONDecodeError):
                continue
        return {}

    def _resolve_local_callback_code(
        self,
        app_dir: str,
        agent_name: str,
        callback_type: str,
        test_file_path: str | None = None,
    ) -> str:
        """Resolves callback python_code from a local app_dir."""
        app_path = Path(app_dir)
        agent_dir, agent_json = self._resolve_local_agent_dir(
            app_dir, agent_name
        )
        plural_snake, camel_case = self._normalize_callback_type(callback_type)
        singular_snake = (
            plural_snake[:-1] if plural_snake.endswith("s") else plural_snake
        )

        code_paths: list[Path] = []
        seen_paths: set[Path] = set()

        def _add_path(p: Path) -> None:
            resolved = p.resolve()
            if p.exists() and p.is_file() and resolved not in seen_paths:
                seen_paths.add(resolved)
                code_paths.append(p)

        # 1. Check <agent>.json callback references first
        raw_refs = (
            agent_json.get(camel_case)
            or agent_json.get(plural_snake)
            or agent_json.get(singular_snake)
            or []
        )
        inline_codes: list[str] = []
        if isinstance(raw_refs, list):
            for entry in raw_refs:
                if not isinstance(entry, dict):
                    continue
                ref = entry.get("pythonCode") or entry.get("python_code")
                if not isinstance(ref, str) or not ref.strip():
                    continue
                # Relative path from app_dir or agent_dir, or inline code
                cand_app = app_path / ref
                cand_agent = agent_dir / ref
                if cand_app.exists() and cand_app.is_file():
                    _add_path(cand_app)
                elif cand_agent.exists() and cand_agent.is_file():
                    _add_path(cand_agent)
                elif "\n" in ref or ref.lstrip().startswith("def "):
                    inline_codes.append(ref)

        # 2. Check standard nested layout:
        #    <agent_dir>/<plural_snake>/<name>/python_code.py
        #    and direct <agent_dir>/<plural_snake>/python_code.py
        for dir_name in (plural_snake, singular_snake, callback_type):
            cb_type_dir = agent_dir / dir_name
            if cb_type_dir.exists() and cb_type_dir.is_dir():
                _add_path(cb_type_dir / "python_code.py")
                for child in sorted(cb_type_dir.iterdir()):
                    if child.is_dir():
                        _add_path(child / "python_code.py")

        # 3. Check flat layout: <agent_dir>/<plural_snake>_*/python_code.py
        #    or <agent_dir>/<singular_snake>_*/python_code.py
        for child in sorted(agent_dir.iterdir()):
            if not child.is_dir():
                continue
            if (
                child.name.startswith(f"{plural_snake}_")
                or child.name.startswith(f"{singular_snake}_")
                or child.name == callback_type
            ):
                _add_path(child / "python_code.py")

        # Disambiguate when multiple callbacks exist and test_file_path
        # points inside or matches one of the candidate callback directories
        if len(code_paths) > 1 and test_file_path:
            resolved_test = Path(test_file_path).resolve()
            colocated = [
                cp
                for cp in code_paths
                if resolved_test.parent == cp.resolve().parent
                or resolved_test.is_relative_to(cp.resolve().parent)
            ]
            if len(colocated) == 1:
                code_paths = colocated
                inline_codes = []
            else:
                test_stem = resolved_test.stem.lower()
                by_name = [
                    cp
                    for cp in code_paths
                    if cp.parent.name.lower() in test_stem
                    or test_stem in cp.parent.name.lower()
                ]
                if len(by_name) == 1:
                    code_paths = by_name
                    inline_codes = []

        total_found = len(code_paths) + len(inline_codes)
        if total_found == 0:
            raise ValueError(
                f"No callback found of type {callback_type} for agent "
                f"{agent_name} in {app_dir}"
            )
        if total_found > 1:
            raise ValueError(
                f"Multiple callbacks found of type {callback_type} for "
                f"agent {agent_name}"
            )

        if code_paths:
            with open(code_paths[0], encoding="utf-8") as f:
                return f.read()
        return inline_codes[0]

    def test_single_callback_offline(
        self,
        app_dir: str,
        agent_name: str,
        callback_type: str,
        test_file_path: str,
        log_file: str | None = None,
        pytest_args: list[str] | None = None,
    ) -> pd.DataFrame:
        """Runs pytest against a single callback resolved from a local
        app_dir.
        """
        code_content = self._resolve_local_callback_code(
            app_dir=app_dir,
            agent_name=agent_name,
            callback_type=callback_type,
            test_file_path=test_file_path,
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
        return pd.DataFrame(
            results,
            columns=[
                "agent_name",
                "callback_type",
                "test_name",
                "status",
                "error_message",
            ],
        )

    def test_single_callback_for_agent(
        self,
        app_name: str | None = None,
        agent_name: str = "",
        callback_type: str = "",
        test_file_path: str = "",
        log_file: str | None = None,
        pytest_args: list[str] | None = None,
        app_dir: str | None = None,
    ) -> pd.DataFrame:
        """Runs test against a single callback fetched from the agent proto
        or local app_dir.

        Args:
            app_name: Optional CXAS App name (required if app_dir is not set).
            agent_name: The name or display name of the agent.
            callback_type: The type of callback (e.g., 'before_model',
                'after_tool').
            test_file_path: Path to the test.py file to run.
            log_file: Optional. Path to a file to log pytest output to.
            pytest_args: Optional. Additional arguments to pass to pytest.
            app_dir: Optional local app directory for offline execution.
        """
        if app_dir:
            return self.test_single_callback_offline(
                app_dir=app_dir,
                agent_name=agent_name,
                callback_type=callback_type,
                test_file_path=test_file_path,
                log_file=log_file,
                pytest_args=pytest_args,
            )

        if not app_name:
            raise ValueError("Either 'app_name' or 'app_dir' must be provided.")

        agents_client = Agents(app_name=app_name)

        # Get agent ID
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

        # Fetch callback
        plural_snake, _ = self._normalize_callback_type(callback_type)
        all_callbacks = {
            "before_model_callback": agent.before_model_callbacks,
            "after_model_callback": agent.after_model_callbacks,
            "before_tool_callback": agent.before_tool_callbacks,
            "after_tool_callback": agent.after_tool_callbacks,
            "before_agent_callbacks": agent.before_agent_callbacks,
            "after_agent_callbacks": agent.after_agent_callbacks,
            "before_model_callbacks": agent.before_model_callbacks,
            "after_model_callbacks": agent.after_model_callbacks,
            "before_tool_callbacks": agent.before_tool_callbacks,
            "after_tool_callbacks": agent.after_tool_callbacks,
            "before_agent_callback": agent.before_agent_callbacks,
            "after_agent_callback": agent.after_agent_callbacks,
        }
        callback = all_callbacks.get(callback_type) or all_callbacks.get(
            plural_snake
        )
        if not callback:
            raise ValueError(
                f"No callback found of type {callback_type} for agent "
                f"{agent_name}"
            )
        if len(callback) > 1:
            raise ValueError(
                f"Multiple callbacks found of type {callback_type} for "
                f"agent {agent_name}"
            )

        code_content = callback[0].python_code

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
            return pd.DataFrame(
                results,
                columns=[
                    "agent_name",
                    "callback_type",
                    "test_name",
                    "status",
                    "error_message",
                ],
            )

    def test_all_callbacks_in_app_dir(
        self,
        app_dir: str,
        agent_name: str = "*",
        callback_type: str = "*_callbacks",
        callback_name: str = "*",
        log_file: str | None = None,
        pytest_args: list[str] | None = None,
    ) -> pd.DataFrame:
        """Runs pytest against all callback tests in the given agent directory.

        Args:
            app_dir: The path to the CES app root directory.
            agent_name: Optional. The name of the agent to run tests for.
                If not provided, all agents will be tested.
            callback_type: Optional. The type of callback to run tests for.
                If not provided, all callback types will be tested.
            callback_name: Optional. The name of the callback to run tests for.
                If not provided, all callbacks will be tested.
            log_file: Optional. Path to a file to log pytest output to.
                If not provided, output will be logged to the console.
            pytest_args: Optional. Additional arguments to pass to pytest.
                Defaults to None.

        Returns:
            A pandas DataFrame containing test execution results.
        """

        agent_name = agent_name or "*"
        callback_type = callback_type or "*_callbacks"
        callback_name = callback_name or "*"

        # Discover all test.py files within the agent directory
        # Expected: agents/<agent_name>/<type>_callbacks/<callback_name>/test.py
        search_pattern = os.path.join(
            app_dir,
            "agents",
            agent_name,
            callback_type,
            callback_name,
            "test.py",
        )
        test_files = glob.glob(search_pattern, recursive=True)

        if not test_files:
            logger.warning(f"No callback tests found in {app_dir}")
            return pd.DataFrame(
                columns=[
                    "agent_name",
                    "callback_type",
                    "test_name",
                    "status",
                    "error_message",
                ]
            )

        logger.info(f"Found {len(test_files)} callback tests.")

        all_results = []

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

        return pd.DataFrame(
            all_results,
            columns=[
                "agent_name",
                "callback_type",
                "test_name",
                "status",
                "error_message",
            ],
        )

    def run_python_unit_tests_in_dir(
        self,
        test_dir: str,
        filter_files: list[str] | None = None,
        exclude_files: list[str] | None = None,
        pytest_args: list[str] | None = None,
    ) -> pd.DataFrame:
        """Discovers and runs standalone `test_*.py` unit tests."""
        columns = [
            "agent_name",
            "agent",
            "callback_type",
            "test_name",
            "status",
            "passed",
            "duration_s",
            "error_message",
            "error",
            "code",
            "file",
        ]
        if not test_dir or not os.path.exists(test_dir):
            return pd.DataFrame(columns=columns)

        if os.path.isfile(test_dir):
            py_files = [test_dir] if test_dir.endswith(".py") else []
        else:
            py_files = sorted(glob.glob(os.path.join(test_dir, "test_*.py")))

        include_pats = [
            p.strip().lower() for p in (filter_files or []) if p and p.strip()
        ]
        exclude_pats = [
            p.strip().lower() for p in (exclude_files or []) if p and p.strip()
        ]
        if include_pats:
            py_files = [
                f
                for f in py_files
                if any(
                    pat in os.path.basename(f).lower() for pat in include_pats
                )
            ]
        if exclude_pats:
            py_files = [
                f
                for f in py_files
                if not any(
                    pat in os.path.basename(f).lower() for pat in exclude_pats
                )
            ]

        if not py_files:
            return pd.DataFrame(columns=columns)

        all_results: list[dict[str, typing.Any]] = []
        for py_file in py_files:
            abs_file = os.path.abspath(py_file)
            file_dir = os.path.dirname(abs_file)
            suite_label = os.path.splitext(os.path.basename(abs_file))[0]
            try:
                with open(abs_file, encoding="utf-8") as f:
                    code_content = f.read()
            except OSError:
                code_content = ""

            collector = _TestResultCollector(
                abs_file,
                suite_label,
                "tool_unit_test",
                code=code_content,
            )
            original_sys_path = sys.path.copy()
            try:
                if file_dir not in sys.path:
                    sys.path.insert(0, file_dir)
                buf = io.StringIO()
                with redirect_stdout(buf), redirect_stderr(buf):
                    pytest.main(
                        [abs_file, "-q"] + (pytest_args or []),
                        plugins=[collector],
                    )
                for row in collector.results:
                    row_copy = dict(row)
                    row_copy.setdefault("agent", suite_label)
                    row_copy.setdefault(
                        "passed", row_copy.get("status") == "PASSED"
                    )
                    row_copy.setdefault("duration_s", 0.0)
                    row_copy.setdefault(
                        "error", row_copy.get("error_message") or ""
                    )
                    row_copy.setdefault("code", code_content)
                    row_copy.setdefault("file", py_file)
                    all_results.append(row_copy)
            finally:
                sys.path = original_sys_path

        return pd.DataFrame(all_results, columns=columns)

    def _get_agent_name(self, original_file: str) -> str:
        """Extracts the agent name from the agent path."""
        return original_file.split("/")[-4]

    def _get_callback_type(self, original_file: str) -> str:
        """Extracts the callback type from the agent path."""
        return original_file.split("/")[-3]

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

            # Unique module names prevent pytest from caching imported files
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

            original_sys_path = sys.path.copy()
            original_cwd = os.getcwd()
            try:
                sys.path.insert(0, temp_dir)
                os.chdir(temp_dir)

                # Clear python_code from sys.modules to load the new code
                if "python_code" in sys.modules:
                    del sys.modules["python_code"]

                collector = _TestResultCollector(
                    test_file_path, agent_name, callback_type
                )
                args = [temp_test_path] + (pytest_args or [])
                if log_file:
                    with open(log_file, "a", encoding="utf-8") as f:  # noqa: SIM117
                        with redirect_stdout(f), redirect_stderr(f):
                            pytest.main(args, plugins=[collector])
                else:
                    f = io.StringIO()
                    with redirect_stdout(f), redirect_stderr(f):
                        pytest.main(args, plugins=[collector])

                return collector.results
            finally:
                sys.path = original_sys_path
                os.chdir(original_cwd)


class _TestResultCollector:
    """Collects execution results from pytest test runs."""

    def __init__(
        self,
        original_file: typing.Any,
        agent_name: typing.Any,
        callback_type: typing.Any,
        code: str = "",
    ) -> None:
        self.results: list[dict[str, typing.Any]] = []
        self.original_file = original_file
        self.agent_name = agent_name
        self.callback_type = callback_type
        self.code = code

    def _get_error_message(self, report: typing.Any) -> typing.Any:
        if getattr(report, "longrepr", None):
            if hasattr(report.longrepr, "reprcrash") and getattr(
                report.longrepr, "reprcrash", None
            ):
                return report.longrepr.reprcrash.message
            return str(report.longrepr)
        return None

    def pytest_runtest_logreport(self, report: typing.Any) -> None:
        if report.when == "call" or (report.when == "setup" and report.failed):
            err_msg = self._get_error_message(report)
            self.results.append(
                {
                    "agent_name": self.agent_name,
                    "agent": self.agent_name,
                    "callback_type": self.callback_type,
                    "test_name": report.nodeid.split("::")[-1],
                    "status": report.outcome.upper(),
                    "passed": report.outcome == "passed",
                    "duration_s": round(
                        float(getattr(report, "duration", 0.0) or 0.0), 4
                    ),
                    "error_message": err_msg,
                    "error": err_msg or "",
                    "code": self.code,
                    "file": self.original_file,
                }
            )

    def pytest_collectreport(self, report: typing.Any) -> None:
        if report.failed:
            self.results.append(
                {
                    "agent_name": self.agent_name,
                    "callback_type": self.callback_type,
                    "test_name": "collection_error",
                    "status": "FAILED",
                    "error_message": self._get_error_message(report),
                }
            )
