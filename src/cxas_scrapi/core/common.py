"""Common utilities and auth for CX Agent Studio classes."""

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

import hashlib
import importlib.metadata
import os
import re
from typing import Any, Dict, List, Optional

from google.api_core.gapic_v1.client_info import ClientInfo
from google.auth import default
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from proto.marshal.collections import maps, repeated

# Define global scopes used for CX Agent Studio Requests
GLOBAL_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
]


class Common:
    """Core Class for managing Auth and shared functions in CX Agent Studio."""

    def __init__(
        self,
        creds_path: str = None,
        creds_dict: Dict[str, str] = None,
        creds: Any = None,
        scope: List[str] = None,
        app_name: str = None,  # Optional: used to determine client_options
    ):
        self.scopes = GLOBAL_SCOPES
        if scope:
            self.scopes += scope

        oauth_token = os.environ.get("CXAS_OAUTH_TOKEN")

        if creds:
            self.creds = creds
            if hasattr(self.creds, "refresh"):
                try:
                    self.creds.refresh(Request())
                except Exception:
                    pass
            self.token = getattr(self.creds, "token", None)

        elif creds_path:
            self.creds = service_account.Credentials.from_service_account_file(
                creds_path, scopes=self.scopes
            )
            self.creds.refresh(Request())
            self.token = self.creds.token

        elif creds_dict:
            self.creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=self.scopes
            )
            self.creds.refresh(Request())
            self.token = self.creds.token

        elif oauth_token:
            self.creds = Credentials(token=oauth_token)
            self.token = oauth_token

        else:
            self.creds, _ = default(scopes=self.scopes)
            self.creds.refresh(Request())
            self.token = self.creds.token

        self.app_name = app_name

        # Calculate standard client options if app_name/resource provided
        self.client_options = None
        if app_name:
            self.client_options = self._get_client_options(app_name)
            self.project_id = self._get_project_id(app_name)
            self.location = self._get_location(app_name)
        else:
            self.project_id = None
            self.location = None

        try:
            sdk_version = importlib.metadata.version("cxas-scrapi")
        except importlib.metadata.PackageNotFoundError:
            sdk_version = "unknown"

        self.client_info = ClientInfo(user_agent=f"cxas-scrapi/{sdk_version}")

    @staticmethod
    def empty_to_dict(v: Any) -> Any:
        return v if v is not None else {}

    @staticmethod
    def empty_to_list(v: Any) -> Any:
        return v if v is not None else []

    @staticmethod
    def sanitize_expectation_id(text: str) -> str:
        """Creates a safe ID from the text (MD5 hash)."""
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _get_client_options(resource_name: str) -> Dict[str, str]:
        """Determine API endpoint based on region."""
        if not resource_name:
            return {}

        try:
            # projects/<PROJECT>/locations/<LOCATION>/...
            # Attempt to find location
            if "locations/" in resource_name:
                _ = resource_name.split("locations/")[1].split("/", maxsplit=1)[
                    0
                ]
            else:
                # If path is just projects/P/locations/L...
                parts = resource_name.split("/")
                if len(parts) > 3 and parts[2] == "locations":
                    _ = parts[3]
                else:
                    return {}
        except IndexError:
            return {}

        # Using global endpoint mapping for CXAS v1beta
        api_endpoint = "ces.googleapis.com"
        return {"api_endpoint": api_endpoint}

    @staticmethod
    def _get_project_id(resource_name: str) -> Optional[str]:
        """Extract project ID from a resource string."""
        if not resource_name:
            return None
        try:
            parts = resource_name.split("/")
            if len(parts) >= 2 and parts[0] == "projects":
                return parts[1]
        except Exception:
            pass
        return None

    @staticmethod
    def _get_location(resource_name: str) -> Optional[str]:
        """Extract location from a resource string."""
        if not resource_name:
            return None
        try:
            if "locations/" in resource_name:
                return resource_name.split("locations/")[1].split(
                    "/", maxsplit=1
                )[0]
            parts = resource_name.split("/")
            if (
                len(parts) >= 4
                and parts[0] == "projects"
                and parts[2] == "locations"
            ):
                return parts[3]
        except Exception:
            pass
        return None

    @staticmethod
    def _tokenize_textproto(text):
        token_pattern = re.compile(
            r'(?P<STRING>"(?:\\.|[^"\\])*")|'
            r"(?P<ID>[a-zA-Z_][a-zA-Z0-9_]*)|"
            r"(?P<NUMBER>-?\d+(?:\.\d+)?)|"
            r"(?P<LBRACE>\{)|"
            r"(?P<RBRACE>\})|"
            r"(?P<COLON>:)|"
            r"(?P<WHITESPACE>\s+)"
        )
        for match in token_pattern.finditer(text):
            kind = match.lastgroup
            if kind == "WHITESPACE":
                continue
            value = match.group()
            yield kind, value

    @staticmethod
    def _parse_textproto_tokens(tokens):
        obj = {}
        current_key = None

        while True:
            try:
                kind, value = next(tokens)
            except StopIteration:
                break

            if kind == "RBRACE":
                return obj

            if kind == "ID":
                current_key = value
                try:
                    next_kind, next_value = next(tokens)
                except StopIteration:
                    break

                if next_kind == "COLON":
                    val_kind, val_value = next(tokens)
                    actual_val = None
                    if val_kind == "STRING":
                        actual_val = (
                            val_value[1:-1]
                            .replace("\\n", "\n")
                            .replace('\\"', '"')
                            .replace("\\'", "'")
                        )
                    elif val_kind in ("NUMBER", "ID"):
                        actual_val = val_value
                        if actual_val == "true":
                            actual_val = True
                        elif actual_val == "false":
                            actual_val = False
                    elif val_kind == "LBRACE":
                        actual_val = Common._parse_textproto_tokens(tokens)

                    if current_key in obj:
                        if not isinstance(obj[current_key], list):
                            obj[current_key] = [obj[current_key]]
                        obj[current_key].append(actual_val)
                    else:
                        obj[current_key] = actual_val

                elif next_kind == "LBRACE":
                    child_obj = Common._parse_textproto_tokens(tokens)
                    if current_key in obj:
                        if not isinstance(obj[current_key], list):
                            obj[current_key] = [obj[current_key]]
                        obj[current_key].append(child_obj)
                    else:
                        obj[current_key] = child_obj
                else:
                    continue

        return obj

    @staticmethod
    def parse_textproto(text):
        tokens = Common._tokenize_textproto(text)
        return Common._parse_textproto_tokens(tokens)

    @staticmethod
    def unwrap_value(val):
        if not isinstance(val, dict):
            return val

        if "string_value" in val:
            return str(val["string_value"])
        if "number_value" in val:
            return (
                float(val["number_value"])
                if "." in str(val["number_value"])
                else int(val["number_value"])
            )
        if "bool_value" in val:
            return True if val["bool_value"] in (True, "true") else False
        if "list_value" in val:
            values = val["list_value"].get("values", [])
            if not isinstance(values, list):
                values = [values]
            return [Common.unwrap_value(v) for v in values]
        if "struct_value" in val:
            return Common.unwrap_struct(val["struct_value"])

        return val

    @staticmethod
    def unwrap_struct(struct):
        if not isinstance(struct, dict):
            return struct

        if "fields" not in struct:
            return struct

        fields = struct["fields"]
        if not isinstance(fields, list):
            fields = [fields]

        res = {}
        for f in fields:
            if "key" in f and "value" in f:
                res[f["key"]] = Common.unwrap_value(f["value"])

        return res

    @staticmethod
    def get_agent_text_from_outputs(
        outputs: List[Any], separator: str = "\n"
    ) -> str:
        """Extracts and concatenates text responses from a list of output
        objects.

        Args:
            outputs: A list of output objects (dict or protobuf) from a
                Session flow execution.
            separator: String used to join multiple text responses.

        Returns:
            A string containing the concatenated text from all outputs.
        """
        agent_texts = []
        for output in outputs:
            if hasattr(output, "text") and getattr(output, "text", ""):
                agent_texts.append(output.text)
            elif (
                isinstance(output, dict) and "text" in output and output["text"]
            ):
                agent_texts.append(output["text"])
        return separator.join(agent_texts)

    def recurse_proto_repeated_composite(self, repeated_object):
        """Recursively converts RepeatedComposite objects to lists."""
        repeated_list = []
        for item in repeated_object:
            if isinstance(item, repeated.RepeatedComposite):
                processed_item = self.recurse_proto_repeated_composite(item)
                repeated_list.append(processed_item)
            elif isinstance(item, maps.MapComposite):
                processed_item = self.recurse_proto_marshal_to_dict(item)
                repeated_list.append(processed_item)
            else:
                repeated_list.append(item)

        return repeated_list

    def recurse_proto_marshal_to_dict(self, marshal_object):
        """Recursively converts MapComposite objects to dicts."""
        new_dict = {}
        for k, v in marshal_object.items():
            processed_v = v
            if isinstance(v, maps.MapComposite):
                processed_v = self.recurse_proto_marshal_to_dict(v)
            elif isinstance(v, repeated.RepeatedComposite):
                processed_v = self.recurse_proto_repeated_composite(v)
            new_dict[k] = processed_v

        return new_dict
