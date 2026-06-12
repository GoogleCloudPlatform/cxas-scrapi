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

from __future__ import annotations

import argparse
import json
import sys

from google.protobuf.json_format import MessageToDict

from cxas_scrapi.core.apps import Apps
from cxas_scrapi.core.common import Common
from cxas_scrapi.core.conversation_history import ConversationHistory


def conversations_list(args: argparse.Namespace) -> None:
    """Lists conversations for an app."""
    print(f"Listing conversations for App: {args.app_name}")

    # Extract and validate app_name
    app_name = Common._get_app_name(args.app_name)
    if not app_name:
        print(
            "Error: Invalid App Name format. Please use the full resource "
            "name in the format 'projects/.../locations/.../apps/...'"
        )
        sys.exit(1)

    project_id = Common._get_project_id(args.app_name)
    location = Common._get_location(args.app_name)

    apps_client = Apps(project_id=project_id, location=location)
    ch_client = ConversationHistory(
        app_name=args.app_name, creds=apps_client.creds
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


def conversations_get(args: argparse.Namespace) -> None:
    """Gets details of a specific conversation."""
    print(f"Getting conversation: {args.conversation_resource_name}")

    # Extract and validate app_name
    app_name = Common._get_app_name(args.conversation_resource_name)
    if not app_name:
        print(
            "Error: Invalid Conversation Resource Name format. Please use the "
            "full resource name in the format "
            "'projects/.../locations/.../apps/.../conversations/...'"
        )
        sys.exit(1)

    project_id = Common._get_project_id(args.conversation_resource_name)
    location = Common._get_location(args.conversation_resource_name)

    apps_client = Apps(project_id=project_id, location=location)
    ch_client = ConversationHistory(app_name=app_name, creds=apps_client.creds)
    conv = ch_client.get_conversation(
        conversation_id=args.conversation_resource_name
    )

    try:
        conv_dict = MessageToDict(conv._pb)
    except AttributeError:
        conv_dict = MessageToDict(conv)

    print(json.dumps(conv_dict, indent=2))
