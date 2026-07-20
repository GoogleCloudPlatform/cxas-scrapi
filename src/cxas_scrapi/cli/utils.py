from __future__ import annotations

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

"""Utility functions for CLI commands."""

import importlib
import inspect
import sys
from pathlib import Path
from dataclasses import fields
from typing import Any, TypeVar

import click

T = TypeVar("T")


def LazyCallable(module_path: str, func_name: str) -> Any:
    """Lazy callable proxy factory that defers module import until execution or attribute access.

    Supports dynamic attribute delegation for static methods, class methods, and class
    attributes while remaining 100% compatible with unittest.mock, autospec, and pytest.

    Args:
        module_path: Dot-separated Python module path.
        func_name: Target function or class name within the module.

    Returns:
        A dynamic proxy type for the targeted function or class.
    """

    def _resolve() -> Any:
        module = importlib.import_module(module_path)
        return getattr(module, func_name)

    class _LazyProxyMeta(type):
        def __call__(cls, *args: Any, **kwargs: Any) -> Any:
            return _resolve()(*args, **kwargs)

        def __getattr__(cls, name: str) -> Any:
            target = _resolve()
            if hasattr(target, "__dict__"):
                raw = target.__dict__.get(name)
                if isinstance(raw, staticmethod):
                    return raw.__get__(None, target)
            attr = getattr(target, name)
            if name == "__name__" and not isinstance(attr, str):
                return str(attr)
            return attr

        def __setattr__(cls, name: str, value: Any) -> None:
            if inspect.isfunction(value):
                value = staticmethod(value)
            setattr(_resolve(), name, value)

        def __delattr__(cls, name: str) -> None:
            delattr(_resolve(), name)

        def __dir__(cls) -> list[str]:
            return dir(_resolve())

    return _LazyProxyMeta(func_name, (), {})


def to_dataclass(
    cls: type[T],
    ctx: click.Context | dict[str, Any] | Any | None = None,
    **kwargs: Any,
) -> T:
    """Translates Click context, dict, or existing objects/namespaces into a target dataclass.

    Supports dual-mode execution for both Click CLI wrappers and direct unit tests
    passing namespace objects or existing config instances.

    Args:
        cls: Target dataclass type to instantiate.
        ctx: Optional Click context, dictionary, namespace, or instance of cls.
        kwargs: Keyword arguments passed from Click command wrapper.

    Returns:
        Instance of T populated with sanitized arguments.
    """
    if isinstance(ctx, cls):
        if not kwargs:
            return ctx
        raw_dict = {f.name: getattr(ctx, f.name) for f in fields(cls)}
        raw_dict.update(kwargs)
    elif isinstance(ctx, click.Context):
        raw_dict = dict(kwargs)
        curr = ctx
        root = ctx.find_root()
        while curr and curr != root:
            if curr.info_name and curr.parent and curr.parent != root:
                key = f"{curr.parent.info_name.replace('-', '_')}_command"
                raw_dict[key] = curr.info_name
            curr = curr.parent
        if "command" not in raw_dict and ctx.info_name:
            raw_dict["command"] = ctx.info_name
    elif isinstance(ctx, dict):
        raw_dict = dict(ctx)
        raw_dict.update(kwargs)
    elif ctx is not None and hasattr(ctx, "__dict__"):
        raw_dict = vars(ctx).copy()
        raw_dict.update(kwargs)
    else:
        raw_dict = dict(kwargs)

    for k, v in list(raw_dict.items()):
        if isinstance(v, tuple):
            raw_dict[k] = list(v)

    field_names = {f.name for f in fields(cls)}

    if cls.__name__ not in ("WorkspaceSetConfig", "CreateLocalConfig"):
        try:
            from cxas_scrapi.core import workspace as ws
            config = ws.load_workspace_config()
            if config:
                if "app_name" in field_names and raw_dict.get("app_name") is None:
                    try:
                        raw_dict["app_name"] = ws.app_name()
                    except Exception:
                        pass
                if "app_resource_name" in field_names and raw_dict.get("app_resource_name") is None:
                    try:
                        raw_dict["app_resource_name"] = ws.app_name()
                    except Exception:
                        pass
                if "app_dir" in field_names and raw_dict.get("app_dir") in (None, "."):
                    raw_dict["app_dir"] = config.get("app_dir", raw_dict.get("app_dir", "."))
                if "agent_dir" in field_names and raw_dict.get("agent_dir") is None:
                    app_dir_val = config.get("app_dir", "app")
                    try:
                        app_dir_path = Path(ws.resolve_project_dir()) / app_dir_val
                        agents_dir = app_dir_path / "agents"
                        if agents_dir.exists():
                            agent_subdirs = [
                                d for d in agents_dir.iterdir()
                                if d.is_dir() and (d / "instruction.txt").exists()
                            ]
                            if len(agent_subdirs) == 1:
                                raw_dict["agent_dir"] = str(agent_subdirs[0])
                            elif not agent_subdirs and (app_dir_path / "instruction.txt").exists():
                                raw_dict["agent_dir"] = str(app_dir_path)
                    except Exception:
                        pass
                if "evals_dir" in field_names and raw_dict.get("evals_dir") is None:
                    raw_dict["evals_dir"] = config.get("evals_dir", "evals")
                if "input_dir" in field_names and raw_dict.get("input_dir") is None:
                    raw_dict["input_dir"] = config.get("evals_dir", "evals")
                if "output_dir" in field_names and raw_dict.get("output_dir") is None:
                    raw_dict["output_dir"] = config.get("output_dir", ".scrapi-out")
                if "model" in field_names and raw_dict.get("model") is None:
                    raw_dict["model"] = config.get("model", "gemini-2.5-flash")
                if "modality" in field_names and raw_dict.get("modality") is None:
                    raw_dict["modality"] = config.get("modality", "AUDIO")
        except Exception:
            pass

    if isinstance(ctx, click.Context) and cls.__name__ not in ("WorkspaceSetConfig", "CreateLocalConfig"):
        try:
            from cxas_scrapi.core import workspace as ws
            config = ws.load_workspace_config()
            if config and not raw_dict.get("json_output") and raw_dict.get("format", "") not in ("json", "csv"):
                profile = "default"
                workspace_root = ws.find_workspace_root()
                if workspace_root:
                    active_project_file = Path(workspace_root) / ".scrapi" / "active-project"
                    if active_project_file.is_file():
                        import toml
                        with active_project_file.open("r", encoding="utf-8") as f:
                            profile = toml.load(f).get("active-profile") or "default"
                app_name_str = raw_dict.get("app_name") or raw_dict.get("app_resource_name")
                if not app_name_str:
                    try:
                        app_name_str = ws.app_name()
                    except Exception:
                        app_name_str = "N/A"
                project_dir_str = config.get("_project_dir", "")
                app_dir_str = raw_dict.get("app_dir") or config.get("app_dir", "app")
                evals_dir_str = raw_dict.get("evals_dir") or raw_dict.get("input_dir") or config.get("evals_dir", "evals")

                skip_keys = {
                    "app_name", "app_resource_name", "app_dir", "agent_dir", "evals_dir", "input_dir", "output_dir",
                    "project_dir", "json_output", "format", "oauth_token", "target_dir", "command",
                }
                cmd_args = {
                    k: v for k, v in raw_dict.items()
                    if k not in skip_keys and not k.endswith("_command") and not k.startswith("_") and v is not None and v != [] and v != {}
                }
                from rich.console import Console
                console = Console(file=sys.stderr)
                console.print(
                    f"[bold cyan][CXAS Workspace][/bold cyan] Profile: [yellow]{profile}[/yellow] | App: [green]{app_name_str}[/green]"
                )
                console.print(
                    f"[bold cyan][CXAS Workspace][/bold cyan] Project Dir: [dim]{project_dir_str}[/dim] | App Dir: [dim]{app_dir_str}[/dim] | Evals: [dim]{evals_dir_str}[/dim]"
                )
                if cmd_args:
                    formatted_args = " | ".join(
                        f"[bold]{k}[/bold]=[cyan]{repr(v)}[/cyan]" if isinstance(v, str) else f"[bold]{k}[/bold]=[magenta]{v}[/magenta]"
                        for k, v in cmd_args.items()
                    )
                    console.print(f"[bold cyan][CXAS Profile][/bold cyan] Args passed: {formatted_args}")
                console.print("-" * 80)
        except Exception:
            pass

    filtered_kwargs = {k: v for k, v in raw_dict.items() if k in field_names}
    return cls(**filtered_kwargs)


@click.command(name="help", context_settings=dict(ignore_unknown_options=True))
@click.argument("help_command", nargs=-1)
@click.pass_context
def help_cmd(ctx: click.Context, help_command: tuple[str, ...]) -> None:
    """Inspect help documentation for any command or sub-command.

    Args:
        ctx: Click context.
        help_command: Subcommand tokens to query help for.

    Returns:
        None
    """
    root = ctx.find_root()
    clean_tokens = [p for p in help_command if not p.startswith("-")]
    if not clean_tokens:
        click.echo(root.get_help())
        return

    current: click.Command = root.command
    parent_ctx = root
    for part in clean_tokens:
        if isinstance(current, click.Group):
            cmd = current.get_command(parent_ctx, part)
            if cmd is None:
                click.echo(f"Error: No such command '{part}'.")
                return
            current = cmd
            parent_ctx = click.Context(
                current, info_name=part, parent=parent_ctx
            )
        else:
            click.echo(f"Error: Command '{current.name}' has no subcommands.")
            return
    click.echo(current.get_help(parent_ctx))
