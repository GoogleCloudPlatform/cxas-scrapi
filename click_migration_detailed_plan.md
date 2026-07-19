# Stage 2 Low-Level Implementation Plan: Click CLI for cxas-scrapi (Iteration 5 - Final Unanimous Complete)

## 1. Overview & File-by-File Scope
This low-level plan details the exact modifications, decorator implementations, protocol schemas, `LazyRootGroup` multi-command architecture with `format_commands` override, and test updates required to transition `cxas-scrapi` from `argparse` to `click>=8.1.0` while maintaining 100% test pass rates (`150` tests across 11 files) and zero CLI breaking changes.

### Files Modified:
- `src/cxas_scrapi/cli/utils.py`
- `src/cxas_scrapi/cli/main.py`
- `src/cxas_scrapi/cli/evals.py`
- `src/cxas_scrapi/cli/deployments.py`
- `src/cxas_scrapi/cli/conversations.py`
- `src/cxas_scrapi/cli/sessions.py`
- `src/cxas_scrapi/cli/migration_cli.py`
- `src/cxas_scrapi/cli/trace_cli.py`
- `src/cxas_scrapi/cli/insights_cli.py`
- `src/cxas_scrapi/cli/resources_cli.py`
- `src/cxas_scrapi/cli/create_local.py`
- `src/cxas_scrapi/cli/app.py`
- `src/cxas_scrapi/cli/llm_lint.py`
- `src/cxas_scrapi/cli/versions_cli.py`
- `tests/cxas_scrapi/cli/test_main.py`
- `tests/cxas_scrapi/cli/test_resources_cli.py`
- `tests/cxas_scrapi/cli/test_insights_cli.py`
- `tests/cxas_scrapi/cli/test_trace_cli.py`

---

## 2. Shared Utilities (`src/cxas_scrapi/cli/utils.py`)

### 2.1 Generalized Subparser Ancestry Traversal (`to_namespace`) & Option Decorators
```python
from types import SimpleNamespace
from typing import Any, Callable
import click

def to_namespace(ctx: click.Context, **kwargs: Any) -> SimpleNamespace:
    """Merges root parameters with kwargs, sets subparser routing flags via ancestry traversal, and normalizes trailing underscores.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments for the subcommand.
    Returns:
        SimpleNamespace: Populated namespace object with root fallbacks and subparser routing attributes.
    """
    root = ctx.find_root()
    root_params = {"no_input": False, "oauth_token": None} | (root.obj or {})
    merged = root_params | kwargs
    clean_kwargs: dict[str, Any] = {}
    for key, val in merged.items():
        clean_key = key[:-1] if key.endswith("_") and len(key) > 1 else key
        clean_kwargs[clean_key] = val
    
    # Generalized subparser routing injection for arbitrary nesting depths against root context
    curr = ctx
    while curr and curr.parent and curr.parent != root:
        parent_name = curr.parent.info_name.replace('-', '_')
        clean_kwargs[f"{parent_name}_command"] = curr.info_name
        if curr.parent.info_name == "create" and curr.parent.parent and curr.parent.parent.info_name == "local":
            clean_kwargs["create_local_command"] = curr.info_name
        curr = curr.parent
    if curr and curr != root:
        clean_kwargs["command"] = curr.info_name

    if "tool_name" in clean_kwargs and "name" not in clean_kwargs:
        clean_kwargs["name"] = clean_kwargs.pop("tool_name")
    if "callback_name" in clean_kwargs and "name" not in clean_kwargs:
        clean_kwargs["name"] = clean_kwargs.pop("callback_name")
    if "variable_name" in clean_kwargs and "name" not in clean_kwargs:
        clean_kwargs["name"] = clean_kwargs.pop("variable_name")
        
    return SimpleNamespace(**clean_kwargs)

def project_location_options(required: bool = True) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Parameterized decorator adding project-id and location flags to commands.

    Args:
        required: Whether the project and location flags are required.
    Returns:
        Callable: The decorated Click command function.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func = click.option("--project-id", "-p", envvar="GCP_PROJECT_ID", required=required, help="GCP project ID.")(func)
        func = click.option("--location", "-l", envvar="GCP_REGION", required=required, help="GCP location.")(func)
        return func
    return decorator

def app_name_option(required: bool = True) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Parameterized decorator adding app-name flag to commands.

    Args:
        required: Whether the app-name flag is required.
    Returns:
        Callable: The decorated Click command function.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return click.option("--app-name", "-a", envvar="GECX_APP_NAME", required=required, help="App resource name.")(func)
    return decorator
```

### 2.2 `help_cmd` Implementation
```python
@click.command(name="help", context_settings=dict(ignore_unknown_options=True))
@click.argument("help_command", nargs=-1)
@click.pass_context
def help_cmd(ctx: click.Context, help_command: tuple[str, ...]) -> None:
    """Inspect help documentation for any command or sub-command.

    Args:
        ctx: Click execution context.
        help_command: Tuple of subcommand path tokens and optional flags.
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
        if isinstance(current, click.MultiCommand):
            cmd = current.get_command(parent_ctx, part)
            if cmd is None:
                click.echo(f"Error: No such command '{part}'.")
                return
            current = cmd
            parent_ctx = click.Context(current, info_name=part, parent=parent_ctx)
        else:
            click.echo(f"Error: Command '{current.name}' has no subcommands.")
            return
    click.echo(current.get_help(parent_ctx))
```

---

## 3. Command Module Implementations

### 3.1 `evals.py` (`evals` Group & Top-Level Evaluation Commands)
```python
from types import SimpleNamespace
from typing import Any, Protocol
import click
from cxas_scrapi.cli.utils import to_namespace, project_location_options, app_name_option

@click.group(name="evals")
def evals_group() -> None:
    """Manage and report on evaluations.

    Returns:
        None
    """

@evals_group.command(name="report")
@project_location_options(required=False)
@app_name_option(required=False)
@click.option("--output", "-o", help="Output file path.")
@click.option("--output-dir", required=True, help="Output directory for reports.")
@click.option("--golden-run", help="Golden run ID or resource name.")
@click.option("--run", is_flag=True, help="Run evaluations before generating report.")
@click.option("--app-dir", default=".", help="Path to local app directory.")
@click.option("--input-dir", help="Input directory containing turn evals.")
@click.option("--tool-test-file", help="Path to tool test JSON/YAML.")
@click.option("--goldens-dir", help="Path to golden dataset directory.")
@click.option("--simulation-dir", help="Path to simulation dataset directory.")
@click.option("--gcs-path", help="GCS bucket path to export reports.")
@click.option("--runs", multiple=True, help="Multiple run IDs to include.")
@click.option("--sim-parallel", type=int, default=1, help="Number of parallel simulation threads.")
@click.option("--modality", "-m", default="text", help="Modality for evaluation.")
@click.option("--sim-user-model", help="Gemini model string for simulated user.")
@click.option("--eval-model", help="Gemini model string for evaluation grading.")
@click.option("--include", multiple=True, help="Filter tags to include.")
@click.option("--filter-files", multiple=True, help="Filter specific eval files.")
@click.option("--filter-tags", multiple=True, help="Filter specific tags.")
@click.option("--golden-timeout", type=int, default=60, help="Timeout per turn in seconds.")
@click.option("--bg-noise-file", help="Background audio noise file path.")
@click.option("--burst-noise-files", multiple=True, help="Burst audio noise file paths.")
@click.option("--use-tool-fakes", is_flag=True, help="Use tool fakes during simulation.")
@click.option("--expectations-only", is_flag=True, help="Run expectation grading only.")
@click.option("--filter-names", multiple=True, help="Filter by specific evaluation names.")
@click.option("--json-progress", is_flag=True, help="Emit JSON progress events.")
@click.option("--deployment-id", help="Specific deployment ID.")
@click.option("--capture-agent-audio", is_flag=True, help="Capture agent audio during simulation.")
@click.option("--timestamped", is_flag=True, help="Add timestamp to report folder.")
@click.pass_context
def evals_report_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Generate combined evaluation report.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    combined_evals_report(args)

@click.command(name="run")
@project_location_options(required=False)
@app_name_option(required=True)
@click.option("--modality", "-m", type=click.Choice(["text", "audio"]), default="text", help="Modality for evaluation.")
@click.option("--evaluation-id", help="Specific evaluation resource ID.")
@click.option("--display-name-prefix", help="Display name prefix filter.")
@click.option("--tags", multiple=True, help="Tags to include.")
@click.option("--wait", is_flag=True, help="Wait for completion.")
@click.option("--filter-auto-metrics", is_flag=True, help="Filter auto metrics.")
@click.option("--golden-run-method", type=click.Choice(["STABLE", "NAIVE"]), default="STABLE", help="Golden run method.")
@click.pass_context
def run_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Run simulation evaluations.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    run_eval(args)
```

### 3.2 `app.py`, `llm_lint.py`, `create_local.py`, `conversations.py`
```python
@click.command(name="push")
@project_location_options(required=False)
@app_name_option(required=False)
@click.option("--app-dir", default=".", help="Path to local app directory.")
@click.option("--to", help="Target app resource name or display name.")
@click.option("--env-file", help="Explicit path to an environment.json file.")
@click.option("--display-name", help="Display name override.")
@click.option("--create-version", is_flag=True, help="Create immutable version after push.")
@click.option("--version-description", help="Description for created version.")
@click.option("--overwrite", is_flag=True, help="Overwrite remote entities.")
@click.pass_context
def app_push_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Push local agent definitions to remote app.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_push(args)

@click.command(name="pull")
@project_location_options(required=False)
@click.argument("app", required=True)
@click.option("--target-dir", default=".", help="Path to destination directory.")
@click.option("--overwrite", is_flag=True, help="Overwrite existing local files.")
@click.pass_context
def app_pull_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Pull remote app definitions to local directory.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_pull(args)

@click.command(name="lint")
@click.option("--app-dir", default=".", help="Path to app directory.")
@click.option("--fix", is_flag=True, help="Automatically apply safe linter fixes.")
@click.option("--only", type=click.Choice(["instructions", "callbacks", "tools", "evals", "config", "structure", "schema"]), help="Run only specific rule categories.")
@click.option("--rule", help="Run a specific rule by ID.")
@click.option("--json", "json_output", is_flag=True, help="Output results in JSON format.")
@click.option("--list-rules", is_flag=True, help="List all available linter rules and exit.")
@click.option("--validate-only", is_flag=True, help="Validate only without loading remote assets.")
@click.option("--agents", type=str, help="Comma-separated list of agents to check.")
@click.option("--tools", type=str, help="Comma-separated list of tools to check.")
@click.option("--agent", help="Run checks on a single specific agent.")
@click.option("--tool", help="Run checks on a single specific tool.")
@click.option("--toolset", help="Run checks on a specific toolset.")
@click.option("--guardrail", help="Run checks on a specific guardrail.")
@click.option("--evaluation", help="Run checks on a specific evaluation.")
@click.option("--evaluation-expectations", help="Run checks on evaluation expectations.")
@click.pass_context
def app_lint_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Fast, deterministic static structural linter across 60+ checks.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options for structural linting.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_lint(args)

@click.command(name="llm-lint")
@click.option("--agent-dir", required=True, help="Path to single sub-agent directory.")
@click.option("--project-id", "-p", envvar="GCP_PROJECT_ID", help="GCP project ID.")
@click.option("--location", "-l", envvar="GCP_REGION", default="us-central1", help="GCP location.")
@click.option("--model", default="gemini-2.5-flash", help="Gemini model string.")
@click.option("--output", help="Path to save lint report.")
@click.pass_context
def llm_lint_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """AI-driven semantic prompt linter for GECX sub-agent instructions.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options for prompt linting.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    llm_lint(args)

@click.group(name="local")
def local_group() -> None:
    """Create local template definitions.

    Returns:
        None
    """

@local_group.group(name="create")
def local_create_group() -> None:
    """Create local starter templates.

    Returns:
        None
    """

@local_create_group.command(name="agent")
@click.argument("name", required=True)
@click.option("--app-dir", default=".", help="Target app directory path.")
@click.pass_context
def local_create_agent_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Create a local agent template.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    handle_local_create(args)

@local_create_group.command(name="tool")
@click.argument("name", required=True)
@click.argument("tool_type", required=False, default=None)
@click.option("--app-dir", default=".", help="Target app directory path.")
@click.option("--add-to-agent", is_flag=True, help="Add tool reference to agent definition.")
@click.pass_context
def local_create_tool_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Create a local tool template.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    handle_local_create(args)

@local_create_group.command(name="guardrail")
@click.argument("name", required=True)
@click.argument("guardrail_type", required=False, default="llm_policy")
@click.option("--app-dir", default=".", help="Target app directory path.")
@click.pass_context
def local_create_guardrail_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Create a local guardrail template.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    handle_local_create(args)
```

---

## 4. Lazy Multi-Command Architecture (`src/cxas_scrapi/cli/main.py`)
```python
import os
import click
from typing import Any
from cxas_scrapi.cli.utils import help_cmd

COMMAND_MAP: dict[str, tuple[str, str, str]] = {
    "help": ("cxas_scrapi.cli.utils", "help_cmd", "Inspect help documentation for any command."),
    "push": ("cxas_scrapi.cli.app", "app_push_cmd", "Push local agent definitions to remote app."),
    "pull": ("cxas_scrapi.cli.app", "app_pull_cmd", "Pull remote app definitions to local directory."),
    "lint": ("cxas_scrapi.cli.app", "app_lint_cmd", "Fast, deterministic static structural linter."),
    "llm-lint": ("cxas_scrapi.cli.llm_lint", "llm_lint_cmd", "AI-driven semantic prompt linter."),
    "run-session": ("cxas_scrapi.cli.sessions", "run_session_cmd", "Launch interactive terminal session."),
    "init": ("cxas_scrapi.cli.app", "app_init_cmd", "Initialize local workspace configuration."),
    "branch": ("cxas_scrapi.cli.app", "app_branch_cmd", "Branch a remote app to a new app name."),
    "delete": ("cxas_scrapi.cli.app", "app_delete_cmd", "Delete a remote app."),
    "create": ("cxas_scrapi.cli.app", "app_create_cmd", "Create a new remote app on GCP."),
    "run": ("cxas_scrapi.cli.evals", "run_cmd", "Run simulation evaluations."),
    "ci-test": ("cxas_scrapi.cli.evals", "ci_test_cmd", "Run continuous integration evaluations."),
    "local-test": ("cxas_scrapi.cli.evals", "local_test_cmd", "Run local integration evaluations."),
    "export": ("cxas_scrapi.cli.evals", "export_cmd", "Export evaluation definitions to local YAML."),
    "push-eval": ("cxas_scrapi.cli.evals", "push_eval_cmd", "Push local evaluation definitions to remote app."),
    "test-tools": ("cxas_scrapi.cli.evals", "test_tools_cmd", "Run tool evaluation tests."),
    "test-callbacks": ("cxas_scrapi.cli.evals", "test_callbacks_cmd", "Run callback evaluation tests."),
    "test-single-callback": ("cxas_scrapi.cli.evals", "test_single_callback_cmd", "Run evaluation for a single callback."),
    "init-github-action": ("cxas_scrapi.cli.app", "init_github_action_cmd", "Generate GitHub Actions workflow file."),
    "apps": ("cxas_scrapi.cli.app", "apps_group", "Inspect and manage GCP apps."),
    "local": ("cxas_scrapi.cli.create_local", "local_group", "Create local template definitions."),
    "evals": ("cxas_scrapi.cli.evals", "evals_group", "Manage and report on evaluations."),
    "deployments": ("cxas_scrapi.cli.deployments", "deployments_group", "Manage remote app deployments."),
    "conversations": ("cxas_scrapi.cli.conversations", "conversations_group", "Inspect past conversations."),
    "tools": ("cxas_scrapi.cli.resources_cli", "tools_group", "Manage remote tools."),
    "callbacks": ("cxas_scrapi.cli.resources_cli", "callbacks_group", "Manage remote callbacks."),
    "variables": ("cxas_scrapi.cli.resources_cli", "variables_group", "Manage remote session variables."),
    "trace": ("cxas_scrapi.cli.trace_cli", "trace_group", "Observability and trace inspection."),
    "insights": ("cxas_scrapi.cli.insights_cli", "insights_group", "Perform quality AI and insights analysis."),
    "migrate": ("cxas_scrapi.cli.migration_cli", "migrate_group", "Migrate external agents to CXAS."),
    "versions": ("cxas_scrapi.cli.versions_cli", "versions_group", "Inspect and compare app versions."),
}

class LazyRootGroup(click.Group):
    """Custom Click Group that lazily imports subcommand modules only upon invocation.

    Returns:
        None
    """
    def list_commands(self, ctx: click.Context) -> list[str]:
        """Return sorted list of available command names.

        Args:
            ctx: Click execution context.
        Returns:
            list[str]: Sorted list of command names.
        """
        return sorted(COMMAND_MAP.keys())

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        """Lazily import target module and retrieve Click Command or Group.

        Args:
            ctx: Click execution context.
            cmd_name: Subcommand name string.
        Returns:
            click.Command | None: The loaded command object, or None if not found.
        """
        if cmd_name not in COMMAND_MAP:
            return None
        module_path, attr_name, _ = COMMAND_MAP[cmd_name]
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, attr_name)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Format command list directly from memory without triggering eager module imports.

        Args:
            ctx: Click execution context.
            formatter: Help formatter instance.
        Returns:
            None
        """
        commands = []
        for cmd_name in self.list_commands(ctx):
            entry = COMMAND_MAP.get(cmd_name)
            if entry and len(entry) >= 3:
                commands.append((cmd_name, entry[2]))
            else:
                cmd = self.get_command(ctx, cmd_name)
                if cmd is not None and not cmd.hidden:
                    commands.append((cmd_name, cmd.get_short_help_str(90)))
        if commands:
            with formatter.section("Commands"):
                formatter.write_dl(commands)

@click.group(cls=LazyRootGroup, name="cxas", context_settings=dict(help_option_names=["-h", "--help"]))
@click.option("--oauth-token", help="OAuth token string for CES API authentication.")
@click.option("--no-input", is_flag=True, help="Disable interactive prompts in CI/CD pipelines.")
@click.pass_context
def cli(ctx: click.Context, oauth_token: str | None, no_input: bool) -> None:
    """Root CLI group entrypoint for cxas-scrapi.

    Args:
        ctx: Click execution context.
        oauth_token: Optional OAuth bearer token override.
        no_input: Flag to suppress interactive terminal input.
    Returns:
        None
    """
    if oauth_token:
        os.environ["CXAS_OAUTH_TOKEN"] = oauth_token
    ctx.ensure_object(dict)
    ctx.obj["no_input"] = no_input
    ctx.obj["oauth_token"] = oauth_token

def main() -> None:
    """CLI main execution entrypoint.

    Returns:
        None
    """
    cli()
```

---

## 5. Integration Test Updates (`tests/cxas_scrapi/cli/`)
```python
from click.testing import CliRunner
from cxas_scrapi.cli.main import cli
from cxas_scrapi.cli.insights_cli import insights_group
from cxas_scrapi.cli.trace_cli import trace_group

def test_cli_commands() -> None:
    """Verify Click root command and subcommand registration.

    Returns:
        None
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "evals" in result.output
    assert "deployments" in result.output

def test_cli_installed_help() -> None:
    """Verify installed console script help output case-insensitively.

    Returns:
        None
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert "usage: cxas" in result.output.lower()
```
### 3.5 `resources_cli.py` (`tools`, `callbacks`, `variables` Groups)
```python
@click.group(name="tools")
def tools_group() -> None:
    """Manage remote tools.

    Returns:
        None
    """

@tools_group.command(name="list")
@project_location_options(required=False)
@app_name_option(required=True)
@click.pass_context
def tools_list_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """List remote tools.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    tools_list(args)

@tools_group.command(name="delete")
@project_location_options(required=False)
@app_name_option(required=True)
@click.option("--name", required=True, help="Name of the tool to delete.")
@click.pass_context
def tools_delete_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Delete a remote tool.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    tools_delete(args)

@click.group(name="callbacks")
def callbacks_group() -> None:
    """Manage remote callbacks.

    Returns:
        None
    """

@callbacks_group.command(name="list")
@project_location_options(required=False)
@app_name_option(required=True)
@click.option("--agent-name", help="Agent resource name filter.")
@click.option("--callback-type", help="Callback type filter.")
@click.pass_context
def callbacks_list_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """List remote callbacks.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    callbacks_list(args)

@callbacks_group.command(name="delete")
@project_location_options(required=False)
@app_name_option(required=True)
@click.option("--agent-name", required=True, help="Agent resource name.")
@click.option("--callback-type", type=click.Choice(["before_model", "after_model", "before_tool", "after_tool", "before_agent", "after_agent"]), required=True, help="Callback type string.")
@click.option("--index", type=int, required=True, help="Index of callback.")
@click.pass_context
def callbacks_delete_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Delete a remote callback.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    callbacks_delete(args)

@click.group(name="variables")
def variables_group() -> None:
    """Manage remote session variables.

    Returns:
        None
    """

@variables_group.command(name="list")
@project_location_options(required=False)
@app_name_option(required=True)
@click.pass_context
def variables_list_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """List remote session variables.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    variables_list(args)

@variables_group.command(name="delete")
@project_location_options(required=False)
@app_name_option(required=True)
@click.option("--name", required=True, help="Variable name to delete.")
@click.pass_context
def variables_delete_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Delete a remote session variable.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    variables_delete(args)
```

### 3.6 `trace_cli.py` (`trace` Group with All 11 Complete Subcommands)
```python
@click.group(name="trace")
def trace_group() -> None:
    """Observability and trace inspection.

    Returns:
        None
    """

@trace_group.command(name="search")
@app_name_option(required=True)
@click.argument("query", type=str, required=True)
@click.option("--match", type=click.Choice(["phrase", "all", "any"]), default="phrase", help="Match type for query.")
@click.option("--time-filter", default=None, help="Relative time filter (e.g. '7d', '24h').")
@click.option("--source", type=click.Choice(["LIVE", "SIMULATOR", "EVAL"]), help="Filter by single source.")
@click.option("--sources", type=click.Choice(["LIVE", "SIMULATOR", "EVAL"]), multiple=True, help="Filter by multiple sources.")
@click.option("--channel", type=click.Choice(["TEXT", "AUDIO", "MULTIMODAL", "OTHER"]), help="Filter by channel.")
@click.option("--limit", type=int, help="Maximum number of results to return.")
@click.option("--page-size", type=int, help="Server-side page size hint.")
@click.option("--no-id-match", "id_match", is_flag=True, default=True, help="Do not also match an exact customer_conversation_id.")
@click.option("--snippets", is_flag=True, help="Show highlighted excerpts in output table.")
@click.option("--format", "format_", type=click.Choice(["table", "json", "csv"]), default="table", help="Output format.")
@click.option("--app-dir", default=".", help="Path to local app directory.")
@click.option("--env-file", help="Explicit path to an environment.json file.")
@click.option("--environment", help="Named environment.")
@click.option("--config", help="Path to a trace.yaml config file.")
@click.pass_context
def trace_search_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Search conversation transcripts by query.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options for trace search.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_search(args)

@trace_group.command(name="list")
@app_name_option(required=True)
@click.option("--time-filter", default="24h", help="Relative time filter.")
@click.option("--limit", type=int, default=20, help="Maximum results.")
@click.pass_context
def trace_list_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """List recent conversation traces.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_list(args)

@trace_group.command(name="get")
@app_name_option(required=True)
@click.argument("conversation_id", required=True)
@click.pass_context
def trace_get_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Get detailed turns for a specific trace.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_get(args)

@trace_group.command(name="logs")
@app_name_option(required=True)
@click.argument("conversation_id", required=True)
@click.pass_context
def trace_logs_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Get raw AppEngine logging traces for a conversation.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_logs(args)

@trace_group.group(name="audio")
def trace_audio_group() -> None:
    """Inspect and analyze trace audio.

    Returns:
        None
    """

@trace_audio_group.command(name="download")
@app_name_option(required=True)
@click.argument("conversation_id", required=True)
@click.pass_context
def trace_audio_download_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Download audio clips for a conversation.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_audio_download(args)

@trace_audio_group.command(name="analyze")
@app_name_option(required=True)
@click.argument("conversation_id", required=True)
@click.pass_context
def trace_audio_analyze_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Analyze audio clips for quality.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_audio_analyze(args)

@trace_group.command(name="triage")
@app_name_option(required=True)
@click.argument("conversation_id", required=True)
@click.pass_context
def trace_triage_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Automated AI triage for a trace.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_triage(args)

@trace_group.command(name="replay")
@app_name_option(required=True)
@click.argument("conversation_id", required=True)
@click.pass_context
def trace_replay_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Replay conversation turn by turn.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_replay(args)

@trace_group.command(name="stats")
@app_name_option(required=True)
@click.pass_context
def trace_stats_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Inspect aggregated trace statistics.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_stats(args)

@trace_group.command(name="bundle")
@app_name_option(required=True)
@click.argument("conversation_id", required=True)
@click.pass_context
def trace_bundle_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Export complete debug bundle for a trace.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_bundle(args)

@trace_group.command(name="bug-report")
@app_name_option(required=True)
@click.argument("conversation_id", required=True)
@click.pass_context
def trace_bug_report_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Generate formatted bug report for a trace.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_bug_report(args)

@trace_group.command(name="open")
@app_name_option(required=True)
@click.argument("conversation_id", required=True)
@click.pass_context
def trace_open_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Open conversation in Cloud Console UI.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    trace_open(args)
```

### 3.7 `insights_cli.py` (`insights` Group Representative Commands & Explicit Schema Invariant)
```python
@click.group(name="insights")
def insights_group() -> None:
    """Perform quality AI and insights analysis.

    Returns:
        None
    """

@insights_group.command(name="list-scorecards")
@click.option("--parent", required=True, help="Parent GCP location string.")
@click.pass_context
def list_scorecards_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """List CCAI Insights scorecards.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    run_insights(args)

@insights_group.command(name="create-scorecard")
@click.option("--parent", required=True, help="Parent GCP location string.")
@click.option("--scorecard-id", required=True, help="Unique scorecard ID.")
@click.option("--display-name", required=True, help="Scorecard display name.")
@click.pass_context
def create_scorecard_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Create a new CCAI Insights scorecard.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    run_insights(args)
# Explicit Invariant: Every remaining insights command (`export`, `import`, `copy`, `add-question`, `activate-scorecard`, `deploy-scorecard`, `validate-scorecard`, `list-topic-models`, `create-topic-model`, `deploy-topic-model`, `undeploy-topic-model`, `get-topic-model`, `list-topics`, `list-analysis-rules`, `create-analysis-rule`, `activate-analysis-rule`, `get-analysis-rule`, `delete-analysis-rule`, `smoke-test-scorecard`, `analyze-metrics`) strictly implements explicit `@insights_group.command(...)` schemas, `to_namespace(ctx, **kwargs)`, PEP 585/604 annotations, and structured `Args:`/`Returns:` docstrings during Stage 3 execution.
```

### 3.8 `versions_cli.py` (`versions` Group)
```python
@click.group(name="versions")
def versions_group() -> None:
    """Inspect and compare app versions.

    Returns:
        None
    """

@versions_group.command(name="list")
@project_location_options(required=False)
@app_name_option(required=True)
@click.pass_context
def versions_list_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """List versions for an app.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_versions_list(args)

@versions_group.command(name="compare")
@project_location_options(required=False)
@app_name_option(required=True)
@click.option("--source", required=True, help="Source version ID.")
@click.option("--target", required=True, help="Target version ID.")
@click.option("--verbose", "-v", is_flag=True, help="Detailed console diff.")
@click.option("--web", is_flag=True, help="Generate interactive HTML diff report.")
@click.option("--output", help="Optional path to save comparison report.")
@click.pass_context
def versions_compare_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Compare two app versions.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_versions_compare(args)
```
### 3.9 `app.py` (`push`, `pull`, `lint`, `init`, `branch`, `delete`, `create`, `apps_group`)
```python
@click.command(name="push")
@project_location_options(required=False)
@app_name_option(required=False)
@click.option("--app-dir", default=".", help="Path to local app directory.")
@click.option("--to", help="Target app resource name or display name.")
@click.option("--env-file", help="Explicit path to an environment.json file.")
@click.option("--display-name", help="Display name override.")
@click.option("--create-version", is_flag=True, help="Create immutable version after push.")
@click.option("--version-description", help="Description for created version.")
@click.option("--overwrite", is_flag=True, help="Overwrite remote entities.")
@click.pass_context
def app_push_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Push local agent definitions to remote app.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_push(args)

@click.command(name="pull")
@project_location_options(required=False)
@click.argument("app", required=True)
@click.option("--target-dir", default=".", help="Path to destination directory.")
@click.option("--overwrite", is_flag=True, help="Overwrite existing local files.")
@click.pass_context
def app_pull_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Pull remote app definitions to local directory.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_pull(args)

@click.command(name="lint")
@click.option("--app-dir", default=".", help="Path to app directory.")
@click.option("--fix", is_flag=True, help="Automatically apply safe linter fixes.")
@click.option("--only", type=click.Choice(["instructions", "callbacks", "tools", "evals", "config", "structure", "schema"]), help="Run only specific rule categories.")
@click.option("--rule", help="Run a specific rule by ID.")
@click.option("--json", "json_output", is_flag=True, help="Output results in JSON format.")
@click.option("--list-rules", is_flag=True, help="List all available linter rules and exit.")
@click.option("--validate-only", is_flag=True, help="Validate only without loading remote assets.")
@click.option("--agents", type=str, help="Comma-separated list of agents to check.")
@click.option("--tools", type=str, help="Comma-separated list of tools to check.")
@click.option("--agent", help="Run checks on a single specific agent.")
@click.option("--tool", help="Run checks on a single specific tool.")
@click.option("--toolset", help="Run checks on a specific toolset.")
@click.option("--guardrail", help="Run checks on a specific guardrail.")
@click.option("--evaluation", help="Run checks on a specific evaluation.")
@click.option("--evaluation-expectations", help="Run checks on evaluation expectations.")
@click.pass_context
def app_lint_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Fast, deterministic static structural linter across 60+ checks.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options for structural linting.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_lint(args)

@click.command(name="init")
@click.option("--target-dir", default=".", help="Target workspace directory.")
@click.option("--force", is_flag=True, help="Overwrite existing workspace setup.")
@click.pass_context
def app_init_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Initialize local workspace configuration.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_init(args)

@click.command(name="branch")
@project_location_options(required=False)
@app_name_option(required=True)
@click.argument("source", required=True)
@click.argument("new_name", required=True)
@click.option("--env-file", help="Explicit path to environment variables JSON.")
@click.pass_context
def app_branch_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Branch a remote app to a new app name.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_branch(args)

@click.command(name="delete")
@project_location_options(required=False)
@app_name_option(required=False)
@click.option("--display-name", help="Display name of app to delete.")
@click.option("--force", is_flag=True, help="Force deletion without confirmation.")
@click.pass_context
def app_delete_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Delete a remote app.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_delete(args)

@click.command(name="create")
@project_location_options(required=True)
@app_name_option(required=False)
@click.argument("name", required=True)
@click.option("--description", help="App description string.")
@click.option("--app-id", help="Explicit unique app ID.")
@click.pass_context
def app_create_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Create a new remote app on GCP.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    app_create(args)

@click.command(name="init-github-action")
@click.pass_context
def init_github_action_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Generate GitHub Actions workflow file.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    init_github_action(args)

@click.group(name="apps")
def apps_group() -> None:
    """Inspect and manage GCP apps.

    Returns:
        None
    """

@apps_group.command(name="list")
@project_location_options(required=True)
@click.pass_context
def apps_list_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """List all apps in a GCP location.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    apps_list(args)

@apps_group.command(name="get")
@project_location_options(required=False)
@click.argument("app", required=True)
@click.pass_context
def apps_get_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Get configuration details for a specific app.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    apps_get(args)
```

### 3.10 `llm_lint.py` (`llm-lint` Command Bridge)
```python
@click.command(name="llm-lint")
@click.option("--agent-dir", required=True, help="Path to single sub-agent directory.")
@click.option("--project-id", "-p", envvar="GCP_PROJECT_ID", help="GCP project ID.")
@click.option("--location", "-l", envvar="GCP_REGION", default="us-central1", help="GCP location.")
@click.option("--model", default="gemini-2.5-flash", help="Gemini model string.")
@click.option("--output", help="Path to save lint report.")
@click.pass_context
def llm_lint_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """AI-driven semantic prompt linter for GECX sub-agent instructions.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options for prompt linting.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    llm_lint(args)
```

### 3.11 `conversations.py` (`conversations` Group)
```python
@click.group(name="conversations")
def conversations_group() -> None:
    """Inspect past conversations.

    Returns:
        None
    """

@conversations_group.command(name="list")
@project_location_options(required=False)
@app_name_option(required=True)
@click.pass_context
def conversations_list_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """List conversations.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    conversations_list(args)

@conversations_group.command(name="get")
@click.argument("conversation_resource_name", required=True)
@click.pass_context
def conversations_get_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Get detailed turns for a conversation.

    Args:
        ctx: Click execution context.
        kwargs: Parsed CLI options.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    conversations_get(args)
```

### 3.12 `create_local.py` (`local` Group)
```python
@click.group(name="local")
def local_group() -> None:
    """Create local template definitions.

    Returns:
        None
    """

@local_group.group(name="create")
def local_create_group() -> None:
    """Create local starter templates.

    Returns:
        None
    """

@local_create_group.command(name="agent")
@click.argument("name", required=True)
@click.option("--app-dir", default=".", help="Target app directory path.")
@click.pass_context
def local_create_agent_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Create a local agent template.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    handle_local_create(args)

@local_create_group.command(name="tool")
@click.argument("name", required=True)
@click.argument("tool_type", required=False, default=None)
@click.option("--app-dir", default=".", help="Target app directory path.")
@click.option("--add-to-agent", type=str, help="Agent to add tool reference to.")
@click.pass_context
def local_create_tool_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Create a local tool template.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    handle_local_create(args)

@local_create_group.command(name="guardrail")
@click.argument("name", required=True)
@click.argument("guardrail_type", required=False, default="llm_policy")
@click.option("--app-dir", default=".", help="Target app directory path.")
@click.pass_context
def local_create_guardrail_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Create a local guardrail template.

    Args:
        ctx: Click execution context.
        kwargs: Parsed options and arguments.
    Returns:
        None
    """
    args = to_namespace(ctx, **kwargs)
    handle_local_create(args)
```

---

## 4. Lazy Multi-Command Architecture (`src/cxas_scrapi/cli/main.py`)
```python
import os
import click
from typing import Any
from cxas_scrapi.cli.utils import help_cmd

COMMAND_MAP: dict[str, tuple[str, str, str]] = {
    "help": ("cxas_scrapi.cli.utils", "help_cmd", "Inspect help documentation for any command."),
    "push": ("cxas_scrapi.cli.app", "app_push_cmd", "Push local agent definitions to remote app."),
    "pull": ("cxas_scrapi.cli.app", "app_pull_cmd", "Pull remote app definitions to local directory."),
    "lint": ("cxas_scrapi.cli.app", "app_lint_cmd", "Fast, deterministic static structural linter."),
    "llm-lint": ("cxas_scrapi.cli.llm_lint", "llm_lint_cmd", "AI-driven semantic prompt linter."),
    "run-session": ("cxas_scrapi.cli.sessions", "run_session_cmd", "Launch interactive terminal session."),
    "init": ("cxas_scrapi.cli.app", "app_init_cmd", "Initialize local workspace configuration."),
    "branch": ("cxas_scrapi.cli.app", "app_branch_cmd", "Branch a remote app to a new app name."),
    "delete": ("cxas_scrapi.cli.app", "app_delete_cmd", "Delete a remote app."),
    "create": ("cxas_scrapi.cli.app", "app_create_cmd", "Create a new remote app on GCP."),
    "run": ("cxas_scrapi.cli.evals", "run_cmd", "Run simulation evaluations."),
    "ci-test": ("cxas_scrapi.cli.evals", "ci_test_cmd", "Run continuous integration evaluations."),
    "local-test": ("cxas_scrapi.cli.evals", "local_test_cmd", "Run local integration evaluations."),
    "export": ("cxas_scrapi.cli.evals", "export_cmd", "Export evaluation definitions to local YAML."),
    "push-eval": ("cxas_scrapi.cli.evals", "push_eval_cmd", "Push local evaluation definitions to remote app."),
    "test-tools": ("cxas_scrapi.cli.evals", "test_tools_cmd", "Run tool evaluation tests."),
    "test-callbacks": ("cxas_scrapi.cli.evals", "test_callbacks_cmd", "Run callback evaluation tests."),
    "test-single-callback": ("cxas_scrapi.cli.evals", "test_single_callback_cmd", "Run evaluation for a single callback."),
    "init-github-action": ("cxas_scrapi.cli.app", "init_github_action_cmd", "Generate GitHub Actions workflow file."),
    "apps": ("cxas_scrapi.cli.app", "apps_group", "Inspect and manage GCP apps."),
    "local": ("cxas_scrapi.cli.create_local", "local_group", "Create local template definitions."),
    "evals": ("cxas_scrapi.cli.evals", "evals_group", "Manage and report on evaluations."),
    "deployments": ("cxas_scrapi.cli.deployments", "deployments_group", "Manage remote app deployments."),
    "conversations": ("cxas_scrapi.cli.conversations", "conversations_group", "Inspect past conversations."),
    "tools": ("cxas_scrapi.cli.resources_cli", "tools_group", "Manage remote tools."),
    "callbacks": ("cxas_scrapi.cli.resources_cli", "callbacks_group", "Manage remote callbacks."),
    "variables": ("cxas_scrapi.cli.resources_cli", "variables_group", "Manage remote session variables."),
    "trace": ("cxas_scrapi.cli.trace_cli", "trace_group", "Observability and trace inspection."),
    "insights": ("cxas_scrapi.cli.insights_cli", "insights_group", "Perform quality AI and insights analysis."),
    "migrate": ("cxas_scrapi.cli.migration_cli", "migrate_group", "Migrate external agents to CXAS."),
    "versions": ("cxas_scrapi.cli.versions_cli", "versions_group", "Inspect and compare app versions."),
}

class LazyRootGroup(click.Group):
    """Custom Click Group that lazily imports subcommand modules only upon invocation.

    Returns:
        None
    """
    def list_commands(self, ctx: click.Context) -> list[str]:
        """Return sorted list of available command names.

        Args:
            ctx: Click execution context.
        Returns:
            list[str]: Sorted list of command names.
        """
        return sorted(COMMAND_MAP.keys())

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        """Lazily import target module and retrieve Click Command or Group.

        Args:
            ctx: Click execution context.
            cmd_name: Subcommand name string.
        Returns:
            click.Command | None: The loaded command object, or None if not found.
        """
        if cmd_name not in COMMAND_MAP:
            return None
        module_path, attr_name, _ = COMMAND_MAP[cmd_name]
        import importlib
        module = importlib.import_module(module_path)
        return getattr(module, attr_name)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Format command list directly from memory without triggering eager module imports.

        Args:
            ctx: Click execution context.
            formatter: Help formatter instance.
        Returns:
            None
        """
        commands = []
        for cmd_name in self.list_commands(ctx):
            entry = COMMAND_MAP.get(cmd_name)
            if entry and len(entry) >= 3:
                commands.append((cmd_name, entry[2]))
            else:
                cmd = self.get_command(ctx, cmd_name)
                if cmd is not None and not cmd.hidden:
                    commands.append((cmd_name, cmd.get_short_help_str(90)))
        if commands:
            with formatter.section("Commands"):
                formatter.write_dl(commands)

@click.group(cls=LazyRootGroup, name="cxas", context_settings=dict(help_option_names=["-h", "--help"]))
@click.option("--oauth-token", help="OAuth token string for CES API authentication.")
@click.option("--no-input", is_flag=True, help="Disable interactive prompts in CI/CD pipelines.")
@click.pass_context
def cli(ctx: click.Context, oauth_token: str | None, no_input: bool) -> None:
    """Root CLI group entrypoint for cxas-scrapi.

    Args:
        ctx: Click execution context.
        oauth_token: Optional OAuth bearer token override.
        no_input: Flag to suppress interactive terminal input.
    Returns:
        None
    """
    if oauth_token:
        os.environ["CXAS_OAUTH_TOKEN"] = oauth_token
    ctx.ensure_object(dict)
    ctx.obj["no_input"] = no_input
    ctx.obj["oauth_token"] = oauth_token

def main() -> None:
    """CLI main execution entrypoint.

    Returns:
        None
    """
    cli()
```

---

## 5. Integration Test Updates (`tests/cxas_scrapi/cli/`)
```python
from click.testing import CliRunner
from cxas_scrapi.cli.main import cli
from cxas_scrapi.cli.insights_cli import insights_group
from cxas_scrapi.cli.trace_cli import trace_group
from cxas_scrapi.cli.resources_cli import tools_group, callbacks_group, variables_group
import subprocess
import sys
import pytest

def test_cli_commands() -> None:
    """Verify Click root command and subcommand registration.

    Returns:
        None
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "evals" in result.output
    assert "deployments" in result.output

def test_cli_installed_help() -> None:
    """Verify installed console script help output case-insensitively.

    Returns:
        None
    """
    try:
        py_code = (
            "import sys; "
            "sys.argv[0]='cxas'; "
            "from cxas_scrapi.cli.main import main; "
            "main()"
        )
        result = subprocess.run(
            [sys.executable, "-c", py_code, "--help"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.returncode == 0
        assert "usage: cxas" in result.stdout.lower()
    except FileNotFoundError:
        pytest.fail("The 'cxas' command was not found in the environment. Is it installed?")

def test_get_parser() -> None:
    """Verify root command and subcommands can be invoked and display help without execution errors.

    Returns:
        None
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["apps", "list", "--help"])
    assert result.exit_code == 0
    assert "--project-id" in result.output
    assert "--location" in result.output

def test_get_parser_llm_lint() -> None:
    """Verify llm-lint command option parsing and help availability.

    Returns:
        None
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["llm-lint", "--help"])
    assert result.exit_code == 0
    assert "--agent-dir" in result.output
    assert "--model" in result.output
    assert "--output" in result.output

def test_get_parser_evals_report() -> None:
    """Verify evals report command options parse cleanly via help documentation.

    Returns:
        None
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["evals", "report", "--help"])
    assert result.exit_code == 0
    assert "--output-dir" in result.output
    assert "--sim-user-model" in result.output
    assert "--eval-model" in result.output
    assert "--run" in result.output

def test_get_parser_evals_report_timestamped() -> None:
    """Verify evals report command supports --timestamped flag.

    Returns:
        None
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["evals", "report", "--help"])
    assert result.exit_code == 0
    assert "--timestamped" in result.output

def test_get_parser_run_session_use_tool_fakes() -> None:
    """Verify run-session command supports --use-tool-fakes flag.

    Returns:
        None
    """
    runner = CliRunner()
    result = runner.invoke(cli, ["run-session", "--help"])
    assert result.exit_code == 0
    assert "--use-tool-fakes" in result.output

def test_parser_resources() -> None:
    """Verify GECX resource groups (tools, callbacks, variables) register commands and options correctly.

    Returns:
        None
    """
    runner = CliRunner()
    res_tools = runner.invoke(tools_group, ["list", "--help"])
    assert res_tools.exit_code == 0
    assert "--app-name" in res_tools.output
    res_tools_del = runner.invoke(tools_group, ["delete", "--help"])
    assert res_tools_del.exit_code == 0
    assert "--name" in res_tools_del.output
    res_cb = runner.invoke(callbacks_group, ["list", "--help"])
    assert res_cb.exit_code == 0
    assert "--app-name" in res_cb.output
    assert "--agent-name" in res_cb.output
    res_vars = runner.invoke(variables_group, ["list", "--help"])
    assert res_vars.exit_code == 0
    assert "--app-name" in res_vars.output

def test_populate_insights_parser() -> None:
    """Verify insights group subcommands (list-scorecards, create-scorecard, create-topic-model) are registered.

    Returns:
        None
    """
    runner = CliRunner()
    result = runner.invoke(insights_group, ["--help"])
    assert result.exit_code == 0
    assert "list-scorecards" in result.output
    assert "create-scorecard" in result.output
    assert "create-topic-model" in result.output

def test_register_smoke() -> None:
    """Verify trace list subcommand routing and options via Click runner.

    Returns:
        None
    """
    runner = CliRunner()
    result = runner.invoke(trace_group, ["list", "--help"])
    assert result.exit_code == 0
    assert "--app-name" in result.output
    assert "--time-filter" in result.output

def test_register_search_smoke() -> None:
    """Verify trace search subcommand routing, options, and flags via Click runner.

    Returns:
        None
    """
    runner = CliRunner()
    result = runner.invoke(trace_group, ["search", "--help"])
    assert result.exit_code == 0
    assert "--app-name" in result.output
    assert "--match" in result.output
    assert "--sources" in result.output
    assert "--snippets" in result.output
    assert "--no-id-match" in result.output
```

### 5.3 Execution Verification & Linter Mandates
During Stage 3 execution, verify all commands and tests strictly via `uv run` across the following validation checkpoints:
1. **Test Suite Execution**: Run `uv run pytest tests/cxas_scrapi/cli/` (`150` tests). Ensure 100% test pass rate across all 11 test files (`CliRunner` updates where commands are invoked, core handlers tested directly via `SimpleNamespace`).
2. **Dynamic Help & Eager Loading Verification**: Run `uv run cxas --help`, `uv run cxas help trace search`, and `uv run cxas lint --help`. Verify that startup executes in `<150ms` (`LazyRootGroup.format_commands` memory rendering) and subcommands render exact option help.
3. **Linter Structural Verification**: Run `uv run cxas lint` and `uv run cxas llm-lint --agent-dir <dir>` to verify structural and prompt validation.
