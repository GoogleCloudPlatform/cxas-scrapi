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

"""Unit tests for SAST security lint rules (SEC001-SEC005)."""

from pathlib import Path

import pytest

from cxas_scrapi.utils.lint_rules.security import (
    CallbackDangerousCodeExecution,
    CallbackHardcodedSecretOrToken,
    CallbackProhibitedSandboxImport,
    CallbackSensitiveDataLogging,
    CallbackUnsafeDeserialization,
    ToolDangerousCodeExecution,
    ToolHardcodedSecretOrToken,
    ToolProhibitedSandboxImport,
    ToolSensitiveDataLogging,
    ToolUnsafeDeserialization,
    iter_agent_callback_files,
)
from cxas_scrapi.utils.linter import (
    Discovery,
    LintConfig,
    LintContext,
    LintReport,
    Severity,
    build_context,
    build_registry,
    run_rules,
)


@pytest.fixture
def context(tmp_path: Path) -> LintContext:
    return LintContext(
        project_root=tmp_path,
        app_dir=tmp_path,
        evals_dir=tmp_path / "evals",
        app_root=tmp_path,
    )


def _write_callback(tmp_path: Path, code: str) -> Path:
    cb_dir = (
        tmp_path
        / "agents"
        / "root_agent"
        / "before_model_callbacks"
        / "guard_01"
    )
    cb_dir.mkdir(parents=True, exist_ok=True)
    f = cb_dir / "python_code.py"
    f.write_text(code)
    return f


def _write_tool(tmp_path: Path, code: str, name: str = "lookup_user") -> Path:
    tool_dir = tmp_path / "tools" / name / "python_function"
    tool_dir.mkdir(parents=True, exist_ok=True)
    f = tool_dir / "python_code.py"
    f.write_text(code)
    return f


# ── SEC001: dangerous-code-execution ─────────────────────────────────────


@pytest.mark.parametrize(
    ("snippet", "expected_token"),
    [
        ("eval(user_expr)", "eval"),
        ("exec(code_str)", "exec"),
        ("compile(src, '<string>', 'exec')", "compile"),
        ("__import__('os')", "__import__"),
        ("obj.__subclasses__()", "__subclasses__"),
        ("fn.__globals__['os']", "__globals__"),
        ("fn.__builtins__", "__builtins__"),
        ("fn.__code__", "__code__"),
    ],
)
def test_sec001_flags_dangerous_execution_in_callbacks_and_tools(
    tmp_path: Path,
    context: LintContext,
    snippet: str,
    expected_token: str,
) -> None:
    cb_file = _write_callback(
        tmp_path,
        f"def before_model_callback(callback_context, llm_request):\n"
        f"    {snippet}\n",
    )
    cb_results = CallbackDangerousCodeExecution().check(
        cb_file, cb_file.read_text(), context
    )
    assert len(cb_results) == 1
    assert cb_results[0].rule_id == "SEC001"
    assert cb_results[0].severity == Severity.ERROR
    assert expected_token in cb_results[0].message
    assert cb_results[0].line == 2

    tool_file = _write_tool(
        tmp_path,
        f"def lookup_user(query: str = '') -> dict:\n"
        f"    {snippet}\n"
        f"    return {{}}\n",
    )
    tool_results = ToolDangerousCodeExecution().check(
        tool_file, tool_file.read_text(), context
    )
    assert len(tool_results) == 1
    assert tool_results[0].rule_id == "SEC001"
    assert expected_token in tool_results[0].message


def test_sec001_allows_safe_code_and_re_compile(
    tmp_path: Path, context: LintContext
) -> None:
    safe_code = (
        "import re\n\n"
        "PATTERN = re.compile(r'^\\d+$')\n\n"
        "def lookup_user(query: str = '') -> dict:\n"
        "    return {'matched': bool(PATTERN.match(query))}\n"
    )
    tool_file = _write_tool(tmp_path, safe_code)
    assert (
        ToolDangerousCodeExecution().check(
            tool_file, tool_file.read_text(), context
        )
        == []
    )


# ── SEC002: prohibited-sandbox-import ────────────────────────────────────


@pytest.mark.parametrize(
    "import_stmt",
    [
        "import subprocess",
        "from subprocess import Popen",
        "import socket",
        "import ctypes",
        "import multiprocessing",
        "import pty",
        "import fcntl",
        "import resource",
        "import pip",
        "import venv",
        "import ensurepip",
    ],
)
def test_sec002_flags_prohibited_sandbox_imports(
    tmp_path: Path, context: LintContext, import_stmt: str
) -> None:
    cb_file = _write_callback(
        tmp_path,
        f"{import_stmt}\n\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    return None\n",
    )
    results = CallbackProhibitedSandboxImport().check(
        cb_file, cb_file.read_text(), context
    )
    assert len(results) == 1
    assert results[0].rule_id == "SEC002"
    assert results[0].severity == Severity.ERROR
    assert results[0].line == 1


@pytest.mark.parametrize(
    ("call_block", "expected_call"),
    [
        ("import os\nos.system('ls')", "os.system"),
        ("import os\nos.popen('id')", "os.popen"),
        ("import os\nos.spawnlp(0, 'sh', 'sh')", "os.spawnlp"),
        ("import os\nos.execvp('sh', ['sh'])", "os.execvp"),
        ("import os\nos.fork()", "os.fork"),
        ("import os\nos.kill(1, 9)", "os.kill"),
        ("import shutil\nshutil.rmtree('/tmp/data')", "shutil.rmtree"),
        ("from os import system\nsystem('whoami')", "os.system"),
        ("from shutil import rmtree\nrmtree('/tmp/x')", "shutil.rmtree"),
    ],
)
def test_sec002_flags_blocked_os_and_shutil_calls(
    tmp_path: Path,
    context: LintContext,
    call_block: str,
    expected_call: str,
) -> None:
    tool_file = _write_tool(
        tmp_path,
        "def lookup_user(query: str = '') -> dict:\n"
        + "\n".join(f"    {line}" for line in call_block.splitlines())
        + "\n    return {}\n",
    )
    results = ToolProhibitedSandboxImport().check(
        tool_file, tool_file.read_text(), context
    )
    assert len(results) >= 1
    assert all(r.rule_id == "SEC002" for r in results)
    assert any(expected_call in r.message for r in results)


def test_sec002_allows_safe_os_environ_and_standard_imports(
    tmp_path: Path, context: LintContext
) -> None:
    safe_code = (
        "import json\n"
        "import math\n"
        "import os\n\n"
        "def lookup_user(query: str = '') -> dict:\n"
        "    env_name = os.environ.get('APP_ENV', 'prod')\n"
        "    return {'env': env_name, 'pi': math.pi}\n"
    )
    tool_file = _write_tool(tmp_path, safe_code)
    assert (
        ToolProhibitedSandboxImport().check(
            tool_file, tool_file.read_text(), context
        )
        == []
    )


# ── SEC003: hardcoded-secret-or-token ────────────────────────────────────


_FAKE_GAPI_KEY = "AIza" + "SyD-9876543210abcdefGHIJKLMN0123456"
_FAKE_OAUTH_TOK = "ya29." + "a0AfH6SMBx9876543210abcdefGHIJKLMN"
_FAKE_PEM_HEADER = "-----BEGIN RSA " + "PRIVATE KEY-----\\nMIIEowIBAAKCAQEA"
_FAKE_GH_PAT = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyz"
_FAKE_SLACK_TOK = "xoxb-" + "123456789012-123456789012-abcdef"


@pytest.mark.parametrize(
    "secret_code",
    [
        "api_key = 'sk-live-9876543210abcdef9876543210'",
        "client_secret: str = 'super_secret_prod_value_99'",
        "headers = {'access_token': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'}",
        "cfg['password'] = 'Hunter2Prod!99'",
        "connect(auth_token='real_production_bearer_998877')",
        f"key = '{_FAKE_GAPI_KEY}'",
        f"tok = '{_FAKE_OAUTH_TOK}'",
        f"pem = '{_FAKE_PEM_HEADER}'",
        f"gh = '{_FAKE_GH_PAT}'",
        f"slack = '{_FAKE_SLACK_TOK}'",
    ],
)
def test_sec003_flags_hardcoded_secrets_and_token_prefixes(
    tmp_path: Path, context: LintContext, secret_code: str
) -> None:
    tool_file = _write_tool(
        tmp_path,
        f"def lookup_user(query: str = '') -> dict:\n"
        f"    {secret_code}\n"
        f"    return {{}}\n",
    )
    results = ToolHardcodedSecretOrToken().check(
        tool_file, tool_file.read_text(), context
    )
    assert len(results) == 1
    assert results[0].rule_id == "SEC003"
    assert results[0].severity == Severity.ERROR
    assert results[0].line == 2


@pytest.mark.parametrize(
    "safe_code",
    [
        "api_key = ''",
        "api_key = 'REDACTED'",
        "api_key = '<API_KEY>'",
        "api_key = 'your_api_key_here'",
        "password = 'dummy'",
        "auth_token = 'mock_token'",
        "api_key = 'test_key'",
        "password = '***'",
        "password = '••••'",
        "api_key = os.environ.get('API_KEY')",
        "api_key = callback_context.state.get('api_key')",
        "auth_token = state['auth_token']",
        "headers = {'access_token': context.state.get('token')}",
    ],
)
def test_sec003_ignores_placeholders_empty_strings_and_dynamic_reads(
    tmp_path: Path, context: LintContext, safe_code: str
) -> None:
    cb_file = _write_callback(
        tmp_path,
        "import os\n\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    state = callback_context.state\n"
        "    context = callback_context\n"
        f"    {safe_code}\n"
        "    return None\n",
    )
    assert (
        CallbackHardcodedSecretOrToken().check(
            cb_file, cb_file.read_text(), context
        )
        == []
    )


# ── SEC004: unsafe-deserialization ───────────────────────────────────────


@pytest.mark.parametrize(
    ("unsafe_code", "expected_label"),
    [
        ("import pickle\npickle.loads(payload)", "pickle.loads"),
        ("import pickle\npickle.load(fp)", "pickle.load"),
        ("import cPickle\ncPickle.loads(payload)", "cPickle.loads"),
        ("import marshal\nmarshal.loads(payload)", "marshal.loads"),
        ("import shelve\nshelve.open('/tmp/cache')", "shelve.open"),
        ("from pickle import loads\nloads(payload)", "pickle.loads"),
        ("import yaml\nyaml.load(raw_yaml)", "yaml.load"),
        (
            "import yaml\nyaml.load(raw_yaml, Loader=yaml.FullLoader)",
            "yaml.load",
        ),
        (
            "import yaml\nyaml.load(raw_yaml, yaml.UnsafeLoader)",
            "yaml.load",
        ),
    ],
)
def test_sec004_flags_unsafe_deserialization(
    tmp_path: Path,
    context: LintContext,
    unsafe_code: str,
    expected_label: str,
) -> None:
    tool_file = _write_tool(
        tmp_path,
        "def lookup_user(payload: str = '') -> dict:\n"
        + "\n".join(f"    {line}" for line in unsafe_code.splitlines())
        + "\n    return {}\n",
    )
    results = ToolUnsafeDeserialization().check(
        tool_file, tool_file.read_text(), context
    )
    assert len(results) == 1
    assert results[0].rule_id == "SEC004"
    assert results[0].severity == Severity.ERROR
    assert expected_label in results[0].message


@pytest.mark.parametrize(
    "safe_yaml_code",
    [
        "import yaml\nyaml.safe_load(raw_yaml)",
        "import yaml\nyaml.load(raw_yaml, Loader=yaml.SafeLoader)",
        "import yaml\nyaml.load(raw_yaml, Loader=yaml.CSafeLoader)",
        "import yaml\nyaml.load(raw_yaml, yaml.SafeLoader)",
        "from yaml import SafeLoader, load\nload(raw_yaml, Loader=SafeLoader)",
        "import json\njson.loads(raw_yaml)",
    ],
)
def test_sec004_allows_safe_yaml_and_json_loading(
    tmp_path: Path, context: LintContext, safe_yaml_code: str
) -> None:
    cb_file = _write_callback(
        tmp_path,
        "def before_model_callback(callback_context, llm_request):\n"
        "    raw_yaml = '{}'\n"
        + "\n".join(f"    {line}" for line in safe_yaml_code.splitlines())
        + "\n    return None\n",
    )
    assert (
        CallbackUnsafeDeserialization().check(
            cb_file, cb_file.read_text(), context
        )
        == []
    )


# ── SEC005: sensitive-data-logging ───────────────────────────────────────


@pytest.mark.parametrize(
    ("log_stmt", "expected_ident"),
    [
        ("print(password)", "password"),
        ("print(f'User SSN is {user_ssn}')", "ssn"),
        ("logging.info(f'Verifying pin={pin}')", "pin"),
        ("logging.debug(state['card_number'])", "card_number"),
        ("logging.warning(callback_context.state.get('cvv'))", "cvv"),
        ("logger.error('Token: %s', access_token)", "access_token"),
        ("logging.exception(f'Auth failed for {api_key}')", "api_key"),
        ("print(state['social_security'])", "social_security"),
    ],
)
def test_sec005_warns_on_sensitive_data_logging(
    tmp_path: Path,
    context: LintContext,
    log_stmt: str,
    expected_ident: str,
) -> None:
    cb_file = _write_callback(
        tmp_path,
        "import logging\n"
        "logger = logging.getLogger(__name__)\n\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    state = callback_context.state\n"
        f"    {log_stmt}\n"
        "    return None\n",
    )
    results = CallbackSensitiveDataLogging().check(
        cb_file, cb_file.read_text(), context
    )
    assert len(results) == 1
    assert results[0].rule_id == "SEC005"
    assert results[0].severity == Severity.WARNING
    assert expected_ident in results[0].message


def test_sec005_allows_non_sensitive_and_static_prompt_logging(
    tmp_path: Path, context: LintContext
) -> None:
    safe_logging = (
        "import logging\n\n"
        "def lookup_user(account_id: str = '') -> dict:\n"
        "    print('Prompting user to enter password or pin')\n"
        "    logging.info(f'Looking up account_id={account_id}')\n"
        "    return {'status': 'ok'}\n"
    )
    tool_file = _write_tool(tmp_path, safe_logging)
    assert (
        ToolSensitiveDataLogging().check(
            tool_file, tool_file.read_text(), context
        )
        == []
    )


# ── End-to-End Runner & Discovery Integration ────────────────────────────


def test_iter_agent_callback_files_and_run_rules_integration(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.json").write_text(
        '{"name": "sec_app", "displayName": "Sec App", '
        '"rootAgent": "root_agent"}'
    )
    agent_dir = tmp_path / "agents" / "root_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "instruction.txt").write_text("<role>Test</role>")
    (agent_dir / "root_agent.json").write_text(
        '{"name": "root_agent", "displayName": "root_agent", '
        '"tools": ["end_session", "unsafe_tool"]}'
    )

    cb_file = _write_callback(
        tmp_path,
        "import subprocess\n\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    eval('1 + 1')\n"
        "    return None\n",
    )
    _write_tool(
        tmp_path,
        '"""Unsafe tool docstring."""\n'
        "import pickle\n\n"
        "def unsafe_tool(data: str = '') -> dict:\n"
        f"    api_key = '{_FAKE_GAPI_KEY}'\n"
        "    print(api_key)\n"
        "    return pickle.loads(b'')\n",
        name="unsafe_tool",
    )

    discovered_cbs = iter_agent_callback_files(tmp_path)
    assert discovered_cbs == [cb_file]

    config = LintConfig()
    discovery = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, config, discovery)
    registry = build_registry()
    report = LintReport()
    run_rules(
        registry,
        config,
        ctx,
        discovery,
        report,
        specific_rules={"SEC001", "SEC002", "SEC003", "SEC004", "SEC005"},
    )

    fired_ids = {r.rule_id for r in report.results}
    assert fired_ids == {"SEC001", "SEC002", "SEC003", "SEC004", "SEC005"}


# ── Adversarial QA Tests (PR 6: SEC001-SEC005) ───────────────────────────


def test_qa_multi_category_no_duplicate_findings_and_cxaslint_off(
    tmp_path: Path,
) -> None:
    """Verify SEC001-SEC005 do not emit duplicate findings per file and
    respect ``cxaslint.yaml`` ``rules: {SEC001: off}`` across both
    ``callbacks`` and ``tools`` registrations.
    """
    (tmp_path / "app.json").write_text(
        '{"name": "qa_app", "displayName": "QA App", "rootAgent": "root_agent"}'
    )
    agent_dir = tmp_path / "agents" / "root_agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "instruction.txt").write_text("<role>Test</role>")
    (agent_dir / "root_agent.json").write_text(
        '{"name": "root_agent", "displayName": "root_agent", '
        '"tools": ["end_session", "lookup_user"]}'
    )

    _write_callback(
        tmp_path,
        "def before_model_callback(callback_context, llm_request):\n"
        "    eval('1 + 1')\n"
        "    return None\n",
    )
    _write_tool(
        tmp_path,
        '"""Tool docstring."""\n'
        "def lookup_user(query: str = '') -> dict:\n"
        "    eval('2 + 2')\n"
        "    return {}\n",
    )

    config = LintConfig.load(tmp_path)
    discovery = Discovery(tmp_path, tmp_path / "evals")
    ctx = build_context(tmp_path, config, discovery)
    registry = build_registry()

    report = LintReport()
    run_rules(
        registry,
        config,
        ctx,
        discovery,
        report,
        specific_rules={"SEC001"},
    )
    # Exactly 1 finding for callback and 1 for tool (no duplicate emission)
    assert len(report.results) == 2  # noqa: PLR2004
    files_flagged = [r.file for r in report.results]
    assert len(files_flagged) == len(set(files_flagged))

    # Disabling SEC001 in cxaslint.yaml must silence BOTH registrations
    (tmp_path / "cxaslint.yaml").write_text("rules:\n  SEC001: off\n")
    config_off = LintConfig.load(tmp_path)
    report_off = LintReport()
    run_rules(
        registry,
        config_off,
        ctx,
        discovery,
        report_off,
        specific_rules={"SEC001"},
    )
    assert report_off.results == []


def test_qa_sec001_builtins_and_chained_dunders(
    tmp_path: Path, context: LintContext
) -> None:
    """Verify SEC001 catches builtins.eval and chained __subclasses__."""
    code = (
        "import builtins\n"
        "import re\n\n"
        "PAT = re.compile(r'^\\d+$')\n\n"
        "def lookup_user(x: str = '') -> dict:\n"
        "    builtins.eval(x)\n"
        "    cls_list = ().__class__.__bases__[0].__subclasses__()\n"
        "    return {'n': len(cls_list)}\n"
    )
    tool_file = _write_tool(tmp_path, code)
    results = ToolDangerousCodeExecution().check(
        tool_file, tool_file.read_text(), context
    )
    assert [r.line for r in results] == [7, 8]


def test_qa_sec002_aliased_imports_and_safe_os_path(
    tmp_path: Path, context: LintContext
) -> None:
    """Verify SEC002 catches aliased imports/calls and allows os.path.join."""
    code = (
        "import os\n"
        "import os as my_os\n"
        "import subprocess as sp\n"
        "from os import system as run_cmd\n\n"
        "def lookup_user(p: str = '') -> dict:\n"
        "    safe = os.path.join('/tmp', p)\n"
        "    env = os.environ.get('HOME', '')\n"
        "    my_os.system('id')\n"
        "    run_cmd('whoami')\n"
        "    sp.run(['ls'])\n"
        "    return {'safe': safe, 'env': env}\n"
    )
    tool_file = _write_tool(tmp_path, code)
    results = ToolProhibitedSandboxImport().check(
        tool_file, tool_file.read_text(), context
    )
    flagged_lines = [r.line for r in results]
    assert 3 in flagged_lines  # import subprocess as sp
    assert 4 in flagged_lines  # from os import system as run_cmd
    assert 9 in flagged_lines  # my_os.system('id')
    assert 10 in flagged_lines  # run_cmd('whoami')
    assert 7 not in flagged_lines  # os.path.join is safe
    assert 8 not in flagged_lines  # os.environ.get is safe


@pytest.mark.parametrize(
    "safe_non_secret_code",
    [
        'monkey = "banana"',
        'donkey = "burro"',
        "token_count = 5",
        "max_tokens = 100",
        "pin_verified = False",
        "password_reset_requested = True",
        'secret_question_asked = "mother_maiden_name"',
        'val = state["api_key"]',
    ],
)
def test_qa_sec003_allows_non_secret_substrings_and_booleans(
    tmp_path: Path, context: LintContext, safe_non_secret_code: str
) -> None:
    """Verify SEC003 does not false-positive on substring collisions."""
    cb_file = _write_callback(
        tmp_path,
        "def before_model_callback(callback_context, llm_request):\n"
        "    state = callback_context.state\n"
        f"    {safe_non_secret_code}\n"
        "    return None\n",
    )
    assert (
        CallbackHardcodedSecretOrToken().check(
            cb_file, cb_file.read_text(), context
        )
        == []
    )


def test_qa_001_sec003_flags_bearer_jwt_and_sk_live_in_authorization_header(
    tmp_path: Path, context: LintContext
) -> None:
    """QA-001: SEC003 must flag hardcoded Bearer JWTs / API keys under
    ``{"Authorization": "Bearer ..."}`` and ``{"X-API-Key": "..."}``.
    """
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    )
    code = (
        "def lookup_user(query: str = '') -> dict:\n"
        f'    headers = {{"Authorization": "Bearer {jwt}"}}\n'
        "    return headers\n"
    )
    tool_file = _write_tool(tmp_path, code)
    results = ToolHardcodedSecretOrToken().check(
        tool_file, tool_file.read_text(), context
    )
    assert len(results) == 1
    assert results[0].rule_id == "SEC003"
    assert results[0].line == 2


@pytest.mark.parametrize(
    "safe_log_stmt",
    [
        'print("PIN verification succeeded")',
        'print(f"pin_verified={pin_verified}")',
        'print(" Resetting password flow")',
        'print(f"password_reset_requested={password_reset_requested}")',
        'logging.info(f"secret_question_asked={secret_question_asked}")',
        'logging.info(f"auth_token_present={auth_token_present}")',
    ],
)
def test_qa_002_sec005_does_not_false_positive_on_boolean_status_flags(
    tmp_path: Path, context: LintContext, safe_log_stmt: str
) -> None:
    """QA-002: SEC005 must not falsely flag non-secret boolean/status flags
    like ``pin_verified`` or ``password_reset_requested`` when logged.
    """
    cb_file = _write_callback(
        tmp_path,
        "import logging\n\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    pin_verified = False\n"
        "    password_reset_requested = True\n"
        "    secret_question_asked = 'mother_maiden_name'\n"
        "    auth_token_present = True\n"
        f"    {safe_log_stmt}\n"
        "    return None\n",
    )
    assert (
        CallbackSensitiveDataLogging().check(
            cb_file, cb_file.read_text(), context
        )
        == []
    )


def test_qa_003_sec005_flags_concatenated_fstring_and_keyword_extra(
    tmp_path: Path, context: LintContext
) -> None:
    """QA-003: SEC005 must detect sensitive variables in concatenated
    f-strings (where the first operand is safe) and ``extra=...`` kwargs.
    """
    code = (
        "import logging\n\n"
        "def before_model_callback(callback_context, llm_request):\n"
        "    user_id = 'u123'\n"
        "    password = 'secret'\n"
        "    logging.info(f'user={user_id}, ' + f'password={password}')\n"
        "    logging.info('auth event', extra={'password': password})\n"
        "    return None\n"
    )
    cb_file = _write_callback(tmp_path, code)
    results = CallbackSensitiveDataLogging().check(
        cb_file, cb_file.read_text(), context
    )
    assert [r.line for r in results] == [6, 7]


@pytest.mark.parametrize(
    "unsafe_yaml_call",
    [
        "import yaml\nyaml.load(data, Loader=yaml.Loader)",
        "import yaml\nyaml.unsafe_load(data)",
        "import yaml\nyaml.full_load(data)",
        "import yaml\nyaml.load_all(data)",
        "import _pickle\n_pickle.loads(data)",
    ],
)
def test_qa_004_sec004_flags_yaml_unsafe_load_and_underscore_pickle(
    tmp_path: Path, context: LintContext, unsafe_yaml_call: str
) -> None:
    """QA-004: SEC004 must flag ``yaml.unsafe_load()``, ``yaml.full_load()``,
    ``yaml.load_all()`` (without SafeLoader), and ``_pickle.loads()``.
    """
    tool_file = _write_tool(
        tmp_path,
        "def lookup_user(data: str = '') -> dict:\n"
        + "\n".join(f"    {line}" for line in unsafe_yaml_call.splitlines())
        + "\n    return {}\n",
    )
    results = ToolUnsafeDeserialization().check(
        tool_file, tool_file.read_text(), context
    )
    assert len(results) == 1
    assert results[0].rule_id == "SEC004"


@pytest.mark.parametrize(
    "benign_metadata_var",
    [
        'password_reset_status = "pending"',
        'password_policy = "min_12_chars"',
        'api_key_header = "X-API-Key"',
        'access_token_type = "Bearer"',
    ],
)
def test_qa_005_sec003_does_not_flag_non_secret_metadata_variables(
    tmp_path: Path, context: LintContext, benign_metadata_var: str
) -> None:
    """QA-005: SEC003 should not flag non-secret metadata/policy/header
    configuration variables that happen to start with a secret prefix.
    """
    cb_file = _write_callback(
        tmp_path,
        "def before_model_callback(callback_context, llm_request):\n"
        f"    {benign_metadata_var}\n"
        "    return None\n",
    )
    assert (
        CallbackHardcodedSecretOrToken().check(
            cb_file, cb_file.read_text(), context
        )
        == []
    )
