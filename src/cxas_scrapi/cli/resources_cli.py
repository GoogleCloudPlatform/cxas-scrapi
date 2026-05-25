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

"""Argparse subcommand handlers and parser registration for GECX resources."""

import argparse
import sys

from google.cloud.ces_v1beta import types

from cxas_scrapi.core.agents import Agents
from cxas_scrapi.core.callbacks import Callbacks
from cxas_scrapi.core.common import Common
from cxas_scrapi.core.tools import Tools
from cxas_scrapi.core.variables import Variables


def tools_list(args: argparse.Namespace) -> None:
    """Lists both tools and toolsets within a specific app."""
    print(f"Listing tools and toolsets for App: {args.app_name}")

    app_name = Common._get_app_name(args.app_name)
    if not app_name:
        print(
            "Error: Invalid App Name format. Please use the full resource "
            "name in the format 'projects/.../locations/.../apps/...'"
        )
        sys.exit(1)

    try:
        tools_client = Tools(app_name=app_name)
        tools = tools_client.list_tools()
        if not tools:
            print("No tools or toolsets found.")
            return

        # Format output using pandas if available
        try:
            import pandas as pd  # noqa: PLC0415

            data = []
            for t in tools:
                is_tset = "/toolsets/" in t.name
                t_type = "Toolset" if is_tset else "Tool"
                data.append(
                    {
                        "Display Name": t.display_name,
                        "Name": t.name,
                        "Type": t_type,
                    }
                )
            df = pd.DataFrame(data)
            print(df.to_string(index=False))
        except ImportError:
            for t in tools:
                is_tset = "/toolsets/" in t.name
                t_type = "Toolset" if is_tset else "Tool"
                print(f"- {t.display_name} ({t.name}) [{t_type}]")
    except Exception as e:
        print(f"Failed to list tools: {e}")
        sys.exit(1)


def tools_delete(args: argparse.Namespace) -> None:
    """Deletes a specific tool or toolset."""
    app_name = Common._get_app_name(args.app_name)
    if not app_name:
        print(
            "Error: Invalid App Name format. Please use the full resource "
            "name in the format 'projects/.../locations/.../apps/...'"
        )
        sys.exit(1)

    print(f"Deleting tool/toolset '{args.name}' from App: {app_name}")

    try:
        tools_client = Tools(app_name=app_name)
        resolved_name = None

        # 1. Check if args.name is a full resource name
        if "/tools/" in args.name or "/toolsets/" in args.name:
            resolved_name = args.name
        else:
            # 2. Map display name to resource name using reverse map
            tools_map = tools_client.get_tools_map(reverse=True)
            if args.name in tools_map:
                resolved_name = tools_map[args.name]
            else:
                # 3. Fallback: check trailing ID
                tools_list_res = tools_client.list_tools()
                for t in tools_list_res:
                    if t.name.split("/")[-1] == args.name:
                        resolved_name = t.name
                        break

        if not resolved_name:
            print(f"Error: Tool/Toolset '{args.name}' not found in app.")
            sys.exit(1)

        tools_client.delete_tool(resolved_name)
        print(f"Successfully deleted: {resolved_name}")
    except Exception as e:
        print(f"Failed to delete tool/toolset: {e}")
        sys.exit(1)


def callbacks_list(args: argparse.Namespace) -> None:
    """Lists callbacks attached to agents."""
    app_name = Common._get_app_name(args.app_name)
    if not app_name:
        print(
            "Error: Invalid App Name format. Please use the full resource "
            "name in the format 'projects/.../locations/.../apps/...'"
        )
        sys.exit(1)

    try:
        agents_client = Agents(app_name=app_name)
        callbacks_client = Callbacks(app_name=app_name)

        # Resolve agent_id if provided
        target_agents = []
        if getattr(args, "agent_name", None):
            # Check if args.agent_name is a full resource name
            if "/agents/" in args.agent_name:
                target_agents = [agents_client.get_agent(args.agent_name)]
            else:
                # Map display name to resource name
                agents_map = agents_client.get_agents_map(reverse=True)
                if args.agent_name in agents_map:
                    agent_res_name = agents_map[args.agent_name]
                    target_agents = [agents_client.get_agent(agent_res_name)]
                else:
                    # Fallback: check trailing ID
                    agents_list_res = agents_client.list_agents()
                    found = False
                    for a in agents_list_res:
                        if a.name.split("/")[-1] == args.agent_name:
                            target_agents = [a]
                            found = True
                            break
                    if not found:
                        print(f"Error: Agent '{args.agent_name}' not found.")
                        sys.exit(1)
        else:
            target_agents = agents_client.list_agents()

        if not target_agents:
            print("No agents found.")
            return

        for agent in target_agents:
            agent_id = agent.name.split("/")[-1]
            print(f"\nAgent: {agent.display_name} ({agent_id})")
            print("-" * 40)
            callbacks_dict = callbacks_client.list_callbacks(agent.name)
            has_callbacks = False
            for cb_field, cb_list in callbacks_dict.items():
                if cb_list:
                    has_callbacks = True
                    # Convert long field name to shorthand callback_type
                    cb_type = cb_field.replace("_callbacks", "")
                    print(f"  {cb_type.upper()}:")
                    for idx, cb in enumerate(cb_list):
                        desc = getattr(cb, "description", "")
                        desc_str = f" - {desc}" if desc else ""
                        preview = (cb.python_code.strip().split("\n")[0])[:60]
                        print(f"    [{idx}] {preview}...{desc_str}")
            if not has_callbacks:
                print("  (No callbacks configured)")
    except Exception as e:
        print(f"Failed to list callbacks: {e}")
        sys.exit(1)


def callbacks_delete(args: argparse.Namespace) -> None:
    """Deletes a callback from an agent by index."""
    app_name = Common._get_app_name(args.app_name)
    if not app_name:
        print(
            "Error: Invalid App Name format. Please use the full resource "
            "name in the format 'projects/.../locations/.../apps/...'"
        )
        sys.exit(1)

    try:
        agents_client = Agents(app_name=app_name)
        callbacks_client = Callbacks(app_name=app_name)

        # Resolve agent name/ID to full resource name
        resolved_agent_name = None
        if "/agents/" in args.agent_name:
            resolved_agent_name = args.agent_name
        else:
            agents_map = agents_client.get_agents_map(reverse=True)
            if args.agent_name in agents_map:
                resolved_agent_name = agents_map[args.agent_name]
            else:
                # Fallback: check trailing ID
                agents_list_res = agents_client.list_agents()
                for a in agents_list_res:
                    if a.name.split("/")[-1] == args.agent_name:
                        resolved_agent_name = a.name
                        break

        if not resolved_agent_name:
            print(f"Error: Agent '{args.agent_name}' not found.")
            sys.exit(1)

        agent_id = resolved_agent_name.split("/")[-1]
        print(
            f"Deleting callback of type '{args.callback_type}' "
            f"at index {args.index} from Agent: {agent_id}"
        )
        callbacks_client.delete_callback(
            agent_id=resolved_agent_name,
            callback_type=args.callback_type,
            index=args.index,
        )
        print("Successfully deleted callback.")
    except Exception as e:
        print(f"Failed to delete callback: {e}")
        sys.exit(1)


def variables_list(args: argparse.Namespace) -> None:
    """Lists variable declarations in an app."""
    app_name = Common._get_app_name(args.app_name)
    if not app_name:
        print(
            "Error: Invalid App Name format. Please use the full resource "
            "name in the format 'projects/.../locations/.../apps/...'"
        )
        sys.exit(1)

    print(f"Listing variable declarations for App: {app_name}")

    try:
        variables_client = Variables(app_name=app_name)
        variables = variables_client.list_variables()
        if not variables:
            print("No variable declarations found.")
            return

        try:
            import pandas as pd  # noqa: PLC0415

            data = []
            for v in variables:
                schema = getattr(v, "schema", None)
                try:
                    decl_schema = types.App.VariableDeclaration.Schema
                    v_type = decl_schema.Type(schema.type_).name
                except Exception:
                    v_type = getattr(schema, "type_", "UNKNOWN")
                    if hasattr(v_type, "name"):
                        v_type = v_type.name
                    else:
                        v_type = str(v_type)

                default_val = variables_client.variable_to_dict(v)
                data.append(
                    {
                        "Name": v.name,
                        "Type": v_type,
                        "Default Value": default_val,
                    }
                )
            df = pd.DataFrame(data)
            print(df.to_string(index=False))
        except ImportError:
            for v in variables:
                schema = getattr(v, "schema", None)
                try:
                    decl_schema = types.App.VariableDeclaration.Schema
                    v_type = decl_schema.Type(schema.type_).name
                except Exception:
                    v_type = getattr(schema, "type_", "UNKNOWN")
                    if hasattr(v_type, "name"):
                        v_type = v_type.name
                    else:
                        v_type = str(v_type)

                default_val = variables_client.variable_to_dict(v)
                print(f"- {v.name} [{v_type}] (Default: {default_val})")
    except Exception as e:
        print(f"Failed to list variables: {e}")
        sys.exit(1)


def variables_delete(args: argparse.Namespace) -> None:
    """Deletes a variable declaration from the app."""
    app_name = Common._get_app_name(args.app_name)
    if not app_name:
        print(
            "Error: Invalid App Name format. Please use the full resource "
            "name in the format 'projects/.../locations/.../apps/...'"
        )
        sys.exit(1)

    print(f"Deleting variable '{args.name}' from App: {app_name}")

    try:
        variables_client = Variables(app_name=app_name)
        variables_client.delete_variable(args.name)
        print(f"Successfully deleted variable declaration: {args.name}")
    except Exception as e:
        print(f"Failed to delete variable: {e}")
        sys.exit(1)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Registers tools, callbacks, and variables subparsers."""
    # Subparsers for 'tools'
    parser_tools = subparsers.add_parser(
        "tools", help="Manage tools (list, delete)."
    )
    tools_subparsers = parser_tools.add_subparsers(
        title="Tools Commands", dest="tools_command", required=True
    )

    parser_tools_list = tools_subparsers.add_parser(
        "list", help="List all tools and toolsets in an app."
    )
    parser_tools_list.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_tools_list.set_defaults(func=tools_list)

    parser_tools_delete = tools_subparsers.add_parser(
        "delete", help="Delete a specific tool or toolset."
    )
    parser_tools_delete.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_tools_delete.add_argument(
        "--name",
        required=True,
        help=(
            "The tool/toolset display name, resource ID, "
            "or full resource name to delete."
        ),
    )
    parser_tools_delete.set_defaults(func=tools_delete)

    # Subparsers for 'callbacks'
    parser_callbacks = subparsers.add_parser(
        "callbacks", help="Manage agent callbacks (list, delete)."
    )
    callbacks_subparsers = parser_callbacks.add_subparsers(
        title="Callbacks Commands", dest="callbacks_command", required=True
    )

    parser_callbacks_list = callbacks_subparsers.add_parser(
        "list", help="List callbacks for agents in an app."
    )
    parser_callbacks_list.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_callbacks_list.add_argument(
        "--agent-name",
        help="Optional agent ID or display name to filter callbacks.",
    )
    parser_callbacks_list.set_defaults(func=callbacks_list)

    parser_callbacks_delete = callbacks_subparsers.add_parser(
        "delete",
        help="Delete a specific callback from an agent by index.",
    )
    parser_callbacks_delete.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_callbacks_delete.add_argument(
        "--agent-name",
        required=True,
        help=(
            "The Agent ID or display name from which to delete the callback."
        ),
    )
    parser_callbacks_delete.add_argument(
        "--callback-type",
        required=True,
        choices=[
            "before_model",
            "after_model",
            "before_tool",
            "after_tool",
            "before_agent",
            "after_agent",
        ],
        help="The type of the callback to delete.",
    )
    parser_callbacks_delete.add_argument(
        "--index",
        required=True,
        type=int,
        help="The 0-based index of the callback to delete.",
    )
    parser_callbacks_delete.set_defaults(func=callbacks_delete)

    # Subparsers for 'variables'
    parser_variables = subparsers.add_parser(
        "variables", help="Manage variables (list, delete)."
    )
    variables_subparsers = parser_variables.add_subparsers(
        title="Variables Commands", dest="variables_command", required=True
    )

    parser_variables_list = variables_subparsers.add_parser(
        "list", help="List variables in an app."
    )
    parser_variables_list.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_variables_list.set_defaults(func=variables_list)

    parser_variables_delete = variables_subparsers.add_parser(
        "delete", help="Delete a specific variable declaration."
    )
    parser_variables_delete.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_variables_delete.add_argument(
        "--name",
        required=True,
        help="The name of the variable to delete.",
    )
    parser_variables_delete.set_defaults(func=variables_delete)
