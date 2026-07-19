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
# distributed under the License is distributed on an "AS IS" BASIS, # WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI command handlers for deployments."""

import json
import sys
import time
from dataclasses import dataclass
from typing import Any

import click
from google.protobuf.json_format import MessageToDict

from cxas_scrapi.cli.utils import LazyCallable, to_dataclass

Apps = LazyCallable("cxas_scrapi.core.apps", "Apps")
Common = LazyCallable("cxas_scrapi.core.common", "Common")
Deployments = LazyCallable("cxas_scrapi.core.deployments", "Deployments")


@dataclass(frozen=False)
class DeploymentsListConfig:
    """Configuration for listing deployments.

    Args:
        app_name: App resource name.
    """

    app_name: str


@dataclass(frozen=False)
class DeploymentsCreateConfig:
    """Configuration for creating deployments.

    Args:
        app_name: App resource name.
        deployment_id: Deployment ID.
        traffic_split: Traffic split configuration string.
        version: App version name.
        version_id: App version ID.
        display_name: Display name for the deployment.
        channel_type: Channel type (e.g. API).
    """

    app_name: str
    deployment_id: str
    traffic_split: str | None = None
    version: str | None = None
    version_id: str | None = None
    display_name: str | None = None
    channel_type: str | None = None


@dataclass(frozen=False)
class DeploymentsPromoteConfig:
    """Configuration for promoting deployments.

    Args:
        app_dir: Path to the CXAS app directory.
        app_resource_name: Fully qualified CXAS app resource name.
        deployment_id: Deployment ID.
        traffic_split: Traffic split configuration string.
        to: Target resource name for app push.
        version: Version ID to promote.
        app_name: The CXAS App ID.
        live_deployment_resource_name: Fully qualified live deployment resource name.
    """

    app_dir: str = "."
    app_resource_name: str | None = None
    deployment_id: str | None = None
    traffic_split: str | None = None
    to: str | None = None
    version: str | None = None
    app_name: str | None = None
    live_deployment_resource_name: str | None = None


def deployments_list(config: DeploymentsListConfig | Any) -> None:
    """Lists deployments for an app.

    Args:
        config: Deployments list configuration object or arguments namespace.
    """
    args = to_dataclass(DeploymentsListConfig, config)
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


def deployments_create(config: DeploymentsCreateConfig | Any) -> None:
    """Creates a deployment.

    Args:
        config: Deployments create configuration object or arguments namespace.
    """
    args = to_dataclass(DeploymentsCreateConfig, config)
    print(f"Creating deployment {args.deployment_id} for App: {args.app_name}")

    traffic_split = None
    if getattr(args, "traffic_split", None):
        try:
            split_parts = args.traffic_split.split(",")
            traffic_split = {}
            for part in split_parts:
                k, v = part.split(":")
                traffic_split[k] = int(v)
        except Exception as e:
            print(f"Error parsing traffic-split: {e}")
            sys.exit(1)

    version_id = getattr(args, "version", None) or getattr(
        args, "version_id", None
    )
    if not version_id and not traffic_split:
        print(
            "Error: You must provide either `--version` (or `--version-id`)"
            " OR `--traffic-split`."
        )
        sys.exit(1)

    display_name = getattr(args, "display_name", None) or args.deployment_id
    channel_type = getattr(args, "channel_type", None) or "API"

    deployments_client = Deployments(app_name=args.app_name)

    deployment = deployments_client.create_deployment(
        deployment_id=args.deployment_id,
        display_name=display_name,
        app_version=version_id,
        channel_type=channel_type,
        traffic_split=traffic_split,
    )
    print(f"Deployment created successfully: {deployment.name}")


def deployments_promote(config: DeploymentsPromoteConfig | Any) -> None:
    """Promotes app deployment to production or updates traffic split."""
    args = to_dataclass(DeploymentsPromoteConfig, config)
    from cxas_scrapi.cli.app import AppPushConfig, PushResult, app_push

    has_id = bool(args.deployment_id)
    has_split_or_ver = bool(
        args.traffic_split or args.version or getattr(args, "version_id", None)
    )
    if has_id and has_split_or_ver:
        # Direct traffic update logic matching current code using args...
        project_id = Common._get_project_id(args.deployment_id)
        location = Common._get_location(args.deployment_id)
        apps_client = Apps(project_id=project_id, location=location)
        deployments_client = Deployments(
            app_name=args.app_name or getattr(args, "app_resource_name", None),
            creds=apps_client.creds,
        )
        kwargs: dict[str, Any] = {}
        if args.version:
            kwargs["version_name"] = args.version
        if args.traffic_split:
            kwargs["traffic_split"] = args.traffic_split
        try:
            deployments_client.update_deployment(
                deployment_id=args.deployment_id, **kwargs
            )
            print("Successfully updated deployment traffic.")
            return
        except Exception as e:
            print(f"Error updating deployment: {e}")
            sys.exit(1)

    if not all(
        [
            getattr(args, "app_resource_name", None),
            getattr(args, "app_dir", None),
            getattr(args, "live_deployment_resource_name", None),
        ]
    ):
        print(
            "Error: Missing required arguments. "
            "You must provide either `--deployment-id` with"
            " `--version`/`--traffic-split`, OR the legacy arguments: "
            "`--app-resource-name`, `--app-dir`, and "
            "`--live-deployment-resource-name`."
        )
        sys.exit(1)

    print(f"Promoting app {args.app_resource_name} to live traffic...")
    push_config = AppPushConfig(
        app_dir=args.app_dir,
        to=args.app_resource_name,
        create_version=True,
        version_description=f"Promote {time.strftime('%Y%m%d%H%M%S')}",
    )
    try:
        print("Calling app_push directly...")
        push_res = app_push(push_config)
        if isinstance(push_res, PushResult):
            app_name = push_res.app_name
            version_id = push_res.created_version_name
        else:
            app_name = push_res
            version_id = getattr(push_config, "created_version_name", None)

        if not app_name:
            print("Error: Push failed during promotion.")
            sys.exit(1)
        if not version_id:
            print("Error: No version created during promotion push.")
            sys.exit(1)

        project_id = Common._get_project_id(args.live_deployment_resource_name)
        location = Common._get_location(args.live_deployment_resource_name)
        apps_client = Apps(project_id=project_id, location=location)
        deployments_client = Deployments(
            app_name=args.app_resource_name, creds=apps_client.creds
        )
        deployments_client.update_deployment(
            deployment_id=args.live_deployment_resource_name,
            version_name=version_id,
        )
        print(f"Successfully promoted version {version_id} to live deployment.")
    except Exception as e:
        print(f"Error during promotion: {e}")
        sys.exit(1)


@click.group(name="deployments")
def deployments_group() -> None:
    """Manage remote app deployments."""


@deployments_group.command(name="list")
@click.option("--app-name", "-a", required=True, help="App resource name.")
@click.pass_context
def deployments_list_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """List deployments for an app."""
    args = to_dataclass(DeploymentsListConfig, ctx, **kwargs)
    deployments_list(args)


@deployments_group.command(name="create")
@click.option("--app-name", "-a", required=True, help="App resource name.")
@click.option("--deployment-id", required=True, help="Deployment ID.")
@click.option(
    "--traffic-split",
    required=False,
    help="Traffic split configuration string.",
)
@click.option("--version", required=False, help="Version name.")
@click.option("--version-id", required=False, help="Version ID.")
@click.option("--display-name", required=False, help="Display name.")
@click.option("--channel-type", required=False, help="Channel type.")
@click.pass_context
def deployments_create_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Create a deployment."""
    args = to_dataclass(DeploymentsCreateConfig, ctx, **kwargs)
    deployments_create(args)


@deployments_group.command(name="promote")
@click.option("--app-dir", default=".", help="Path to CXAS app directory.")
@click.option(
    "--app-resource-name",
    required=False,
    help="Fully qualified CXAS app resource name.",
)
@click.option("--deployment-id", required=False, help="Deployment ID.")
@click.option(
    "--traffic-split",
    required=False,
    help="Traffic split configuration string.",
)
@click.option("--to", required=False, help="Target resource name.")
@click.option("--version", required=False, help="Version ID to promote.")
@click.option("--app-name", required=False, help="CXAS App ID.")
@click.option(
    "--live-deployment-resource-name",
    required=False,
    help="Fully qualified live deployment resource name.",
)
@click.pass_context
def deployments_promote_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """Promote app deployment to production or update traffic split."""
    args = to_dataclass(DeploymentsPromoteConfig, ctx, **kwargs)
    deployments_promote(args)
