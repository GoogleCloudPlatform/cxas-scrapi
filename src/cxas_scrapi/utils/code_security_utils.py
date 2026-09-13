"""Security utilities for safe AST validation and execution of callback code."""

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

import ast
from typing import Any

FORBIDDEN_CALL_NAMES = frozenset(
    {
        "__import__",
        "compile",
        "eval",
        "exec",
        "globals",
        "input",
        "locals",
        "open",
    }
)

SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "None": None,
    "True": True,
    "False": False,
}


class _CallbackSecurityVisitor(ast.NodeVisitor):
    """AST visitor that enforces safety constraints on callback code."""

    def visit_Import(self, node: ast.Import) -> None:
        raise ValueError(
            f"Import statements are forbidden in localized callbacks "
            f"(line {node.lineno})."
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        raise ValueError(
            f"'from ... import' statements are forbidden in localized "
            f"callbacks (line {node.lineno})."
        )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Block dunder introspection attacks (__class__, __subclasses__, etc.)
        if node.attr.startswith("__") and node.attr.endswith("__"):
            raise ValueError(
                f"Access to private/dunder attribute '{node.attr}' is "
                f"forbidden (line {node.lineno})."
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Block direct calls to dangerous builtins (eval, exec, open, etc.)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in FORBIDDEN_CALL_NAMES
        ):
            raise ValueError(
                f"Call to forbidden builtin function '{node.func.id}' "
                f"(line {node.lineno})."
            )
        self.generic_visit(node)


class CodeSecurityUtils:
    """Utilities for safely validating and executing untrusted callback code."""

    @staticmethod
    def validate_callback_ast(code_str: str) -> None:
        """Parses and validates callback code against an AST safety policy.

        Raises:
            ValueError: If the AST contains forbidden syntax (imports, dunder
                attributes, or dangerous builtins).
            SyntaxError: If the code is not valid Python syntax.
        """
        tree = ast.parse(code_str)
        visitor = _CallbackSecurityVisitor()
        visitor.visit(tree)

    @staticmethod
    def get_safe_builtins() -> dict[str, Any]:
        """Returns a restricted dictionary of safe Python builtins."""
        return dict(SAFE_BUILTINS)
