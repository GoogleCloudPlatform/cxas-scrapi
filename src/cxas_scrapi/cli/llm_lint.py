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
"""CLI subcommand for AI-driven semantic linter on GECX instructions."""

import argparse
import ast
import json
import os
import sys
from pathlib import Path

from cxas_scrapi.prompts import LLM_LINT_SYSTEM_PROMPT, LLM_LINT_USER_PROMPT
from cxas_scrapi.utils.gemini import GeminiGenerate


def discover_agent_callbacks(agent_dir: Path) -> list[tuple[str, Path]]:
    """Discovers all dynamic instruction callbacks for a given agent directory.

    Returns:
        A list of tuples: (callback_display_name, callback_file_path)
    """
    callbacks = []
    callback_types = [
        "before_agent_callbacks",
        "after_agent_callbacks",
        "before_model_callbacks",
        "after_model_callbacks",
    ]
    for cb_type in callback_types:
        cb_dir = agent_dir / cb_type
        if cb_dir.exists() and cb_dir.is_dir():
            for item in sorted(cb_dir.iterdir()):
                if item.is_dir():
                    code_file = item / "python_code.py"
                    if code_file.exists():
                        display_name = f"{cb_type}/{item.name}"
                        callbacks.append((display_name, code_file))
    return callbacks


def get_callback_output_path(base_path: Path, callback_name: str) -> Path:
    """Generates output file path for a callback's lint report."""
    clean_cb_name = callback_name.replace("/", "_")
    if base_path.suffix:
        return base_path.with_name(
            f"{base_path.stem}_{clean_cb_name}{base_path.suffix}"
        )
    return base_path.with_name(f"{base_path.name}_{clean_cb_name}.md")


def extract_dynamic_instructions(file_path: Path) -> dict[str, str]:
    """Parses Python file to extract state -> instruction dictionary mappings.

    Focuses only on top-level (module-level) constant dictionaries where the
    variable name includes "instruction" (case-insensitive).
    """
    dynamic_instructions = {}
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    var_name = target.id
                    if "instruction" in var_name.lower():
                        if isinstance(node.value, ast.Dict):
                            keys = node.value.keys
                            values = node.value.values
                            for k, v in zip(keys, values, strict=True):
                                if isinstance(k, ast.Constant) and isinstance(
                                    v, ast.Constant
                                ):
                                    if isinstance(k.value, str) and isinstance(
                                        v.value, str
                                    ):
                                        dynamic_instructions[k.value] = v.value
    except Exception as e:
        print(
            f"Warning: Failed to parse AST of {file_path}: {e}", file=sys.stderr
        )
    return dynamic_instructions


def resolve_gcp_credentials(
    agent_dir: Path,
    cli_project_id: str | None = None,
    cli_location: str | None = None,
) -> tuple[str, str]:
    """Resolves the GCP project ID and location using multiple fallback methods.

    Checks:
    1. Explicit CLI arguments.
    2. Standard environment variables.
    3. Walk up from agent directory to locate gecx-config.json.
    4. Root level gecx-config.json.

    Args:
        agent_dir: Path to the agent directory.
        cli_project_id: Project ID from CLI args if provided.
        cli_location: Location/region from CLI args if provided.

    Returns:
        A tuple containing (project_id, location).
    """
    project_id = cli_project_id
    location = cli_location

    # 2. Check environment variables
    if not project_id:
        project_id = os.environ.get("PROJECT_ID") or os.environ.get(
            "GOOGLE_CLOUD_PROJECT"
        )
    if not location:
        location = os.environ.get("LOCATION") or os.environ.get("REGION")

    # 3. Search for gecx-config.json by walking up from the agent directory
    current_dir = agent_dir.resolve()
    while current_dir != current_dir.parent:
        config_path = current_dir / "gecx-config.json"
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    config_data = json.load(f)
                    if not project_id:
                        project_id = config_data.get("gcp_project_id")
                    if not location:
                        location = config_data.get("location")
            except (json.JSONDecodeError, OSError) as e:
                print(
                    f"Warning: Failed to parse config at {config_path}: {e}",
                    file=sys.stderr,
                )
            break
        current_dir = current_dir.parent

    # 4. Try default fallback paths if still not resolved
    if not project_id or not location:
        # Walk up from current working directory to find root level config
        repo_root = Path.cwd()
        while repo_root != repo_root.parent:
            repo_config = repo_root / "gecx-config.json"
            if repo_config.exists():
                try:
                    with open(repo_config, encoding="utf-8") as f:
                        config_data = json.load(f)
                        if not project_id:
                            project_id = config_data.get("gcp_project_id")
                        if not location:
                            location = config_data.get("location")
                except (json.JSONDecodeError, OSError):
                    pass
                break
            repo_root = repo_root.parent

    # Standard GECX default location if still None
    if not location or location == "<YOUR_GCP_REGION>":
        location = "us-central1"

    if not project_id or project_id == "<YOUR_GCP_PROJECT_ID>":
        print(
            "Error: GCP Project ID could not be resolved. Please provide "
            "either --project-id, set PROJECT_ID environment variable, "
            "or configure gecx-config.json in your project directory.",
            file=sys.stderr,
        )
        sys.exit(1)

    return project_id, location


def llm_lint(args: argparse.Namespace) -> None:
    """Executes the main linter flow."""
    agent_path = Path(args.agent_dir)

    if not agent_path.exists():
        print(
            f"Error: Agent directory '{args.agent_dir}' does not exist.",
            file=sys.stderr,
        )
        sys.exit(1)

    instruction_file = agent_path / "instruction.txt"
    if not instruction_file.exists():
        print(
            f"Error: Could not find instruction.txt in '{args.agent_dir}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Search for global_instruction.txt by walking up from the agent directory
    global_instruction_content = None
    global_instruction_file = None
    current_dir = agent_path.resolve()
    while current_dir != current_dir.parent:
        p = current_dir / "global_instruction.txt"
        if p.exists():
            global_instruction_file = p
            break
        if (
            (current_dir / "app.json").exists()
            or (current_dir / "app.yaml").exists()
            or (current_dir / "gecx-config.json").exists()
        ):
            # Stop walking up once we hit app root markers
            p = current_dir / "global_instruction.txt"
            if p.exists():
                global_instruction_file = p
            break
        current_dir = current_dir.parent

    print("------------------------------------------------------------")
    print(f"LLM LINTER — Starting analysis for agent: {agent_path.name}")
    print("------------------------------------------------------------")

    # Resolve Credentials
    project_id, location = resolve_gcp_credentials(
        agent_path, args.project_id, args.location
    )
    print(f"GCP Project : {project_id}")
    print(f"GCP Location: {location}")
    print(f"Gemini Model: {args.model}")
    if global_instruction_file:
        print(f"Global Inst : {global_instruction_file.resolve()}")

    # Load instruction content
    try:
        with open(instruction_file, encoding="utf-8") as f:
            instruction_content = f.read()
    except OSError as e:
        print(f"Error reading instruction.txt: {e}", file=sys.stderr)
        sys.exit(1)

    if not instruction_content.strip():
        print(
            f"Warning: instruction.txt in '{args.agent_dir}' is empty.",
            file=sys.stderr,
        )
        sys.exit(0)

    # Load global instruction content if found
    if global_instruction_file:
        try:
            with open(global_instruction_file, encoding="utf-8") as f:
                global_instruction_content = f.read()
        except OSError as e:
            print(
                f"Warning: Failed to read global_instruction.txt: {e}",
                file=sys.stderr,
            )

    # Initialize Gemini
    print("Initializing Gemini Client...")
    gemini_client = GeminiGenerate(
        project_id=project_id,
        location=location,
        model_name=args.model,
    )

    callbacks = discover_agent_callbacks(agent_path)
    if callbacks:
        print(
            f"Found {len(callbacks)} dynamic instruction "
            "callback(s) to analyze."
        )

    # --- Run Base Lint ---
    user_prompt = LLM_LINT_USER_PROMPT.format(
        global_instruction_content=global_instruction_content or "",
        instruction_content=instruction_content,
        dynamic_instruction_content="",
    )

    print(
        "Running semantic review for base instructions using Gemini "
        "(this may take a few seconds)..."
    )
    report = gemini_client.generate(
        prompt=user_prompt,
        system_prompt=LLM_LINT_SYSTEM_PROMPT,
    )

    if not report:
        print("Error: Failed to generate report from Gemini.", file=sys.stderr)
        sys.exit(1)

    print("\n============================================================")
    print("LINT REPORT GENERATED (Base)")
    print("============================================================\n")
    print(report)

    # Determine base output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = agent_path / "llm_lint_report.md"

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)
        print("\n------------------------------------------------------------")
        print(f"Successfully saved base report to: {output_path}")
        print("------------------------------------------------------------")
    except OSError as e:
        print(
            f"Warning: Failed to save base report file to {output_path}: {e}",
            file=sys.stderr,
        )

    # --- Run Callback Lints ---
    for cb_display, cb_path in callbacks:
        print(f"\nAnalyzing dynamic instruction callback: {cb_display}...")

        # Attempt to extract state -> instruction dictionary mapping
        dynamic_dis = extract_dynamic_instructions(cb_path)

        if dynamic_dis:
            is_recommended_cb = cb_display.startswith("before_agent_callbacks/")
            if not is_recommended_cb:
                warning_msg = (
                    "Warning: Dynamic instructions found in "
                    f"'{cb_display}'.\n"
                    "The recommended place to include dynamic instructions "
                    "is only in 'before_agent_callbacks'. Please move "
                    "these dynamic instructions to a "
                    "'before_agent_callbacks' callback."
                )
                print(warning_msg, file=sys.stderr)

            print(
                f"Found {len(dynamic_dis)} dynamic state "
                "instruction(s) inside callback."
            )
            for state_name, state_instruction in sorted(dynamic_dis.items()):
                print(f"  - Linting state: {state_name}...")

                user_prompt = LLM_LINT_USER_PROMPT.format(
                    global_instruction_content=global_instruction_content or "",
                    instruction_content=instruction_content,
                    dynamic_instruction_content=state_instruction,
                )

                cb_report = gemini_client.generate(
                    prompt=user_prompt,
                    system_prompt=LLM_LINT_SYSTEM_PROMPT,
                )

                if cb_report:
                    if not is_recommended_cb:
                        warning_md = (
                            "> [!WARNING]\n"
                            "> **LINTER WARNING**: Dynamic instructions "
                            f"are declared in `{cb_display}`.\n"
                            "> The recommended place to include dynamic "
                            "instructions is only in "
                            "`before_agent_callbacks`.\n"
                            "> Please move these dynamic instructions to a "
                            "`before_agent_callbacks` callback.\n\n"
                        )
                        cb_report = warning_md + cb_report

                    # Use f"{cb_display}/{state_name}" so
                    # get_callback_output_path replaces slashes properly
                    cb_output_path = get_callback_output_path(
                        output_path, f"{cb_display}/{state_name}"
                    )
                    try:
                        with open(cb_output_path, "w", encoding="utf-8") as f:
                            f.write(cb_report)
                        print(
                            "    Successfully saved state report to: "
                            f"{cb_output_path}"
                        )
                    except OSError as e:
                        print(
                            "    Warning: Failed to save state report to "
                            f"{cb_output_path}: {e}",
                            file=sys.stderr,
                        )
                else:
                    print(
                        "    Error: Failed to generate report from Gemini "
                        f"for state: {state_name}",
                        file=sys.stderr,
                    )
