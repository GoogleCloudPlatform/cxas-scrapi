"""CLI module for handling Insights Operations."""

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

import argparse
import sys
import tempfile

import cxas_scrapi.utils.scorecard_template_manager as template_manager
from cxas_scrapi.core.scorecards import Scorecards
from cxas_scrapi.utils.insights_utils import InsightsUtils


def _get_project_and_location_from_parent(parent: str) -> tuple[str, str]:
    """Helper to extract project and location from parent string."""
    parts = parent.split("/")
    if len(parts) < 4 or parts[0] != "projects" or parts[2] != "locations":
        print(
            f"Error: Invalid parent format: {parent}. "
            f"Expected projects/PROJ/locations/LOC"
        )
        sys.exit(1)
    return parts[1], parts[3]


def handle_list(args: argparse.Namespace) -> None:
    """Handles the 'insights list' command."""
    print(f"Listing scorecards in: {args.parent}")
    project_id, location = _get_project_and_location_from_parent(args.parent)
    scorecards_client = Scorecards(project_id=project_id, location=location)

    try:
        scorecards = scorecards_client.list_scorecards(parent=args.parent)
        for s in scorecards:
            print(f"Scorecard: {s['name']} ({s.get('displayName', 'N/A')})")
    except Exception as e:
        print(f"Failed to list scorecards: {e}")
        sys.exit(1)


def handle_export(args: argparse.Namespace) -> None:
    """Handles the 'insights export' command."""
    print(f"Exporting scorecard {args.scorecard_name} to {args.template}")
    # Extract project/location from the full scorecard name.
    # Format: projects/PROJ/locations/LOC/qaScorecards/ID
    project_id, location = _get_project_and_location_from_parent(
        args.scorecard_name
    )
    scorecards_client = Scorecards(project_id=project_id, location=location)

    try:
        # Fetch the scorecard wrapper metadata
        scorecard = scorecards_client.get_scorecard(args.scorecard_name)

        # Fetch the latest revision to get the questions
        latest_revision_name = f"{args.scorecard_name}/revisions/latest"
        questions = scorecards_client.list_questions(latest_revision_name)

        # We explicitly list the fields to export, identical to the old behavior
        fields_to_export = (
            "order",
            "questionType",
            "questionMedium",
            "qaQuestionDataOptions",
            "questionBody",
            "answerChoices",
            "answerInstructions",
        )

        template_manager.save_scorecard_template(
            scorecard, questions, args.template, fields_to_export
        )
        print(f"Successfully exported to {args.template}")

    except Exception as e:
        print(f"Failed to export scorecard: {e}")
        sys.exit(1)


def handle_import(args: argparse.Namespace) -> None:
    """Handles the 'insights import' command."""
    if not args.scorecard_name and not args.parent:
        print(
            "Error: Must provide either --scorecard_name or --parent for "
            "import."
        )
        sys.exit(1)

    target_id = None
    if args.scorecard_name:
        project_id, location = _get_project_and_location_from_parent(
            args.scorecard_name
        )
        target_id = args.scorecard_name.split("/")[-1]
    else:
        project_id, location = _get_project_and_location_from_parent(
            args.parent
        )

    print(f"Importing scorecard template from {args.template}")
    utils_client = InsightsUtils(project_id=project_id, location=location)

    try:
        scorecard_dict, questions = template_manager.load_scorecard_template(
            args.template
        )

        target_revision = utils_client.import_scorecard(
            scorecard_dict=scorecard_dict,
            questions=questions,
            target_scorecard_id=target_id,
        )
        print(f"Successfully imported template to revision: {target_revision}")

    except Exception as e:
        print(f"Failed to import scorecard: {e}")
        sys.exit(1)


def handle_copy(args: argparse.Namespace) -> None:
    """Handles the 'insights copy' command."""
    if not args.dst_scorecard_name and not args.parent:
        print(
            "Error: Must provide either --dst_scorecard_name or --parent "
            "for destination."
        )
        sys.exit(1)

    print(f"Copying scorecard from {args.scorecard_name}")
    # Run an export to a temp file, then import it
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w") as tmp_file:
        template_path = tmp_file.name

        # Shim the args for the export internal call
        export_args = argparse.Namespace()
        export_args.scorecard_name = args.scorecard_name
        export_args.template = template_path
        handle_export(export_args)

        # Shim the args for the import internal call
        import_args = argparse.Namespace()
        import_args.scorecard_name = args.dst_scorecard_name
        import_args.parent = args.parent
        import_args.template = template_path
        handle_import(import_args)

    print("Successfully completed copy operation.")


def populate_insights_parser(parser_insights: argparse.ArgumentParser) -> None:
    """Populates the provided insights parser with its subcommands."""

    # Require an operation (list, export, import, copy)
    insights_subparsers = parser_insights.add_subparsers(
        title="Insights Operations", dest="insights_command", required=True
    )

    # 2. 'list-scorecards' subcommand
    parser_list = insights_subparsers.add_parser(
        "list-scorecards", help="List all QA Scorecards under a parent."
    )
    parser_list.add_argument(
        "--parent",
        required=True,
        help="Parent resource name (e.g. projects/*/locations/*).",
    )
    parser_list.set_defaults(func=handle_list)

    # 3. 'export-scorecard-from-insights' subcommand
    parser_export = insights_subparsers.add_parser(
        "export-scorecard-from-insights",
        help="Export a Scorecard and its questions to a JSON/YAML template.",
    )
    parser_export.add_argument(
        "--scorecard_name",
        required=True,
        help="Full resource name of the scorecard (e.g. "
        "projects/*/locations/*/qaScorecards/*).",
    )
    parser_export.add_argument(
        "--template",
        required=True,
        help="Local path for the scorecard template file (.json, .yaml).",
    )
    parser_export.set_defaults(func=handle_export)

    # 4. 'import-scorecard-to-insights' subcommand
    parser_import = insights_subparsers.add_parser(
        "import-scorecard-to-insights",
        help="Import a JSON/YAML template as an editable SDK Scorecard "
        "revision.",
    )
    parser_import.add_argument(
        "--template",
        required=True,
        help="Local path to the scorecard template file (.json, .yaml).",
    )
    parser_import.add_argument(
        "--scorecard_name",
        help="Optional: Full resource name of an existing scorecard to "
        "overwrite. If omitted, --parent must be provided.",
    )
    parser_import.add_argument(
        "--parent",
        help="Optional: Parent resource name (projects/*/locations/*) to "
        "create a brand new scorecard under. If omitted, --scorecard_name "
        "must be provided.",
    )
    parser_import.set_defaults(func=handle_import)

    # 5. 'copy-scorecard' subcommand
    parser_copy = insights_subparsers.add_parser(
        "copy-scorecard",
        help="Copy a Scorecard's questions into a new destination Scorecard.",
    )
    parser_copy.add_argument(
        "--scorecard_name",
        required=True,
        help="Full resource name of the SOURCE scorecard.",
    )
    parser_copy.add_argument(
        "--dst_scorecard_name",
        help="Optional: Full resource name of the DESTINATION scorecard to "
        "overwrite. If omitted, --parent must be provided.",
    )
    parser_copy.add_argument(
        "--parent",
        help="Optional: Parent resource name to create a brand new copied "
        "scorecard under. If omitted, --dst_scorecard_name must be "
        "provided.",
    )
    parser_copy.set_defaults(func=handle_copy)
