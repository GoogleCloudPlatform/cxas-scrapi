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

"""CLI command handlers for deployments."""

from __future__ import annotations

import argparse
import json
import sys
import time

from google.api_core.exceptions import NotFound
from google.protobuf.json_format import MessageToDict

from cxas_scrapi.cli.app import app_push
from cxas_scrapi.core.deployments import Deployments


def deployments_list(args: argparse.Namespace) -> None:
    """Lists deployments for an app."""
    print(f"Listing deployments for App: {args.app_name}")

    deployments_client = Deployments(app_name=args.app_name)
    deployments = deployments_client.list_deployments()

    deployments_dict = []
    for d in deployments:
        try:
            d_dict = MessageToDict(d._pb)
        except AttributeError:
            d_dict = MessageToDict(d)
        deployments_dict.append(d_dict)

    print(json.dumps(deployments_dict, indent=2))


def deployments_create(args: argparse.Namespace) -> None:
    """Creates a deployment for a version."""
    print(
        f"Creating deployment {args.deployment_id} with version "
        f"{args.version_id} for App: {args.app_name}"
    )

    deployments_client = Deployments(app_name=args.app_name)
    deployment = deployments_client.create_deployment(
        deployment_id=args.deployment_id,
        display_name=args.deployment_id,
        app_version=args.version_id,
    )
    print(f"Deployment created successfully: {deployment.name}")


def deployments_promote(args: argparse.Namespace) -> None:
    """Promotes app to live traffic."""
    print(f"Promoting app {args.app_resource_name} to live traffic...")

    # Step 1: Push and create version
    push_args = argparse.Namespace(
        app_dir=args.app_dir,
        to=args.app_resource_name,
        app_name=None,
        display_name=None,
        env_file=None,
        project_id=None,
        location=None,
        create_version=True,
        version_description=f"Promote {time.strftime('%Y%m%d%H%M%S')}",
    )

    try:
        print("Calling app_push directly...")
        app_name = app_push(push_args)
        if not app_name:
            print("Error: Push failed during promotion.")
            sys.exit(1)

        version_id = getattr(push_args, "created_version_name", None)
        if not version_id:
            print("Error: Could not get created version ID.")
            sys.exit(1)

        # Step 2: Update deployment
        deployment_id = args.live_deployment_resource_name.split(
            "/deployments/"
        )[-1]

        deployments_client = Deployments(app_name=args.app_resource_name)

        try:
            deployments_client.get_deployment(deployment_id=deployment_id)
        except NotFound:
            print(f"Error: Deployment '{deployment_id}' does not exist.")
            print(
                "`deployments promote` requires "
                "promoting an existing deployment."
            )
            print(
                "Please create the deployment first using `deployments create`."
            )
            sys.exit(1)

        print(
            f"Updating deployment {deployment_id} with version {version_id}..."
        )
        deployments_client.update_deployment(
            deployment_id=deployment_id, app_version=version_id
        )

        print("Successfully promoted agent to live traffic.")

    except Exception as e:
        print(f"Error during promotion: {e}")
        sys.exit(1)
