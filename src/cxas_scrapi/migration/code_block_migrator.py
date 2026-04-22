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

"""Code Block Migrator for transforming legacy DFCX Python code blocks."""

import ast
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from cxas_scrapi.core.tools import Tools
from cxas_scrapi.migration.ai_augment import AIAugment

logger = logging.getLogger(__name__)


class ToolCallTransformer(ast.NodeTransformer):
    """AST Transformer to:

    1. Rewrite DFCX tool calls: tools.display_name.op(args) ->
    tools.toolset_op(args).json()
    2. Comment out system functions: respond(), add_override(),
    playbooks.PlaybookTransfer()
    3. Fix return types: -> None becomes -> dict, and ensures a return {}
    exists.
    """

    def __init__(
        self, tool_map: Dict[str, Any], tool_display_name_map: Dict[str, str]
    ):
        super().__init__()
        self.tool_map = tool_map
        self.tool_display_name_map = tool_display_name_map
        self.dependencies = (
            set()
        )  # Stores full resource names of referenced toolsets
        self.tool_display_name_map_lower = {
            display_name.lower(): tool_id
            for display_name, tool_id in tool_display_name_map.items()
        }

    def _get_comment_node(self, node, prefix=""):
        """Helper to create a string constant node for commented code."""
        try:
            original_code = ast.unparse(node)
            comment_text = (
                f"# MIGRATION_TODO [System Function]: {original_code}"
            )
        except Exception:
            comment_text = f"# MIGRATION_TODO [System Function]: {prefix}..."

        return ast.Expr(value=ast.Constant(value=comment_text))

    def _is_system_function(self, call_node):
        """Checks if a Call node represents a DFCX system function to be
        commented out.
        """
        if not isinstance(call_node, ast.Call):
            return False

        # Case 1: Direct calls like respond(), add_override()
        if isinstance(call_node.func, ast.Name):
            return call_node.func.id in ["respond", "add_override"]

        # Case 2: Attribute calls like playbooks.PlaybookTransfer()
        # OR agents.agentTransfer()
        if isinstance(call_node.func, ast.Attribute):
            if isinstance(call_node.func.value, ast.Name):
                module_name = call_node.func.value.id
                func_name = call_node.func.attr

                if (
                    module_name == "playbooks"
                    and func_name == "PlaybookTransfer"
                ):
                    return True
                if module_name == "agents" and func_name in [
                    "agentTransfer",
                    "AgentTransfer",
                ]:
                    return True

        return False

    def visit_Expr(self, node):
        # Handle standalone calls
        if self._is_system_function(node.value):
            return self._get_comment_node(node)
        return self.generic_visit(node)

    def visit_Return(self, node):
        # Handle returns: return respond(...) or return
        # playbooks.PlaybookTransfer(...)
        if node.value and self._is_system_function(node.value):
            return self._get_comment_node(node)
        return self.generic_visit(node)

    def visit_Call(self, node):
        # Check for structure: tools.A.B(...)
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "tools"
        ):
            dfcx_tool_display_name = node.func.value.attr
            op_name = node.func.attr

            dfcx_id = self.tool_display_name_map.get(dfcx_tool_display_name)
            if not dfcx_id:
                dfcx_id = self.tool_display_name_map_lower.get(
                    dfcx_tool_display_name.lower()
                )

            if dfcx_id and dfcx_id in self.tool_map:
                tool_info = self.tool_map[dfcx_id]

                if tool_info["type"] == "TOOLSET":
                    ps_resource_name = tool_info["name"]
                    ps_toolset_id = ps_resource_name.split("/")[-1]

                    self.dependencies.add(ps_resource_name)

                    new_attr_name = f"{ps_toolset_id}_{op_name}"

                    node.func = ast.Attribute(
                        value=ast.Name(id="tools", ctx=ast.Load()),
                        attr=new_attr_name,
                        ctx=ast.Load(),
                    )

                    new_node = ast.Call(
                        func=ast.Attribute(
                            value=node, attr="json", ctx=ast.Load()
                        ),
                        args=[],
                        keywords=[],
                    )

                    self.generic_visit(node)
                    return new_node
            else:
                logger.warning(
                    f"      - WARNING: Found code reference to "
                    f"'tools.{dfcx_tool_display_name}' "
                    f"but could not resolve it to a migrated Toolset."
                )

        return self.generic_visit(node)

    def visit_FunctionDef(self, node):
        # 1. Visit children FIRST
        self.generic_visit(node)

        # 2. Fix/Add Return Type Annotation
        should_set_dict = False

        if node.returns is None:
            should_set_dict = True
        else:
            is_none = False
            if hasattr(ast, "Constant") and isinstance(
                node.returns, ast.Constant
            ):
                is_none = node.returns.value is None
            elif hasattr(ast, "NameConstant") and isinstance(
                node.returns, ast.NameConstant
            ):
                is_none = node.returns.value is None

            if is_none:
                should_set_dict = True

        if should_set_dict:
            node.returns = ast.Name(id="dict", ctx=ast.Load())

            # Ensure it returns a dict at the end if it didn't have a return
            # or returned None
            last_stmt = node.body[-1] if node.body else None
            if not isinstance(last_stmt, ast.Return):
                node.body.append(ast.Return(value=ast.Dict(keys=[], values=[])))
            elif isinstance(last_stmt, ast.Return) and last_stmt.value is None:
                last_stmt.value = ast.Dict(keys=[], values=[])

        return node


class CodeBlockMigrator:
    """Handles the migration of DFCX Code Blocks to Polysynth components."""

    TYPING_MAP = {
        "Dict": "from typing import Dict",
        "List": "from typing import List",
        "Optional": "from typing import Optional",
        "Any": "from typing import Any",
        "Tuple": "from typing import Tuple",
        "Set": "from typing import Set",
        "Union": "from typing import Union",
    }

    def __init__(
        self, ps_tools_client: Tools, ai_augment_client: Optional[AIAugment]
    ):
        self.ps_tools = ps_tools_client
        self.ai_augment = ai_augment_client
        logger.info("CodeBlockMigrator initialized.")

    @staticmethod
    def _get_typing_imports_for_function(function_code: str) -> Set[str]:
        imports_needed = set()
        try:
            tree = ast.parse(function_code)
            func_node = tree.body[0]

            def find_type_names(annotation_node):
                names = set()
                if isinstance(annotation_node, ast.Name):
                    names.add(annotation_node.id)
                elif isinstance(annotation_node, ast.Subscript):
                    names.update(find_type_names(annotation_node.value))
                    slice_node = (
                        annotation_node.slice.value
                        if hasattr(annotation_node.slice, "value")
                        else annotation_node.slice
                    )
                    names.update(find_type_names(slice_node))
                elif isinstance(annotation_node, (ast.Tuple, ast.List)):
                    for element in annotation_node.elts:
                        names.update(find_type_names(element))
                return names

            if (
                isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and func_node.returns
            ):
                for name in find_type_names(func_node.returns):
                    if name in CodeBlockMigrator.TYPING_MAP:
                        imports_needed.add(CodeBlockMigrator.TYPING_MAP[name])

            for arg in func_node.args.args:
                if arg.annotation:
                    for name in find_type_names(arg.annotation):
                        if name in CodeBlockMigrator.TYPING_MAP:
                            imports_needed.add(
                                CodeBlockMigrator.TYPING_MAP[name]
                            )
        except (SyntaxError, IndexError):
            pass
        return imports_needed

    @staticmethod
    def _parse_code_block_with_ast(
        code_string: str,
    ) -> Tuple[Set[str], List[Tuple[str, str]]]:
        explicit_imports = set()
        extracted_functions = []
        try:
            tree = ast.parse(code_string)
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    explicit_imports.add(ast.unparse(node))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func_text = ast.unparse(node)
                    if func_text:
                        extracted_functions.append((node.name, func_text))
            return explicit_imports, extracted_functions
        except SyntaxError as e:
            logger.warning(
                f"  - WARNING: Could not parse code block due to a syntax "
                f"error: {e}"
            )
            return set(), []

    def _sanitize_resource_id(
        self, name: str, min_len: int = 5, max_len: int = 36
    ) -> str:
        """Sanitizes a string to be a valid Polysynth resource ID."""
        # Replace spaces and other invalid characters with underscores
        sanitized = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)

        # Ensure it starts with a letter (strip leading underscores/hyphens)
        sanitized = sanitized.lstrip("_-")

        # If it's empty or doesn't start with a letter, prepend 'tool_'
        if not sanitized or not re.match(r"^[a-zA-Z]", sanitized):
            sanitized = "tool_" + sanitized

        # Truncate to max length
        sanitized = sanitized[:max_len]

        # Pad to min length if necessary
        while len(sanitized) < min_len:
            sanitized += "_"
        return sanitized

    def extract_functions_to_ir(
        self,
        code: str,
        existing_tool_ids: Set[str],
        migrated_function_names: Set[str],
        function_name_to_tool_map: Dict[str, str],
        tool_map: Dict[str, Any],
        tool_display_name_map: Dict[str, str],
        target_app_resource_name: str,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, str], Set[str]]:
        """Extracts, transforms (AST), and compiles Python functions into IR
        tool payloads.

        Returns: (extracted_tools_list, action_to_tool_map, referenced_toolsets)
        """
        extracted_tools = []
        action_to_tool_map = {}
        referenced_toolsets = set()

        RESERVED_NAMES = {
            "transfer_to_agent",
            "tranferToAgent",
            "end_session",
            "customize_response",
        }

        shared_imports, extracted_functions = self._parse_code_block_with_ast(
            code
        )

        if not extracted_functions:
            return [], {}, set()

        for original_func_name, function_code in extracted_functions:
            target_func_name = original_func_name
            clean_name = original_func_name.lstrip("_-")
            if clean_name in RESERVED_NAMES:
                target_func_name = f"usr_{clean_name}"
                logger.debug(
                    f"    - Renaming reserved function '{original_func_name}' "
                    f"-> '{target_func_name}'"
                )

            # --- AST Transformation ---
            try:
                func_tree = ast.parse(function_code)
                if target_func_name != original_func_name:
                    func_tree.body[0].name = target_func_name

                transformer = ToolCallTransformer(
                    tool_map, tool_display_name_map
                )
                transformed_tree = transformer.visit(func_tree)
                ast.fix_missing_locations(transformed_tree)

                if hasattr(ast, "unparse"):
                    final_code = ast.unparse(transformed_tree)
                else:
                    final_code = function_code

                referenced_toolsets.update(transformer.dependencies)
            except Exception as e:
                logger.warning(
                    f"    - Warning: Failed to transform tool calls in "
                    f"'{original_func_name}': {e}"
                )

            # Check if already migrated
            if original_func_name in migrated_function_names:
                existing_tool_id = function_name_to_tool_map[original_func_name]
                action_to_tool_map[original_func_name] = existing_tool_id
                continue

            typing_imports = self._get_typing_imports_for_function(final_code)
            final_imports = shared_imports.union(typing_imports)
            imports_header = "\n".join(sorted(list(final_imports)))

            base_tool_id = self._sanitize_resource_id(target_func_name)
            final_tool_id = base_tool_id
            suffix_counter = 2
            while final_tool_id in existing_tool_ids:
                suffix = f"_{suffix_counter}"
                truncated_base = base_tool_id[: 36 - len(suffix)]
                final_tool_id = f"{truncated_base}{suffix}"
                suffix_counter += 1

            existing_tool_ids.add(final_tool_id)

            final_function_code = (
                f"{imports_header}\n\n{final_code}"
                if imports_header
                else final_code
            )

            # Create the IR Payload
            ps_tool_payload = {
                "name": final_tool_id,
                "displayName": target_func_name,
                "pythonFunction": {
                    "name": target_func_name,
                    "python_code": final_function_code,
                },
            }

            extracted_tools.append(
                {
                    "type": "PYTHON",
                    "id": final_tool_id,
                    "name": f"{target_app_resource_name}/tools/{final_tool_id}",
                    "payload": ps_tool_payload,
                }
            )

            action_to_tool_map[original_func_name] = final_tool_id
            migrated_function_names.add(original_func_name)
            function_name_to_tool_map[original_func_name] = final_tool_id

        return extracted_tools, action_to_tool_map, referenced_toolsets
