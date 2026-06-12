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

"""CLI script for running CXAS SCRAPI evaluations."""

from __future__ import annotations

import argparse
import datetime
import logging
import os
from typing import TYPE_CHECKING

from cxas_scrapi.cli.insights_cli import populate_insights_parser
from cxas_scrapi.cli.resources_cli import (
    register as register_resources_subparsers,
)
from cxas_scrapi.cli.trace_cli import register as register_trace_subparser

DEFAULT_MODEL = "gemini-3.1-flash-live"

if TYPE_CHECKING:
    from cxas_scrapi.cli.app import (
        app_branch,
        app_create,
        app_delete,
        app_init,
        app_lint,
        app_pull,
        app_push,
        apps_get,
        apps_list,
    )
    from cxas_scrapi.cli.conversations import (
        conversations_get,
        conversations_list,
    )
    from cxas_scrapi.cli.create_local import handle_local_create
    from cxas_scrapi.cli.deployments import (
        deployments_create,
        deployments_list,
        deployments_promote,
    )
    from cxas_scrapi.cli.evals import (
        ci_test,
        combined_evals_report_cmd,
        export_eval,
        local_test,
        push_eval,
        run_eval,
        test_callbacks,
        test_single_callback,
        test_tools,
    )
    from cxas_scrapi.cli.llm_lint import llm_lint
    from cxas_scrapi.cli.migration_cli import run_migration_dashboard
    from cxas_scrapi.cli.sessions import run_session
    from cxas_scrapi.cli.versions_cli import (
        app_versions_compare,
        app_versions_list,
    )
    from cxas_scrapi.core.github import init_github_action
else:
    from cxas_scrapi.cli.utils import LazyCallable

    app_branch = LazyCallable("cxas_scrapi.cli.app", "app_branch")
    app_create = LazyCallable("cxas_scrapi.cli.app", "app_create")
    app_delete = LazyCallable("cxas_scrapi.cli.app", "app_delete")
    app_init = LazyCallable("cxas_scrapi.cli.app", "app_init")
    app_lint = LazyCallable("cxas_scrapi.cli.app", "app_lint")
    app_pull = LazyCallable("cxas_scrapi.cli.app", "app_pull")
    app_push = LazyCallable("cxas_scrapi.cli.app", "app_push")
    apps_get = LazyCallable("cxas_scrapi.cli.app", "apps_get")
    apps_list = LazyCallable("cxas_scrapi.cli.app", "apps_list")
    handle_local_create = LazyCallable(
        "cxas_scrapi.cli.create_local", "handle_local_create"
    )
    llm_lint = LazyCallable("cxas_scrapi.cli.llm_lint", "llm_lint")
    run_migration_dashboard = LazyCallable(
        "cxas_scrapi.cli.migration_cli", "run_migration_dashboard"
    )
    app_versions_list = LazyCallable(
        "cxas_scrapi.cli.versions_cli", "app_versions_list"
    )
    app_versions_compare = LazyCallable(
        "cxas_scrapi.cli.versions_cli", "app_versions_compare"
    )
    init_github_action = LazyCallable(
        "cxas_scrapi.core.github", "init_github_action"
    )

    # Evals
    export_eval = LazyCallable("cxas_scrapi.cli.evals", "export_eval")
    push_eval = LazyCallable("cxas_scrapi.cli.evals", "push_eval")
    run_eval = LazyCallable("cxas_scrapi.cli.evals", "run_eval")
    combined_evals_report_cmd = LazyCallable(
        "cxas_scrapi.cli.evals", "combined_evals_report_cmd"
    )
    test_tools = LazyCallable("cxas_scrapi.cli.evals", "test_tools")
    test_callbacks = LazyCallable("cxas_scrapi.cli.evals", "test_callbacks")
    test_single_callback = LazyCallable(
        "cxas_scrapi.cli.evals", "test_single_callback"
    )
    ci_test = LazyCallable("cxas_scrapi.cli.evals", "ci_test")
    local_test = LazyCallable("cxas_scrapi.cli.evals", "local_test")

    # Sessions
    run_session = LazyCallable("cxas_scrapi.cli.sessions", "run_session")

    # Conversations
    conversations_list = LazyCallable(
        "cxas_scrapi.cli.conversations", "conversations_list"
    )
    conversations_get = LazyCallable(
        "cxas_scrapi.cli.conversations", "conversations_get"
    )

    # Deployments
    deployments_list = LazyCallable(
        "cxas_scrapi.cli.deployments", "deployments_list"
    )
    deployments_create = LazyCallable(
        "cxas_scrapi.cli.deployments", "deployments_create"
    )
    deployments_promote = LazyCallable(
        "cxas_scrapi.cli.deployments", "deployments_promote"
    )

logger = logging.getLogger(__name__)


def get_parser() -> argparse.ArgumentParser:
    """Sets up the argument parser."""
    parser = argparse.ArgumentParser(
        description="CXAS SCRAPI Evaluation Runner for CI/CD.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    parser.add_argument(
        "--oauth-token",
        help=(
            "Optional: OAuth token string for CES API authentication. "
            "Alternatively, set CXAS_OAUTH_TOKEN env var."
        ),
        required=False,
    )

    def _add_project_location_args(
        subparser: argparse.ArgumentParser, required: bool = True
    ) -> None:
        """Helper to add standard GCP args to subparsers."""
        help_suffix = "" if required else " (Optional if using Display Name)"
        subparser.add_argument(
            "--project-id",
            required=required,
            help=f"The GCP Project ID.{help_suffix}",
        )
        subparser.add_argument(
            "--location",
            required=required,
            help=f"The GCP Location (e.g., global, us-central1).{help_suffix}",
        )

    subparsers = parser.add_subparsers(
        title="Commands", dest="command", required=True
    )

    # Parser for 'migrate'
    parser_migrate = subparsers.add_parser("migrate", help="Migration tools.")
    migrate_subparsers = parser_migrate.add_subparsers(
        title="Migration Commands", dest="migrate_command", required=True
    )

    parser_migrate_dfcx = migrate_subparsers.add_parser(
        "dfcx",
        help=(
            "Launch the interactive DFCX migration TUI dashboard, or run "
            "non-interactive --run/--optimize flows."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # Mode selection group
    mode_group = parser_migrate_dfcx.add_mutually_exclusive_group(
        required=False
    )
    mode_group.add_argument(
        "--run",
        action="store_true",
        help="Run end-to-end scriptable DFCX→CXAS migration non-interactively.",
    )
    mode_group.add_argument(
        "--optimize",
        action="store_true",
        help=(
            "Run checkpoint-level optimization stages or resume menu "
            "non-interactively."
        ),
    )

    # General / TUI arguments
    default_name_ts = datetime.datetime.now().strftime("ma-%m%d-%H%M")
    parser_migrate_dfcx.add_argument(
        "--default-agent-name",
        default=default_name_ts,
        help=(
            "Default name for the target agent "
            f"(TUI Mode / Fallback, default: '{default_name_ts}')."
        ),
    )

    # E2E Migration Arguments (active when --run is specified)
    e2e_group = parser_migrate_dfcx.add_argument_group(
        "End-to-End Migration Options (--run)"
    )
    src_group = e2e_group.add_mutually_exclusive_group(required=False)
    src_group.add_argument(
        "--source-agent-id",
        help=(
            "The source DFCX Agent ID (projects/.../locations/.../agents/...)."
        ),
    )
    src_group.add_argument(
        "--source-zip",
        help="Path to a local DFCX agent export (.zip) file.",
    )
    e2e_group.add_argument(
        "--project-id",
        help=(
            "Target GCP Project ID for CXAS deployment "
            "(Required for non-interactive modes)."
        ),
    )
    e2e_group.add_argument(
        "--location",
        default="us",
        help="Target GCP Location for CXAS deployment (Default: 'us').",
    )
    e2e_group.add_argument(
        "--target-name",
        help="The display name prefix / bundle target for the migrated app.",
    )
    e2e_group.add_argument(
        "--env",
        choices=["PROD", "AUTOPUSH"],
        default="PROD",
        help="CXAS Environment to target (PROD or AUTOPUSH).",
    )
    e2e_group.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=(
            "The Gemini model to use for translation & optimization "
            f"(Default: {DEFAULT_MODEL})."
        ),
    )
    e2e_group.add_argument(
        "--profile",
        choices=["standard", "direct", "custom"],
        default="standard",
        help=(
            "The E2E migration profile configuration:\n"
            "  * standard: standard best practices (dedup + N->M TUI "
            "consolidation + Stage 3 wiring)\n"
            "  * direct: baseline fast 1:1 transpile (no "
            "optimizations/consolidation)\n"
            "  * custom: allows overriding via individual switches below"
        ),
    )
    e2e_group.add_argument(
        "--no-optimize",
        action="store_true",
        help=(
            "Custom Mode: Skip Stage 1 + Stage 2 + Stage 3 optimization passes."
        ),
    )
    e2e_group.add_argument(
        "--persist-bundle",
        action="store_true",
        help=(
            "Custom Mode: Persist intermediate IR bundle JSON for "
            "stage-resumability."
        ),
    )
    e2e_group.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Non-interactive mode: auto-confirm stages and operations.",
    )

    # Optimization/Checkpoint Arguments (active when --optimize is specified)
    opt_group = parser_migrate_dfcx.add_argument_group(
        "Optimization / Checkpoint Stage Options (--optimize)"
    )
    opt_group.add_argument(
        "--stage",
        choices=["1", "2", "3", "resume"],
        help=(
            "The specific optimization stage or resume menu to invoke "
            "(Required for --optimize)."
        ),
    )
    opt_group.add_argument(
        "--ir-bundle",
        help="Path to an existing <target>_ir.json bundle file.",
    )
    opt_group.add_argument(
        "--version-label",
        help=(
            "CXAS Version display_name to create after the stage "
            "(Default: '0.0.3' for stage 1, '0.0.4' for stage 2, "
            "'0.0.5' for stage 3)."
        ),
    )
    opt_group.add_argument(
        "--no-persist",
        action="store_true",
        help="Skip writing the updated bundle state back to disk.",
    )
    opt_group.add_argument(
        "--no-unit-tests",
        action="store_true",
        help=(
            "Stage 2: Skip deterministic unit-test goldens/scenarios "
            "generation."
        ),
    )
    opt_group.add_argument(
        "--no-lint",
        action="store_true",
        help=(
            "Stage 2: Skip running local post-deploy schema and practice "
            "linters."
        ),
    )
    opt_group.add_argument(
        "--no-report",
        action="store_true",
        help=(
            "Stage 2: Skip generating the detailed optimization markdown "
            "audit log."
        ),
    )
    opt_group.add_argument(
        "--architecture",
        choices=["hub-and-spoke", "original-hierarchy"],
        default="hub-and-spoke",
        help=(
            "Stage 3: Spoke-Hub architecture style mapping to compile "
            "child routing (Default: 'hub-and-spoke')."
        ),
    )

    parser_migrate_dfcx.set_defaults(func=run_migration_dashboard)

    # Parser for 'init-github-action'
    parser_init_gh = subparsers.add_parser(
        "init-github-action",
        help="Generate a GitHub Actions workflow file for testing the agent.",
    )
    parser_init_gh.add_argument(
        "--app-dir",
        help=(
            "Optional: The path to the app directory (e.g., 'pilot') "
            "to extract app_name and agent_name from app.yaml."
        ),
    )
    parser_init_gh.add_argument(
        "--app-name",
        help=(
            "Optional: The CXAS App ID (projects/.../apps/...). "
            "If missing, extracts from app_dir/app.yaml."
        ),
    )
    parser_init_gh.add_argument(
        "--agent-name",
        help=(
            "Optional: The name of the agent directory to scope the workflow "
            "to (e.g., 'pilot')."
        ),
    )

    parser_init_gh.add_argument(
        "--workload-identity-provider",
        help="Optional: GCP Workload Identity Provider string.",
    )
    parser_init_gh.add_argument(
        "--service-account",
        help="Optional: GCP Service Account email.",
    )
    parser_init_gh.add_argument(
        "--output",
        help=(
            "Optional: Override path where the workflow file will be saved. "
            "Defaults to .github/workflows/test_{agent_name}.yml"
        ),
    )

    _add_project_location_args(parser_init_gh, required=False)

    parser_init_gh.add_argument(
        "--branch",
        default="main",
        help=(
            "Optional: Target branch for deploy trigger (e.g. main). "
            "Defaults to 'main'."
        ),
    )
    parser_init_gh.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Optional: Skip generation of the cleanup workflow.",
    )
    parser_init_gh.add_argument(
        "--install-hook",
        action="store_true",
        help=(
            "Optional: Install a git pre-push hook to run local-test "
            "automatically."
        ),
    )
    parser_init_gh.add_argument(
        "--auto-create-wif",
        action="store_true",
        help=(
            "Optional: Automatically create Workload "
            "Identity Pool, Provider, and Service "
            "Account on Google Cloud."
        ),
    )
    parser_init_gh.add_argument(
        "--wif-pool-name",
        default="github-actions-pool-scrapi",
        help="Optional: The name of the Workload Identity Pool to create/use.",
    )
    parser_init_gh.add_argument(
        "--github-repo",
        help=(
            "Optional: Override inferred GitHub repository (e.g., owner/repo)."
        ),
    )

    parser_init_gh.set_defaults(func=init_github_action)

    parser_evals = subparsers.add_parser("evals", help="Manage evaluations.")
    evals_subparsers = parser_evals.add_subparsers(dest="evals_command")
    parser_report = evals_subparsers.add_parser(
        "report",
        help="Generate combined report for golden + simulation results.",
    )
    parser_report.add_argument(
        "--output-dir",
        required=True,
        help="Directory containing eval results (sim_results.json, etc.).",
    )
    parser_report.add_argument(
        "--output",
        help="Output path. Defaults to <evals-dir>/combined_report.html",
    )
    parser_report.add_argument(
        "--golden-run",
        help="Optional: Golden eval run ID to fetch from server.",
    )
    parser_report.add_argument(
        "--app-name",
        help="Optional: App resource name (projects/.../apps/...)",
    )
    parser_report.add_argument(
        "--run",
        action="store_true",
        help="Run evaluations before generating report.",
    )
    parser_report.add_argument(
        "--app-dir",
        help="Directory of the app (used for callback tests).",
    )
    parser_report.add_argument(
        "--input-dir",
        help=(
            "Base directory containing goldens/, simulations/, "
            "and tool_tests/ subdirectories."
        ),
    )
    parser_report.add_argument(
        "--tool-test-file",
        default="evals/tool_tests/",
        help="Path to tool test file or directory.",
    )
    parser_report.add_argument(
        "--goldens-dir",
        default="evals/goldens/",
        help="Path to goldens directory or file to push.",
    )
    parser_report.add_argument(
        "--simulation-dir",
        default="evals/simulations/",
        help="Path to simulation files directory.",
    )
    parser_report.add_argument(
        "--gcs-path",
        help="Optional: GCS path to store the combined report (starts with gs://).",
    )
    parser_report.add_argument(
        "--format",
        default="html",
        help="Output format (default: html).",
    )
    parser_report.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of runs per golden and simulation test case.",
    )
    parser_report.add_argument(
        "--sim-parallel",
        type=int,
        default=5,
        help=(
            "Number of parallel worker sessions for simulations. Defaults to 5."
        ),
    )
    parser_report.add_argument(
        "--modality",
        choices=["text", "audio"],
        default="text",
        help="Evaluation execution modality (text or audio). Defaults to text.",
    )
    parser_report.add_argument(
        "--include",
        default="sims,goldens,tools,callbacks",
        help=(
            "Categories to include (comma-separated, "
            "default: sims,goldens,tools,callbacks)."
        ),
    )
    parser_report.add_argument(
        "--filter-files",
        help="Optional: Comma-separated list of filenames to include.",
    )
    parser_report.add_argument(
        "--filter-tags",
        help="Optional: Comma-separated list of tags to include.",
    )
    parser_report.add_argument(
        "--golden-timeout",
        type=int,
        default=600,
        help="Timeout in seconds waiting for remote goldens. Defaults to 600.",
    )
    parser_report.add_argument(
        "--bg-noise-file",
        help="Optional: Path to continuous background noise audio file.",
    )
    parser_report.add_argument(
        "--burst-noise-files",
        help=(
            "Optional: Comma-separated list of paths to burst noise audio "
            "files."
        ),
    )
    parser_report.set_defaults(func=combined_evals_report_cmd)

    parser_test_tools = subparsers.add_parser(
        "test-tools",
        help="Run local tool unit tests against the deployed agent.",
    )
    parser_test_tools.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_test_tools.add_argument(
        "--test-file",
        required=True,
        help="Path to the YAML/JSON file containing tool test definitions.",
    )
    parser_test_tools.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging for tool executions.",
    )

    parser_test_tools.set_defaults(func=test_tools)

    # Parser for 'test-callbacks'
    parser_test_callbacks = subparsers.add_parser(
        "test-callbacks",
        help="Run local callback unit tests against the deployed agent.",
    )
    parser_test_callbacks.add_argument(
        "--app-dir",
        required=True,
        help="The path to the app directory.",
    )
    parser_test_callbacks.add_argument(
        "--agent-name",
        required=False,
        help="Optional: The name of the agent to run callback tests for.",
    )
    parser_test_callbacks.add_argument(
        "--callback-type",
        required=False,
        help="Optional: The type of callback to run tests for.",
    )
    parser_test_callbacks.add_argument(
        "--callback-name",
        required=False,
        help="Optional: The name of the callback to run tests for.",
    )
    parser_test_callbacks.add_argument(
        "--log-file",
        required=False,
        help="Optional: Path to a file to log pytest output to.",
    )
    parser_test_callbacks.add_argument(
        "--pytest-args",
        type=lambda s: [item for item in s.split(",")],
        help='Comma-separated list (e.g., "-v,-s")',
    )

    parser_test_callbacks.set_defaults(func=test_callbacks)

    # Parser for 'test-single-callback'
    parser_test_single_callback = subparsers.add_parser(
        "test-single-callback",
        help="Run local callback unit tests against the deployed agent.",
    )
    parser_test_single_callback.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_test_single_callback.add_argument(
        "--agent-name",
        required=True,
        help="Optional: The name of the agent to run callback tests for.",
    )
    parser_test_single_callback.add_argument(
        "--callback-type",
        required=True,
        help="Optional: The type of callback to run tests for.",
    )
    parser_test_single_callback.add_argument(
        "--test-file-path",
        required=True,
        help="Path to the test python file to run.",
    )
    parser_test_single_callback.add_argument(
        "--log-file",
        required=False,
        help="Optional: Path to a file to log pytest output to.",
    )
    parser_test_single_callback.add_argument(
        "--pytest-args",
        type=lambda s: [item for item in s.split(",")],
        help='Comma-separated list (e.g., "-v,-s")',
    )

    parser_test_single_callback.set_defaults(func=test_single_callback)

    # Parser for 'export'
    parser_export = subparsers.add_parser(
        "export", help="Export an evaluation to YAML or JSON format."
    )
    parser_export.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_export.add_argument(
        "--evaluation-id",
        required=True,
        help=(
            "The evaluation resource name "
            "(projects/.../locations/.../apps/.../evaluations/...)."
        ),
    )
    parser_export.add_argument(
        "--format",
        choices=["yaml", "json"],
        default="yaml",
        help="Export format (yaml or json). Defaults to yaml.",
    )
    parser_export.add_argument(
        "--output",
        help=(
            "Path to save the exported evaluation. "
            "If not provided, prints to stdout."
        ),
    )

    parser_export.set_defaults(func=export_eval)

    # Parser for 'push'
    parser_push_eval = subparsers.add_parser(
        "push-eval", help="Push evaluation(s) from a YAML file to the app."
    )
    parser_push_eval.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_push_eval.add_argument(
        "--file",
        required=True,
        help="Path to the YAML file containing evaluation definitions.",
    )
    parser_push_eval.set_defaults(func=push_eval)

    # Parser for 'run'
    parser_run = subparsers.add_parser(
        "run", help="Run an evaluation and assert results."
    )
    parser_run.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_run.add_argument(
        "--evaluation-id",
        required=False,
        help=(
            "The evaluation resource name "
            "(projects/.../locations/.../apps/.../evaluations/...)."
        ),
    )
    parser_run.add_argument(
        "--modality",
        choices=["text", "audio"],
        default="text",
        help="Evaluation execution modality (text or audio). Defaults to text.",
    )
    parser_run.add_argument(
        "--display-name-prefix",
        required=False,
        help="Run all tests whose display name starts with this string.",
    )
    parser_run.add_argument(
        "--tags",
        nargs="+",
        default=[],
        help=(
            "Space-separated list of tags. Runs tests "
            "containing any of these tags."
        ),
    )
    parser_run.add_argument(
        "--wait",
        action="store_true",
        help=(
            "Wait for evaluation to complete and return exit code 0 "
            "on pass or 1 on fail."
        ),
    )
    parser_run.add_argument(
        "--filter-auto-metrics",
        action="store_true",
        help=(
            "Filter out automated metrics (semantic similarity, "
            "hallucination) and only evaluate custom expectations."
        ),
    )

    parser_run.set_defaults(func=run_eval)

    # Parser for 'run-session'
    parser_run_session = subparsers.add_parser(
        "run-session",
        help="Start an interactive text session with the agent.",
    )
    parser_run_session.add_argument(
        "modality",
        choices=["text"],
        help="Modality of the session.",
    )
    parser_run_session.add_argument(
        "app_name",
        help="The app name (projects/.../locations/.../apps/...).",
    )
    parser_run_session.add_argument(
        "--use-tool-fakes",
        action="store_true",
        default=False,
        help="Use fake tools for the session if available.",
    )
    parser_run_session.set_defaults(func=run_session)

    # Parser for 'ci-test'
    parser_ci_test = subparsers.add_parser(
        "ci-test", help="Runs standard integration tests on a temporary agent."
    )
    parser_ci_test.add_argument(
        "--app-dir",
        default=".",
        help=(
            "Path to the app directory to test. Defaults to current directory."
        ),
    )
    parser_ci_test.add_argument(
        "--display-name",
        help=(
            "Optional: Deterministic display name for the temp agent "
            "(e.g. [CI] PR-123). Overwrites existing."
        ),
    )
    parser_ci_test.add_argument(
        "--env-file",
        help=(
            "Path to a specific environment JSON "
            "file to include as environment.json."
        ),
    )
    _add_project_location_args(parser_ci_test)
    parser_ci_test.set_defaults(func=ci_test)

    # Parser for 'delete'
    parser_delete = subparsers.add_parser(
        "delete", help="Deletes a specified agent/app."
    )
    parser_delete.add_argument(
        "--app-name",
        help=(
            "The CXAS App ID (projects/.../locations/.../apps/...). "
            "Required if --display-name not provided."
        ),
    )
    parser_delete.add_argument(
        "--display-name",
        help=(
            "The Display Name of the app to delete. "
            "Required if --app-name not provided."
        ),
    )
    _add_project_location_args(parser_delete, required=False)
    parser_delete.add_argument(
        "--force",
        action="store_true",
        help="Force delete even if there are child resources.",
    )
    parser_delete.set_defaults(func=app_delete)

    # Parser for 'local-test'
    parser_local_test = subparsers.add_parser(
        "local-test", help="Runs the agent tests locally using Docker."
    )
    parser_local_test.add_argument(
        "--app-dir",
        default=".",
        help="Path to the app directory. Defaults to current directory.",
    )
    parser_local_test.add_argument(
        "--env-file",
        help=(
            "Path to a specific environment JSON "
            "file to include as environment.json."
        ),
    )
    _add_project_location_args(parser_local_test)
    parser_local_test.set_defaults(func=local_test)

    # Parser for 'pull'
    parser_pull = subparsers.add_parser(
        "pull", help="Export an app to a local directory."
    )
    parser_pull.add_argument("app", help="App Resource Name or Display Name.")
    parser_pull.add_argument(
        "--target-dir", default=".", help="Directory to extract to."
    )
    parser_pull.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Overwrite existing target directory data with exported data. "
            "Existing resources that do not have a matching display name in "
            "the exported app will be deleted."
        ),
    )
    _add_project_location_args(parser_pull, required=False)
    parser_pull.set_defaults(func=app_pull)

    # Parser for 'push'
    parser_push = subparsers.add_parser(
        "push", help="Import local files back to CXAS."
    )
    parser_push.add_argument(
        "--app-dir", default=".", help="Local app directory."
    )
    parser_push.add_argument(
        "--to", help="Target App Resource Name or Display Name."
    )
    parser_push.add_argument(
        "--env-file",
        help=(
            "Path to a specific environment JSON "
            "file to include as environment.json."
        ),
    )
    parser_push.add_argument(
        "--app-name",
        help="Target App ID to explicitly push to (v1beta API).",
    )
    parser_push.add_argument(
        "--display-name",
        help="Display name for a new App if --to is not provided.",
    )
    _add_project_location_args(parser_push, required=False)
    parser_push.add_argument(
        "--create-version",
        action="store_true",
        help="Create a version after successful push.",
    )
    parser_push.add_argument(
        "--version-description",
        help="Description for the created version.",
    )
    parser_push.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Overwrite existing data with imported data. Existing resources "
            "that do not have a matching display name in the imported app "
            "will be deleted"
        ),
    )
    parser_push.set_defaults(func=app_push)

    # Parser for 'lint'
    parser_lint = subparsers.add_parser(
        "lint",
        help="Lint an app directory for best practices and structural issues.",
    )
    parser_lint.add_argument(
        "--app-dir",
        default=".",
        help="Path to the app directory to lint (default: current directory).",
    )
    parser_lint.add_argument(
        "--fix",
        action="store_true",
        help="Show fix suggestions for each issue.",
    )
    parser_lint.add_argument(
        "--only",
        choices=[
            "instructions",
            "callbacks",
            "tools",
            "evals",
            "config",
            "structure",
            "schema",
        ],
        help="Only run a specific linter category.",
    )
    parser_lint.add_argument(
        "--rule",
        type=str,
        help="Run specific rules only (comma-separated IDs, e.g. I003,C005).",
    )
    parser_lint.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON.",
    )
    parser_lint.add_argument(
        "--list-rules",
        action="store_true",
        help="List all available lint rules.",
    )
    parser_lint.add_argument(
        "--validate-only",
        action="store_true",
        help="Run only structure and config rules.",
    )
    parser_lint.add_argument(
        "--agents",
        help="Only discover/lint specific agents (comma-separated list).",
    )
    parser_lint.add_argument(
        "--tools",
        help="Only discover/lint specific tools (comma-separated list).",
    )
    parser_lint.add_argument(
        "--agent",
        help="Validate a single agent directory against CES schema.",
    )
    parser_lint.add_argument(
        "--tool",
        help="Validate a single tool directory against CES schema.",
    )
    parser_lint.add_argument(
        "--toolset",
        help="Validate a single toolset directory against CES schema.",
    )
    parser_lint.add_argument(
        "--guardrail",
        help="Validate a single guardrail directory against CES schema.",
    )
    parser_lint.add_argument(
        "--evaluation",
        help="Validate a single evaluation directory against CES schema.",
    )
    parser_lint.add_argument(
        "--evaluation-expectations",
        help=(
            "Validate a single evaluation expectations"
            " directory against CES schema."
        ),
    )
    parser_lint.set_defaults(func=app_lint)

    # Parser for 'llm-lint'
    parser_llm_lint = subparsers.add_parser(
        "llm-lint",
        help="Run AI-driven semantic linter on GECX sub-agent instructions.",
    )
    parser_llm_lint.add_argument(
        "--agent-dir",
        required=True,
        help="Path to the sub-agent directory containing instruction.txt.",
    )
    parser_llm_lint.add_argument(
        "--project-id",
        help="GCP Project ID (auto-detected if omitted).",
    )
    parser_llm_lint.add_argument(
        "--location",
        default="us-central1",
        help="GCP location for Vertex AI queries (default: us-central1).",
    )
    parser_llm_lint.add_argument(
        "--model",
        default="gemini-2.5-flash",
        help="Gemini model name to use (default: gemini-2.5-flash).",
    )
    parser_llm_lint.add_argument(
        "--output",
        help="Optional path to write the markdown lint report.",
    )
    parser_llm_lint.set_defaults(func=llm_lint)

    # Parser for 'init'
    parser_init = subparsers.add_parser(
        "init",
        help="Initialize a project with CXAS agent development skills "
        "(.agents, .claude, .gemini, AGENTS.md, etc.).",
    )
    parser_init.add_argument(
        "--target-dir",
        default=".",
        help="Directory to install skills into (default: current directory).",
    )
    parser_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files without prompting.",
    )
    parser_init.set_defaults(func=app_init)

    # Parser for 'create'
    parser_create = subparsers.add_parser("create", help="Create a new app.")
    parser_create.add_argument("name", help="Display name of the new app.")
    parser_create.add_argument(
        "--description", help="Description for the new app."
    )
    parser_create.add_argument(
        "--app-id", help="Optional specific app_id to use."
    )
    _add_project_location_args(parser_create)
    parser_create.set_defaults(func=app_create)

    # Parser for 'branch'
    parser_branch = subparsers.add_parser(
        "branch", help="Branch an app (pull -> create -> push)."
    )
    parser_branch.add_argument(
        "source", help="Source App Resource Name or Display Name."
    )
    parser_branch.add_argument(
        "--new-name", required=True, help="Display name of the new branch app."
    )
    _add_project_location_args(parser_branch)
    parser_branch.set_defaults(func=app_branch)

    # Subparsers for 'apps'
    parser_apps = subparsers.add_parser("apps", help="Manage apps (list, get).")
    apps_subparsers = parser_apps.add_subparsers(
        title="Apps Commands", dest="apps_command", required=True
    )

    parser_apps_list = apps_subparsers.add_parser("list", help="List all apps.")
    _add_project_location_args(parser_apps_list)
    parser_apps_list.set_defaults(func=apps_list)

    parser_apps_get = apps_subparsers.add_parser("get", help="Get app details.")
    parser_apps_get.add_argument(
        "app",
        help="App Resource Name or Display Name.",
    )
    _add_project_location_args(parser_apps_get, required=False)
    parser_apps_get.set_defaults(func=apps_get)

    # Subparsers for 'conversations'
    parser_convs = subparsers.add_parser(
        "conversations", help="Manage conversations (list, get)."
    )
    convs_subparsers = parser_convs.add_subparsers(
        title="Conversations Commands",
        dest="conversations_command",
        required=True,
    )

    parser_convs_list = convs_subparsers.add_parser(
        "list", help="List conversations for an app."
    )
    parser_convs_list.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_convs_list.set_defaults(func=conversations_list)

    parser_convs_get = convs_subparsers.add_parser(
        "get", help="Get conversation details."
    )
    parser_convs_get.add_argument(
        "conversation_resource_name",
        help="The conversation resource name.",
    )
    parser_convs_get.set_defaults(func=conversations_get)

    # Subparsers for 'deployments'
    parser_deps = subparsers.add_parser(
        "deployments", help="Manage deployments (list, create, promote)."
    )
    deps_subparsers = parser_deps.add_subparsers(
        title="Deployments Commands",
        dest="deployments_command",
        required=True,
    )

    parser_deps_list = deps_subparsers.add_parser(
        "list", help="List deployments for an app."
    )
    parser_deps_list.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_deps_list.set_defaults(func=deployments_list)

    parser_deps_create = deps_subparsers.add_parser(
        "create", help="Create a deployment."
    )
    parser_deps_create.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_deps_create.add_argument(
        "--deployment-id",
        required=True,
        help="Deployment ID for create_deployment.",
    )
    parser_deps_create.add_argument(
        "--version-id",
        required=True,
        help="Version ID for create_deployment.",
    )
    parser_deps_create.set_defaults(func=deployments_create)

    parser_deps_promote = deps_subparsers.add_parser(
        "promote", help="Promote app to live traffic."
    )
    parser_deps_promote.add_argument(
        "--app-resource-name",
        required=True,
        help="Fully qualified CXAS app resource name.",
    )
    parser_deps_promote.add_argument(
        "--app-dir",
        required=True,
        help="Path to the CXAS app directory.",
    )
    parser_deps_promote.add_argument(
        "--live-deployment-resource-name",
        required=True,
        help="Fully qualified live deployment resource name.",
    )
    parser_deps_promote.set_defaults(func=deployments_promote)

    # Subparsers for 'local'
    parser_local = subparsers.add_parser(
        "local", help="Local workspace operations."
    )
    local_subparsers = parser_local.add_subparsers(
        title="Local Commands", dest="local_command", required=True
    )

    parser_local_create = local_subparsers.add_parser(
        "create", help="Create local templates for CXAS components."
    )
    local_create_subparsers = parser_local_create.add_subparsers(
        title="Create Local Commands",
        dest="create_local_command",
        required=True,
    )

    parser_local_create_agent = local_create_subparsers.add_parser(
        "agent", help="Create local agent template."
    )
    parser_local_create_agent.add_argument(
        "name", help="Display name of the agent."
    )
    parser_local_create_agent.add_argument(
        "--app-dir", default=".", help="App directory."
    )
    parser_local_create_agent.set_defaults(func=handle_local_create)

    parser_local_create_tool = local_create_subparsers.add_parser(
        "tool", help="Create local tool template."
    )
    parser_local_create_tool.add_argument(
        "name", help="Display name of the tool."
    )
    parser_local_create_tool.add_argument(
        "tool_type", nargs="?", help="Type of tool (e.g., PYTHON)."
    )
    parser_local_create_tool.add_argument(
        "--add-to-agent", nargs="?", help="Agent to add the tool to."
    )
    parser_local_create_tool.add_argument(
        "--app-dir", default=".", help="App directory."
    )
    parser_local_create_tool.set_defaults(func=handle_local_create)

    # Subparsers for 'versions'
    parser_versions = subparsers.add_parser(
        "versions", help="Manage CXAS app versions (list, compare)."
    )
    versions_subparsers = parser_versions.add_subparsers(
        title="Versions Commands", dest="versions_command", required=True
    )

    parser_versions_list = versions_subparsers.add_parser(
        "list", help="List all deployed versions of an app."
    )
    parser_versions_list.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    _add_project_location_args(parser_versions_list, required=False)
    parser_versions_list.set_defaults(func=app_versions_list)

    parser_versions_compare = versions_subparsers.add_parser(
        "compare",
        help="Compare two app versions and generate a human-readable diff.",
    )
    parser_versions_compare.add_argument(
        "--app-name",
        required=True,
        help="The CXAS App ID (projects/.../locations/.../apps/...).",
    )
    parser_versions_compare.add_argument(
        "--source",
        required=True,
        help="Source version ID (e.g., UUID).",
    )
    parser_versions_compare.add_argument(
        "--target",
        required=True,
        help="Target version ID (e.g., UUID).",
    )
    parser_versions_compare.add_argument(
        "--output",
        help="Optional path to save the Markdown/HTML comparison report.",
    )
    parser_versions_compare.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help=(
            "Print detailed line-by-line diff directly to the console using "
            "rich formatting."
        ),
    )
    parser_versions_compare.add_argument(
        "--web",
        action="store_true",
        help=(
            "Force generate a self-contained interactive HTML diff report "
            "instead of console text."
        ),
    )
    _add_project_location_args(parser_versions_compare, required=False)
    parser_versions_compare.set_defaults(func=app_versions_compare)

    # Subparsers for 'tools', 'callbacks', and 'variables'
    register_resources_subparsers(subparsers)

    # Subparsers for 'insights'
    parser_insights = subparsers.add_parser(
        "insights",
        help="Perform high-level CXAS Insights and Quality AI operations.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    populate_insights_parser(parser_insights)

    # Subparsers for 'trace' — observability/debugging for past conversations.
    register_trace_subparser(subparsers)

    return parser


def main() -> None:
    parser = get_parser()
    args = parser.parse_args()

    if getattr(args, "oauth_token", None):
        os.environ["CXAS_OAUTH_TOKEN"] = args.oauth_token

    # Configure logging
    log_level = logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
