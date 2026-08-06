"""Tests for code security and AST evaluation utilities."""

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

import re

import pytest

from cxas_scrapi.utils.code_security_utils import CodeSecurityUtils


def test_validate_callback_ast_valid_session_transformations() -> None:
    """Verifies that legitimate callback logic passes AST validation."""
    code = """
def beforeModelCallback(session):
    user_query = session.get("user_query", "").strip()
    session["normalized_query"] = user_query.lower()
    session["token_count"] = len(user_query.split())
    session["is_valid"] = bool(user_query)

    tags = []
    for word in user_query.split():
        if len(word) > 3:
            tags.append(word.upper())
    session["tags"] = tags
    return session
"""
    # Should not raise
    CodeSecurityUtils.validate_callback_ast(code)


def test_validate_callback_ast_import_rejected() -> None:
    """Verifies that import statements are strictly rejected."""
    code = """
def callback(session):
    import os
    os.system("echo compromised")
    return session
"""
    with pytest.raises(
        ValueError,
        match="Import statements are forbidden in localized callbacks",
    ):
        CodeSecurityUtils.validate_callback_ast(code)


def test_validate_callback_ast_import_from_rejected() -> None:
    """Verifies that 'from ... import' statements are strictly rejected."""
    code = """
def callback(session):
    from subprocess import Popen
    return session
"""
    pattern = re.escape(
        "'from ... import' statements are forbidden in localized callbacks"
    )
    with pytest.raises(ValueError, match=pattern):
        CodeSecurityUtils.validate_callback_ast(code)


def test_validate_callback_ast_dunder_attribute_rejected() -> None:
    """Verifies that dunder attribute access is strictly rejected."""
    code = """
def callback(session):
    subclasses = ().__class__.__base__.__subclasses__()
    return session
"""
    with pytest.raises(
        ValueError,
        match="Access to private/dunder attribute",
    ):
        CodeSecurityUtils.validate_callback_ast(code)


def test_validate_callback_ast_forbidden_builtins_rejected() -> None:
    """Verifies that direct calls to forbidden builtins are rejected."""
    forbidden_snippets = [
        "def cb(s):\n    open('/tmp/test', 'w')\n    return s",
        "def cb(s):\n    eval('1 + 1')\n    return s",
        "def cb(s):\n    exec('x = 1')\n    return s",
        "def cb(s):\n    compile('x = 1', '', 'exec')\n    return s",
        "def cb(s):\n    globals()['foo'] = 1\n    return s",
        "def cb(s):\n    locals()['foo'] = 1\n    return s",
        "def cb(s):\n    __import__('os')\n    return s",
    ]
    for snippet in forbidden_snippets:
        with pytest.raises(
            ValueError, match="Call to forbidden builtin function"
        ):
            CodeSecurityUtils.validate_callback_ast(snippet)


def test_validate_callback_ast_syntax_error() -> None:
    """Verifies that invalid Python code raises a SyntaxError."""
    code = "def broken_syntax(:"
    with pytest.raises(SyntaxError):
        CodeSecurityUtils.validate_callback_ast(code)


def test_get_safe_builtins_isolation() -> None:
    """Verifies that safe builtins contain standard helpers without danger."""
    safe = CodeSecurityUtils.get_safe_builtins()

    assert "len" in safe
    assert "int" in safe
    assert "str" in safe
    assert "dict" in safe
    assert "list" in safe
    assert "isinstance" in safe

    assert "open" not in safe
    assert "eval" not in safe
    assert "exec" not in safe
    assert "compile" not in safe
    assert "__import__" not in safe
