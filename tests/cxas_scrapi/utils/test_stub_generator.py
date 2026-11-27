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

"""Unit tests for typed stub generation (`cxas generate-stubs`)."""

from __future__ import annotations

import ast
import json
from typing import TYPE_CHECKING

import pytest

from cxas_scrapi.cli.app import app_generate_stubs
from cxas_scrapi.cli.main import get_parser
from cxas_scrapi.utils.stub_generator import generate_stubs

if TYPE_CHECKING:
    from pathlib import Path


def test_generate_stubs_state_variables_and_environment(
    tmp_path: Path,
) -> None:
    """Verify primitive, array, nested OBJECT, and predefined env variables."""
    app_json = {
        "name": "test-app",
        "displayName": "Test App",
        "variableDeclarations": [
            {"name": "user_name", "schema": {"type": "STRING"}},
            {"name": "retry_count", "schema": {"type": "INTEGER"}},
            {"name": "confidence_score", "schema": {"type": "NUMBER"}},
            {"name": "is_verified", "schema": {"type": "BOOLEAN"}},
            {
                "name": "tags",
                "schema": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
            {"name": "raw_items", "schema": {"type": "ARRAY"}},
            {"name": "freeform_meta", "schema": {"type": "OBJECT"}},
            {
                "name": "customer",
                "schema": {
                    "type": "OBJECT",
                    "properties": {
                        "account_id": {"type": "STRING"},
                        "tier": {"type": "INTEGER"},
                        "profile": {
                            "type": "OBJECT",
                            "properties": {
                                "zip_code": {"type": "STRING"},
                            },
                        },
                    },
                },
            },
        ],
    }
    env_json = {
        "predefinedVariableDeclarations": [
            {"name": "env_region", "schema": {"type": "STRING"}},
            {"name": "feature_enabled", "schema": {"type": "BOOL"}},
        ]
    }
    (tmp_path / "app.json").write_text(json.dumps(app_json), encoding="utf-8")
    (tmp_path / "environment.json").write_text(
        json.dumps(env_json), encoding="utf-8"
    )

    result = generate_stubs(tmp_path)

    assert result.variables_count == 10
    assert result.tools_count == 0
    assert result.up_to_date is True
    assert result.output_path == tmp_path / "cxas_types.pyi"
    assert result.output_path.is_file()

    # Must compile cleanly as valid Python syntax
    ast.parse(result.content)

    # Check nested TypedDicts and AppState fields
    assert "class CustomerProfileState(TypedDict, total=False):" in (
        result.content
    )
    assert "    zip_code: str" in result.content
    assert "class CustomerState(TypedDict, total=False):" in result.content
    assert "    account_id: str" in result.content
    assert "    tier: int" in result.content
    assert "    profile: CustomerProfileState" in result.content

    assert "class AppState(TypedDict, total=False):" in result.content
    assert "    user_name: str" in result.content
    assert "    retry_count: int" in result.content
    assert "    confidence_score: float" in result.content
    assert "    is_verified: bool" in result.content
    assert "    tags: list[str]" in result.content
    assert "    raw_items: list[Any]" in result.content
    assert "    freeform_meta: dict[str, Any]" in result.content
    assert "    customer: CustomerState" in result.content
    assert "    env_region: str" in result.content
    assert "    feature_enabled: bool" in result.content
    assert "    current_date: str" in result.content

    # Check context classes
    assert "class TypedCallbackContext:" in result.content
    assert "class TypedToolContext(TypedCallbackContext):" in result.content
    assert "    state: AppState" in result.content
    assert "    variables: AppState" in result.content
    assert "    tools: TypedTools" in result.content


def test_generate_stubs_python_tools_json_tools_and_toolsets(
    tmp_path: Path,
) -> None:
    """Verify AST extraction, JSON-schema tools, OpenAPI toolsets, and Xbox v2 signature."""
    (tmp_path / "app.yaml").write_text(
        "name: demo\n"
        "variable_declarations:\n"
        "  - name: session_locale\n"
        "    schema:\n"
        "      type: STRING\n",
        encoding="utf-8",
    )

    # 1. Python function tool with AST signature
    py_tool_dir = tmp_path / "tools" / "book_reservation" / "python_function"
    py_tool_dir.mkdir(parents=True)
    (
        tmp_path / "tools" / "book_reservation" / "book_reservation.json"
    ).write_text(
        json.dumps(
            {
                "displayName": "book_reservation",
                "pythonFunction": {
                    "name": "book_reservation",
                    "pythonCode": (
                        "tools/book_reservation/python_function/python_code.py"
                    ),
                    "description": "Book a table reservation.",
                },
            }
        ),
        encoding="utf-8",
    )
    (py_tool_dir / "python_code.py").write_text(
        "from typing import Any\n\n"
        "def _helper(x: int) -> int:\n"
        "    return x\n\n"
        "def book_reservation(\n"
        "    party_size: int,\n"
        "    preferred_date: str,\n"
        "    tags: list[str],\n"
        "    notes: str = '',\n"
        "    vip: bool = False,\n"
        "    tool_context: Any = None,\n"
        ") -> dict[str, Any]:\n"
        '    """Book a reservation."""\n'
        "    return {'ok': True}\n",
        encoding="utf-8",
    )

    # 2. JSON-schema tool (no Python code)
    json_tool_dir = tmp_path / "tools" / "lookup_customer"
    json_tool_dir.mkdir(parents=True)
    (json_tool_dir / "lookup_customer.json").write_text(
        json.dumps(
            {
                "displayName": "lookup_customer",
                "description": "Look up customer by phone or ID.",
                "parameters": {
                    "type": "OBJECT",
                    "required": ["phone_number"],
                    "properties": {
                        "phone_number": {"type": "STRING"},
                        "include_history": {"type": "BOOLEAN"},
                        "filter_options": {
                            "type": "OBJECT",
                            "properties": {
                                "max_results": {"type": "INTEGER"},
                            },
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    # 3. OpenAPI toolset under toolsets/
    ts_dir = tmp_path / "toolsets" / "billing_api"
    (ts_dir / "open_api_toolset").mkdir(parents=True)
    (ts_dir / "billing_api.json").write_text(
        json.dumps(
            {
                "displayName": "billing_api",
                "openApiToolset": {
                    "openApiSchema": "open_api_toolset/open_api_schema.yaml"
                },
            }
        ),
        encoding="utf-8",
    )
    (ts_dir / "open_api_toolset" / "open_api_schema.yaml").write_text(
        "openapi: 3.0.0\n"
        "paths:\n"
        "  /v1/invoices:\n"
        "    get:\n"
        "      operationId: listInvoices\n"
        "      summary: List customer invoices\n"
        "      parameters:\n"
        "        - name: account_id\n"
        "          in: query\n"
        "          required: true\n"
        "          schema:\n"
        "            type: string\n"
        "        - name: limit\n"
        "          in: query\n"
        "          required: false\n"
        "          schema:\n"
        "            type: integer\n",
        encoding="utf-8",
    )

    result = generate_stubs(tmp_path)

    assert result.variables_count == 1
    assert result.tools_count == 3
    ast.parse(result.content)

    # Check BookReservationArgs (required vs NotRequired)
    assert "class BookReservationArgs(TypedDict, total=False):" in (
        result.content
    )
    assert "    party_size: int" in result.content
    assert "    preferred_date: str" in result.content
    assert "    tags: list[str]" in result.content
    assert "    notes: NotRequired[str]" in result.content
    assert "    vip: NotRequired[bool]" in result.content
    assert "tool_context" not in result.content

    # Check LookupCustomerArgs
    assert (
        "class LookupCustomerFilterOptionsParam(TypedDict, total=False):"
        in (result.content)
    )
    assert "    max_results: int" in result.content
    assert "class LookupCustomerArgs(TypedDict, total=False):" in (
        result.content
    )
    assert "    phone_number: str" in result.content
    assert "    include_history: NotRequired[bool]" in result.content
    assert (
        "    filter_options: NotRequired[LookupCustomerFilterOptionsParam]"
        in result.content
    )

    # Check OpenAPI toolset BillingApiListInvoicesArgs
    assert "class BillingApiListInvoicesArgs(TypedDict, total=False):" in (
        result.content
    )
    assert "    account_id: str" in result.content
    assert "    limit: NotRequired[int]" in result.content

    # Check Xbox v2 positional-only dict signature in TypedTools Protocol
    assert "class TypedTools(Protocol):" in result.content
    assert (
        "    def book_reservation(\n"
        "        self,\n"
        "        args: BookReservationArgs | dict[str, Any] | None = None,\n"
        "        /,\n"
        "    ) -> dict[str, Any]: ..."
    ) in result.content
    assert (
        "    def lookup_customer(\n"
        "        self,\n"
        "        args: LookupCustomerArgs | dict[str, Any] | None = None,\n"
        "        /,\n"
        "    ) -> dict[str, Any]: ..."
    ) in result.content
    assert (
        "    def billing_api_listInvoices(\n"
        "        self,\n"
        "        args: BillingApiListInvoicesArgs | dict[str, Any] | None = None,\n"
        "        /,\n"
        "    ) -> dict[str, Any]: ..."
    ) in result.content


def test_generate_stubs_special_characters_and_keywords_valid_syntax(
    tmp_path: Path,
) -> None:
    """Verify functional TypedDict fallback when keys have hyphens/dots/keywords."""
    app_json = {
        "name": "special-chars-app",
        "variableDeclarations": [
            {"name": "customer-id", "schema": {"type": "STRING"}},
            {"name": "session.step", "schema": {"type": "INTEGER"}},
            {"name": "from", "schema": {"type": "STRING"}},
            {"name": "123_flag", "schema": {"type": "BOOLEAN"}},
            {
                "name": "nested_ weird",
                "schema": {
                    "type": "OBJECT",
                    "properties": {
                        "class": {"type": "STRING"},
                        "x-header": {"type": "STRING"},
                    },
                },
            },
        ],
    }
    (tmp_path / "app.json").write_text(json.dumps(app_json), encoding="utf-8")

    # Tool with keyword and hyphenated parameter names & keyword tool name
    tool_dir = tmp_path / "tools" / "from"
    tool_dir.mkdir(parents=True)
    (tool_dir / "from.json").write_text(
        json.dumps(
            {
                "displayName": "from",
                "parameters": {
                    "type": "OBJECT",
                    "required": ["from"],
                    "properties": {
                        "from": {"type": "STRING"},
                        "x-request-id": {"type": "STRING"},
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    result = generate_stubs(tmp_path)

    # Must compile as 100% valid Python syntax
    tree = ast.parse(result.content)
    assert tree is not None

    # Functional TypedDict syntax used for AppState and tool Args
    assert 'AppState = TypedDict(\n    "AppState",' in result.content
    assert '"customer-id": str,' in result.content
    assert '"session.step": int,' in result.content
    assert '"from": str,' in result.content
    assert '"123_flag": bool,' in result.content
    assert "def from_(" in result.content


def test_generate_stubs_check_mode_and_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify --check mode and CLI subcommand execution."""
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "name": "cli-app",
                "variableDeclarations": [
                    {"name": "auth_status", "schema": {"type": "STRING"}}
                ],
            }
        ),
        encoding="utf-8",
    )

    # 1. --check when stub file does not exist -> up_to_date is False, CLI exits 1
    check_missing = generate_stubs(tmp_path, check=True)
    assert check_missing.up_to_date is False
    assert not (tmp_path / "cxas_types.pyi").exists()

    parser = get_parser()
    args_check_fail = parser.parse_args(
        ["generate-stubs", "--app-dir", str(tmp_path), "--check"]
    )
    with pytest.raises(SystemExit) as exc_info:
        app_generate_stubs(args_check_fail)
    assert exc_info.value.code == 1

    # 2. Generate stubs via CLI
    args_gen = parser.parse_args(["generate-stubs", "--app-dir", str(tmp_path)])
    app_generate_stubs(args_gen)
    stub_file = tmp_path / "cxas_types.pyi"
    assert stub_file.is_file()
    out_gen = capsys.readouterr().out
    assert "Generated typed stubs" in out_gen

    # 3. --check when stub file is in sync -> exits 0
    args_check_ok = parser.parse_args(
        ["generate-stubs", "--app-dir", str(tmp_path), "--check"]
    )
    with pytest.raises(SystemExit) as exc_ok:
        app_generate_stubs(args_check_ok)
    assert exc_ok.value.code == 0
    out_ok = capsys.readouterr().out
    assert "Stubs are up to date" in out_ok

    # 4. Modify stub file -> --check detects drift and exits 1 without overwriting
    drifted_text = stub_file.read_text(encoding="utf-8") + "\n# drifted\n"
    stub_file.write_text(drifted_text, encoding="utf-8")
    with pytest.raises(SystemExit) as exc_drift:
        app_generate_stubs(args_check_fail)
    assert exc_drift.value.code == 1
    assert stub_file.read_text(encoding="utf-8") == drifted_text

    # 5. --stdout prints generated stubs to stdout without writing default file
    custom_dir = tmp_path / "stdout_only"
    custom_dir.mkdir()
    (custom_dir / "app.json").write_text(
        json.dumps({"name": "stdout-app", "variableDeclarations": []}),
        encoding="utf-8",
    )
    capsys.readouterr()
    args_stdout = parser.parse_args(
        ["generate-stubs", "--app-dir", str(custom_dir), "--stdout"]
    )
    app_generate_stubs(args_stdout)
    stdout_captured = capsys.readouterr().out
    assert "class AppState(TypedDict, total=False):" in stdout_captured
    assert not (custom_dir / "cxas_types.pyi").exists()


def test_generate_stubs_negative_numeric_defaults_inference(
    tmp_path: Path,
) -> None:
    """Verify unannotated parameters with negative numeric defaults infer int/float."""
    (tmp_path / "app.json").write_text(
        json.dumps({"name": "neg-default-app"}), encoding="utf-8"
    )
    tool_dir = tmp_path / "tools" / "adjust_score" / "python_function"
    tool_dir.mkdir(parents=True)
    (tmp_path / "tools" / "adjust_score" / "adjust_score.json").write_text(
        json.dumps({"displayName": "adjust_score"}), encoding="utf-8"
    )
    (tool_dir / "python_code.py").write_text(
        "def adjust_score(offset=-1, weight=-0.5, bonus=+10):\n"
        '    """Adjust score."""\n'
        "    return {'ok': True}\n",
        encoding="utf-8",
    )

    result = generate_stubs(tmp_path)
    ast.parse(result.content)
    assert "    offset: NotRequired[int]" in result.content
    assert "    weight: NotRequired[float]" in result.content
    assert "    bonus: NotRequired[int]" in result.content
