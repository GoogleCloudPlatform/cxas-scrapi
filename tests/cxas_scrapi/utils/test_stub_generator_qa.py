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

"""Adversarial QA and regression tests for `cxas generate-stubs` (PR 1)."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cxas_scrapi.cli.app import app_generate_stubs
from cxas_scrapi.cli.main import get_parser
from cxas_scrapi.utils.stub_generator import generate_stubs

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture


def _find_ruff_bin() -> str | None:
    """Locate the ruff binary in the active Python env, repo venv, or PATH."""
    exe_ruff = Path(sys.executable).resolve().with_name("ruff")
    if exe_ruff.is_file():
        return str(exe_ruff)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".venv" / "bin" / "ruff"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ruff")


# ---------------------------------------------------------------------------
# Defect Reproduction Tests (QA-001 .. QA-004) — fails-as-expected
# ---------------------------------------------------------------------------


def test_qa_001_docstrings_with_quotes_backslashes_and_newlines_valid_syntax(
    tmp_path: Path,
) -> None:
    """QA-001: Tool/toolset descriptions with triple quotes, trailing quotes,
    backslashes, or newlines in toolsets must produce syntactically valid Python.
    """
    (tmp_path / "app.json").write_text(
        json.dumps({"name": "qa-001-app"}), encoding="utf-8"
    )

    # 1. Tool description containing """ and trailing "
    t_quotes = tmp_path / "tools" / "quote_tool"
    t_quotes.mkdir(parents=True)
    (t_quotes / "quote_tool.json").write_text(
        json.dumps(
            {
                "displayName": "quote_tool",
                "description": (
                    'Parses """triple quoted""" strings and trailing quote "'
                ),
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"text": {"type": "STRING"}},
                },
            }
        ),
        encoding="utf-8",
    )

    # 2. Tool description containing Windows paths (\U) and trailing backslash
    t_slash = tmp_path / "tools" / "path_tool"
    t_slash.mkdir(parents=True)
    (t_slash / "path_tool.json").write_text(
        json.dumps(
            {
                "displayName": "path_tool",
                "description": r"Reads from C:\Users\name\folder\\",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"path": {"type": "STRING"}},
                },
            }
        ),
        encoding="utf-8",
    )

    # 3. Toolset explicit tool declaration with multiline description
    ts_dir = tmp_path / "toolsets" / "crm_suite"
    ts_dir.mkdir(parents=True)
    (ts_dir / "crm_suite.json").write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "sync_contact",
                        "description": (
                            'Line one summary.\nLine two with """quotes"""'
                        ),
                        "parameters": {
                            "type": "OBJECT",
                            "properties": {"contact_id": {"type": "STRING"}},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = generate_stubs(tmp_path)

    # Must compile cleanly with ast.parse without SyntaxError
    ast.parse(result.content)


def test_qa_002_distinct_tools_colliding_after_identifier_sanitization(
    tmp_path: Path,
) -> None:
    """QA-002: Distinct tool names that sanitize to the same identifier
    (e.g. `lookup-user` and `lookup_user`) must not emit duplicate method
    definitions on `TypedTools` (Ruff F811).
    """
    (tmp_path / "app.json").write_text(
        json.dumps({"name": "qa-002-app"}), encoding="utf-8"
    )

    t1 = tmp_path / "tools" / "tool_hyphen"
    t1.mkdir(parents=True)
    (t1 / "tool_hyphen.json").write_text(
        json.dumps(
            {
                "displayName": "lookup-user",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"user_id": {"type": "STRING"}},
                },
            }
        ),
        encoding="utf-8",
    )

    t2 = tmp_path / "tools" / "tool_underscore"
    t2.mkdir(parents=True)
    (t2 / "tool_underscore.json").write_text(
        json.dumps(
            {
                "displayName": "lookup_user",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {"user_id": {"type": "INTEGER"}},
                },
            }
        ),
        encoding="utf-8",
    )

    result = generate_stubs(tmp_path)
    tree = ast.parse(result.content)

    typed_tools_cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "TypedTools"
    )
    method_names = [
        node.name
        for node in typed_tools_cls.body
        if isinstance(node, ast.FunctionDef)
    ]
    assert len(method_names) == 2
    assert len(set(method_names)) == len(method_names), (
        f"Duplicate method names in TypedTools: {method_names}"
    )


def test_qa_003_generated_stubs_pass_ruff_check_and_format(
    tmp_path: Path,
) -> None:
    """QA-003: Generated `.pyi` stub files (including apps with 0 optional tool
    params) must pass `ruff check` (no F401 unused `NotRequired`) and
    `ruff format --check`.
    """
    ruff_bin = _find_ruff_bin()
    if not ruff_bin:
        pytest.skip("ruff binary not available")

    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "name": "qa-003-app",
                "variableDeclarations": [
                    {"name": "session_id", "schema": {"type": "STRING"}},
                ],
            }
        ),
        encoding="utf-8",
    )
    tool_dir = tmp_path / "tools" / "ping"
    tool_dir.mkdir(parents=True)
    (tool_dir / "ping.json").write_text(
        json.dumps(
            {
                "displayName": "ping",
                "description": "Ping the service.",
                "parameters": {
                    "type": "OBJECT",
                    "required": ["target"],
                    "properties": {"target": {"type": "STRING"}},
                },
            }
        ),
        encoding="utf-8",
    )

    result = generate_stubs(tmp_path)

    check_proc = subprocess.run(
        [ruff_bin, "check", str(result.output_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_proc.returncode == 0, (
        f"ruff check failed:\n{check_proc.stdout}\n{check_proc.stderr}"
    )

    fmt_proc = subprocess.run(
        [ruff_bin, "format", "--check", str(result.output_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert fmt_proc.returncode == 0, (
        f"ruff format --check failed:\n{fmt_proc.stdout}\n{fmt_proc.stderr}"
    )


def test_qa_004_cli_app_dir_is_file_exits_cleanly_without_traceback(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """QA-004: Passing a file path instead of a directory to `--app-dir` must
    print a clean error message to stderr and exit 1 without an unhandled
    `NotADirectoryError` traceback.
    """
    app_json_file = tmp_path / "app.json"
    app_json_file.write_text(json.dumps({"name": "file-app"}), encoding="utf-8")

    parser = get_parser()
    args = parser.parse_args(
        ["generate-stubs", "--app-dir", str(app_json_file)]
    )
    with pytest.raises(SystemExit) as exc_info:
        app_generate_stubs(args)
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "ERROR:" in err


# ---------------------------------------------------------------------------
# Passing Edge-Case & E2E Journey Tests
# ---------------------------------------------------------------------------


def test_qa_edge_empty_app_and_yaml_only(tmp_path: Path) -> None:
    """Verify `app.yaml` without `variableDeclarations` and empty `tools/`."""
    (tmp_path / "app.yaml").write_text(
        "name: yaml_only_app\ndisplayName: YAML Only App\n",
        encoding="utf-8",
    )
    (tmp_path / "tools").mkdir()

    result = generate_stubs(tmp_path)
    assert result.variables_count == 0
    assert result.tools_count == 0
    ast.parse(result.content)
    assert "current_date: str" in result.content
    assert "class TypedTools(Protocol):" in result.content


def test_qa_edge_deeply_nested_objects_and_arrays(tmp_path: Path) -> None:
    """Verify deeply nested OBJECT -> ARRAY -> OBJECT -> OBJECT schemas."""
    app_json = {
        "name": "deep-nested-app",
        "variableDeclarations": [
            {
                "name": "order_cart",
                "schema": {
                    "type": "OBJECT",
                    "properties": {
                        "line_items": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "sku": {"type": "STRING"},
                                    "quantity": {"type": "INTEGER"},
                                    "pricing": {
                                        "type": "OBJECT",
                                        "properties": {
                                            "unit-price": {"type": "NUMBER"},
                                            "currency": {"type": "STRING"},
                                        },
                                    },
                                },
                            },
                        }
                    },
                },
            }
        ],
    }
    (tmp_path / "app.json").write_text(json.dumps(app_json), encoding="utf-8")

    result = generate_stubs(tmp_path)
    ast.parse(result.content)
    assert "OrderCartLineItemsItemPricingState = TypedDict(" in result.content
    assert (
        "class OrderCartLineItemsItemState(TypedDict, total=False):"
        in result.content
    )
    assert "    line_items: list[OrderCartLineItemsItemState]" in result.content


def test_qa_edge_complex_python_ast_signatures_and_runtime_import(
    tmp_path: Path,
) -> None:
    """Verify complex Python AST annotations (`Optional[list[dict[str, int]]]`,
    `str | None`, `*args`, `**kwargs`, keyword-only args, `context` first param)
    and runtime import of generated `.py` stub by a typed callback.
    """
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "name": "complex-ast-app",
                "variableDeclarations": [
                    {"name": "customer_tier", "schema": {"type": "STRING"}},
                ],
            }
        ),
        encoding="utf-8",
    )
    pdir = tmp_path / "tools" / "evaluate_order" / "python_function"
    pdir.mkdir(parents=True)
    (tmp_path / "tools" / "evaluate_order" / "evaluate_order.json").write_text(
        json.dumps({"displayName": "evaluate_order"}),
        encoding="utf-8",
    )
    (pdir / "python_code.py").write_text(
        "from typing import Any, Optional, Union\n\n"
        "def evaluate_order(\n"
        "    context: Any,\n"
        "    matrix: Optional[list[dict[str, int]]],\n"
        "    coupon: str | None = None,\n"
        "    fallback: Union[str, int] = 'default',\n"
        "    untyped_param=None,\n"
        "    *args: Any,\n"
        "    strict_mode: bool,\n"
        "    retry_limit: int = 3,\n"
        "    **kwargs: Any,\n"
        ") -> dict[str, Any]:\n"
        '    """Evaluate an order."""\n'
        "    return {'ok': True}\n",
        encoding="utf-8",
    )

    py_stub = tmp_path / "cxas_types.py"
    result = generate_stubs(tmp_path, output_path=py_stub)
    ast.parse(result.content)

    assert "    matrix: list[dict[str, int]] | None" in result.content
    assert "    coupon: NotRequired[str | None]" in result.content
    assert "    fallback: NotRequired[str | int]" in result.content
    assert "    untyped_param: NotRequired[Any]" in result.content
    assert "    strict_mode: bool" in result.content
    assert "    retry_limit: NotRequired[int]" in result.content

    # Verify a sample typed callback can import and execute against the stub
    callback_file = tmp_path / "sample_callback.py"
    callback_file.write_text(
        "from cxas_types import AppState, EvaluateOrderArgs, TypedCallbackContext\n\n"
        "def before_agent_callback(callback_context: TypedCallbackContext) -> None:\n"
        "    state: AppState = callback_context.state\n"
        "    tier = state.get('customer_tier', 'standard')\n"
        "    payload: EvaluateOrderArgs = {\n"
        "        'matrix': [{'a': 1}],\n"
        "        'strict_mode': tier == 'vip',\n"
        "    }\n"
        "    callback_context.tools.evaluate_order(payload)\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "-c", "import sample_callback"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
