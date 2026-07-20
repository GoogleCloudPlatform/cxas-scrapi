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

"""CLI command handlers for conversation history."""


import json
import sys
from dataclasses import dataclass
from typing import Any

import click
from google.protobuf.json_format import MessageToDict

from cxas_scrapi.cli.utils import LazyCallable, to_dataclass

Apps = LazyCallable("cxas_scrapi.core.apps", "Apps")
Common = LazyCallable("cxas_scrapi.core.common", "Common")
ConversationHistory = LazyCallable(
    "cxas_scrapi.core.conversation_history", "ConversationHistory"
)


@dataclass(frozen=False)
class ConversationsListConfig:
    """Configuration for listing conversations.

    Args:
        app_name: App resource name.
        app: App resource name alias.
    """

    app_name: str | None = None
    app: str | None = None


@dataclass(frozen=False)
class ConversationsGetConfig:
    """Configuration for getting conversation details.

    Args:
        app_name: App resource name.
        conversation_id: Conversation ID.
        conversation_resource_name: Full conversation resource name.
    """

    app_name: str | None = None
    conversation_id: str | None = None
    conversation_resource_name: str | None = None


def conversations_list(config: ConversationsListConfig | Any) -> None:
    """Lists conversations for an app.

    Args:
        config: Conversations list configuration object or arguments namespace.
    """
    cfg = to_dataclass(ConversationsListConfig, config)
    target_app = cfg.app_name or cfg.app
    print(f"Listing conversations for App: {target_app}")

    # Extract and validate app_name
    app_name = Common._get_app_name(target_app)
    if not app_name:
        print(
            "Error: Invalid App Name format. Please use the full resource "
            "name in the format 'projects/.../locations/.../apps/...'"
        )
        sys.exit(1)

    project_id = Common._get_project_id(target_app)
    location = Common._get_location(target_app)

    apps_client = Apps(project_id=project_id, location=location)
    ch_client = ConversationHistory(
        app_name=target_app, creds=apps_client.creds
    )
    conversations = ch_client.list_conversations()

    conversations_dict = []
    for c in conversations:
        try:
            c_dict = MessageToDict(c._pb)
        except AttributeError:
            c_dict = MessageToDict(c)
        conversations_dict.append(c_dict)

    print(json.dumps(conversations_dict, indent=2))


def conversations_get(config: ConversationsGetConfig | Any) -> None:
    """Gets details of a specific conversation.

    Args:
        config: Conversations get configuration object or arguments namespace.
    """
    cfg = to_dataclass(ConversationsGetConfig, config)
    target_conv = cfg.conversation_resource_name or cfg.conversation_id
    print(f"Getting conversation: {target_conv}")

    # Extract and validate app_name
    app_name = Common._get_app_name(target_conv)
    if not app_name:
        print(
            "Error: Invalid Conversation Resource Name format. Please use the "
            "full resource name in the format "
            "'projects/.../locations/.../apps/.../conversations/...'"
        )
        sys.exit(1)

    project_id = Common._get_project_id(target_conv)
    location = Common._get_location(target_conv)

    apps_client = Apps(project_id=project_id, location=location)
    ch_client = ConversationHistory(app_name=app_name, creds=apps_client.creds)
    conv = ch_client.get_conversation(conversation_id=target_conv)

    try:
        conv_dict = MessageToDict(conv._pb)
    except AttributeError:
        conv_dict = MessageToDict(conv)

    print(json.dumps(conv_dict, indent=2))


@click.group(name="conversations")
def conversations_group() -> None:
    """Manage conversation history."""


@conversations_group.command(name="list")
@click.option("--app-name", "-a", required=False, help="App resource name.")
@click.pass_context
def conversations_list_cmd(ctx: click.Context, **kwargs: Any) -> None:
    """List conversation histories."""
    cfg = to_dataclass(ConversationsListConfig, ctx, **kwargs)
    conversations_list(cfg)
