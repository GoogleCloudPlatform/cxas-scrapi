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

import base64
import logging
import re
import urllib.parse
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)


class DFCXToolConverter:
    """Converts Dialogflow CX tools and webhooks to CXAS resources."""

    def __init__(self, secret_manager: Any, reporter: Any):
        self.secret_manager = secret_manager
        self.reporter = reporter

    @staticmethod
    def sanitize_resource_id(
        resource_id: str, min_len: int = 5, max_len: int = 36
    ) -> str:
        """Sanitizes a string to be a valid CXAS resource ID."""
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", resource_id)
        sanitized = sanitized.lstrip("_-")
        if not sanitized or not re.match(r"^[a-zA-Z0-9]", sanitized):
            sanitized = "tool_" + sanitized
        sanitized = sanitized[:max_len]
        while len(sanitized) < min_len:
            sanitized += "_"
        return sanitized

    def convert_cx_tool_to_ps_resource(
        self, cx_tool: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Converts a DFCX tool structure to a CXAS resource payload."""
        display_name = cx_tool.get("displayName", "unnamed_tool")
        description = cx_tool.get("description", "")
        sanitized_id = DFCXToolConverter.sanitize_resource_id(
            display_name, min_len=5, max_len=36
        )

        # --- OPENAPI TOOLSET MIGRATION ---
        if "openApiSpec" in cx_tool and cx_tool["openApiSpec"].get(
            "textSchema"
        ):
            logger.info(
                f"  -> Detected OpenAPI tool '{display_name}'. "
                f"Migrating as OpenApiToolset."
            )
            open_api_schema_text = cx_tool["openApiSpec"]["textSchema"]

            if "@dialogflow/sessionId" in open_api_schema_text:
                logger.info(
                    "    - Replacing '@dialogflow/sessionId' with "
                    "'x-ces-session-context: $context.session_id'."
                )

                open_api_schema_text = re.sub(
                    r"^(\s*)schema:\s*\n\s*\$ref:\s*['\"]?@dialogflow/sessionId['\"]?",
                    r"\1schema:\n\1  type: string\n"
                    r"\1x-ces-session-context: $context.session_id",
                    open_api_schema_text,
                    flags=re.MULTILINE,
                )

                open_api_schema_text = re.sub(
                    r"^(\s*)format:\s*['\"]?@dialogflow/sessionId['\"]?",
                    r"\1x-ces-session-context: $context.session_id",
                    open_api_schema_text,
                    flags=re.MULTILINE,
                )

            operation_ids = []
            try:
                spec = yaml.safe_load(open_api_schema_text)
                paths = spec.get("paths", {})
                for path, methods in paths.items():
                    for method, details in methods.items():
                        op_id = details.get("operationId")
                        if not op_id:
                            sanitized_path = (
                                path.strip("/")
                                .replace("/", "_")
                                .replace("-", "_")
                            )
                            op_id = f"{method.upper()}_{sanitized_path}"
                        operation_ids.append(op_id)
            except Exception as e:
                logger.warning(
                    f"  Warning: Could not parse YAML for tool "
                    f"'{display_name}' "
                    f"to extract operation IDs: {e}"
                )

            ps_api_authentication = {}
            cx_auth = cx_tool.get("openApiSpec", {}).get("authentication", {})

            if cx_auth:
                if "apiKeyConfig" in cx_auth:
                    cx_api_key = cx_auth["apiKeyConfig"]
                    secret_version = cx_api_key.get("apiKeySecretVersion")
                    if not secret_version and "apiKey" in cx_api_key:
                        logger.info(
                            f"  Found raw API key for tool '{display_name}'. "
                            f"Creating secret..."
                        )
                        secret_id = f"{sanitized_id}-api-key"
                        secret_version = (
                            self.secret_manager.create_secret_with_version(
                                secret_id=secret_id,
                                secret_payload=cx_api_key["apiKey"],
                            )
                        )

                    if secret_version:
                        request_location_map = {
                            "HEADER": 1,
                            "QUERY_STRING": 2,
                        }
                        ps_request_location = request_location_map.get(
                            cx_api_key.get("requestLocation")
                        )
                        ps_api_authentication["api_key_config"] = {
                            "key_name": cx_api_key.get("keyName"),
                            "request_location": ps_request_location,
                            "api_key_secret_version": secret_version,
                        }

                elif "oauthConfig" in cx_auth:
                    cx_oauth = cx_auth["oauthConfig"]
                    secret_version = cx_oauth.get(
                        "secretVersionForClientSecret"
                    )
                    if not secret_version and "clientSecret" in cx_oauth:
                        logger.info(
                            f"  Found raw OAuth client secret for tool "
                            f"'{display_name}'. Creating secret..."
                        )
                        secret_id = f"{sanitized_id}-oauth-secret"
                        secret_version = (
                            self.secret_manager.create_secret_with_version(
                                secret_id=secret_id,
                                secret_payload=cx_oauth["clientSecret"],
                            )
                        )

                    if secret_version:
                        grant_type_map = {"CLIENT_CREDENTIAL": 1}
                        ps_grant_type = grant_type_map.get(
                            cx_oauth.get("oauthGrantType")
                        )
                        ps_api_authentication["oauth_config"] = {
                            "oauth_grant_type": ps_grant_type,
                            "client_id": cx_oauth.get("clientId"),
                            "client_secret_version": secret_version,
                            "token_endpoint": cx_oauth.get("tokenEndpoint"),
                            "scopes": cx_oauth.get("scopes", []),
                        }

                elif "bearerTokenConfig" in cx_auth:
                    cx_bearer = cx_auth["bearerTokenConfig"]
                    secret_version = cx_bearer.get("bearerTokenSecretVersion")
                    if not secret_version and "token" in cx_bearer:
                        logger.info(
                            f"  Found raw Bearer token for tool "
                            f"'{display_name}'. "
                            f"Creating secret..."
                        )
                        secret_id = f"{sanitized_id}-bearer-token"
                        secret_payload = f"Bearer {cx_bearer['token']}"
                        secret_version = (
                            self.secret_manager.create_secret_with_version(
                                secret_id=secret_id,
                                secret_payload=secret_payload,
                            )
                        )

                    if secret_version:
                        ps_api_authentication["api_key_config"] = {
                            "key_name": "Authorization",
                            "request_location": 1,  # HEADER
                            "api_key_secret_version": secret_version,
                        }

                elif "serviceAccountAuthConfig" in cx_auth:
                    service_account_email = cx_auth.get(
                        "serviceAccountAuthConfig", {}
                    ).get("serviceAccount")
                    if service_account_email:
                        logger.info(
                            f"  -> Migrating Service Account "
                            f"authentication for tool '{display_name}'."
                        )
                        ps_api_authentication["service_account_auth_config"] = {
                            "service_account": service_account_email
                        }
                    else:
                        logger.warning(
                            f"  Warning: Found 'serviceAccountAuthConfig' for "
                            f"tool '{display_name}' but no service account "
                            f"email was provided. Skipping auth migration."
                        )

                elif "serviceAgentAuthConfig" in cx_auth:
                    auth_type = cx_auth.get("serviceAgentAuthConfig", {}).get(
                        "serviceAgentAuth"
                    )
                    if auth_type == "ID_TOKEN":
                        logger.info(
                            f"  -> Migrating Service Agent ID Token "
                            f"authentication for tool '{display_name}'."
                        )
                        ps_api_authentication[
                            "service_agent_id_token_auth_config"
                        ] = {}
                    else:
                        logger.warning(
                            f"  Warning: Found 'serviceAgentAuthConfig' for "
                            f"tool '{display_name}' but the type was not "
                            f"'ID_TOKEN'. Skipping auth migration."
                        )

            toolset_payload = {
                "display_name": display_name,
                "description": description,
                "open_api_toolset": {"open_api_schema": open_api_schema_text},
            }

            if ps_api_authentication:
                toolset_payload["open_api_toolset"]["api_authentication"] = (
                    ps_api_authentication
                )

            return {
                "type": "TOOLSET",
                "id": sanitized_id,
                "payload": toolset_payload,
                "operation_ids": operation_ids,
            }

        # --- DATA STORE TOOL MIGRATION ---
        tool_payload = {"name": sanitized_id, "displayName": display_name}

        if "dataStoreSpec" in cx_tool or "dataStoreTool" in cx_tool:
            data_store_spec = cx_tool.get("dataStoreSpec") or cx_tool.get(
                "dataStoreTool", {}
            )
            data_store_connections = data_store_spec.get("dataStoreConnections")

            if data_store_connections:
                logger.info(
                    f"  -> Migrating fully configured Data Store tool "
                    f"'{display_name}'."
                )
                data_store_path = data_store_connections[0]["dataStore"]
                tool_payload["data_store_tool"] = {
                    "description": description,
                    "name": sanitized_id,
                    "data_store_source": {
                        "data_store": {"name": data_store_path}
                    },
                }
                return {
                    "type": "TOOL",
                    "id": sanitized_id,
                    "payload": tool_payload,
                    "operation_ids": [],
                }
            else:
                logger.warning(
                    f"  -> SKIPPING MIGRATION for Data Store tool "
                    f"'{display_name}'."
                )
                logger.warning(
                    "     Reason: No datastore is selected in the source agent."
                )
                self.reporter.log_skipped(
                    "Data Store Tool",
                    display_name,
                    "No datastore selected in source agent.",
                )
                return None

        elif "connectorSpec" in cx_tool:
            logger.warning(
                f"Warning: Conversion for 'connectorSpec' tool "
                f"'{display_name}' is not fully implemented."
            )
            self.reporter.log_skipped(
                "Connector Tool",
                display_name,
                "connectorSpec conversion not fully implemented.",
            )
            return None

        else:
            logger.warning(
                f"Warning: Skipping tool '{display_name}' as its type is "
                f"not supported for conversion."
            )
            return None

    def convert_webhook_to_openapi_toolset(
        self, cx_webhook: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Converts a DFCX Webhook into a generalized Polysynth OpenAPI
        Toolset payload.
        """
        display_name = cx_webhook.get("displayName", "unnamed_webhook")
        sanitized_id = DFCXToolConverter.sanitize_resource_id(
            f"webhook_{display_name}", min_len=5, max_len=36
        )

        gws = cx_webhook.get("genericWebService", {})
        uri = gws.get("uri", "")
        if not uri:
            logger.warning(
                f"  -> Skipping webhook '{display_name}': No URI provided."
            )
            return None

        webhook_type = gws.get("webhookType", "STANDARD")
        method = gws.get("httpMethod", "POST").lower()
        operation_id = f"{method}_{sanitized_id}"

        clean_uri = re.sub(
            r"\$(?:session\.params|flow)\.([a-zA-Z0-9_]+)", r"{\1}", uri
        )

        parsed_uri = urllib.parse.urlparse(clean_uri)
        server_url = (
            f"{parsed_uri.scheme}://{parsed_uri.netloc}"
            if parsed_uri.netloc
            else "https://example.com"
        )
        path = parsed_uri.path if parsed_uri.path else "/"

        openapi_schema = f"""openapi: 3.0.0
info:
  title: {display_name} Webhook
  version: 1.0.0
  description: Migrated DFCX webhook ({webhook_type})
servers:
  - url: {server_url}
paths:
  {path}:
    {method}:
      operationId: {operation_id}
      summary: Execute {display_name}
"""

        path_params = re.findall(r"{([a-zA-Z0-9_]+)}", path)
        if path_params:
            openapi_schema += "      parameters:\n"
            for p in path_params:
                openapi_schema += (
                    f"        - name: {p}\n"
                    f"          in: path\n"
                    f"          required: true\n"
                    f"          schema:\n"
                    f"            type: string\n"
                )

        if method in ["post", "put", "patch"]:
            openapi_schema += """      requestBody:
        required: false
        content:
          application/json:
            schema:
              type: object
"""
        openapi_schema += """      responses:
        '200':
          description: Success
          content:
            application/json:
              schema:
                type: object
"""

        toolset_payload = {
            "display_name": display_name,
            "description": f"Backend webhook for {display_name}",
            "open_api_toolset": {"open_api_schema": openapi_schema},
        }

        ps_api_authentication = {}

        if "username" in gws and "password" in gws:
            logger.info(
                f"  -> Found Basic Auth for webhook '{display_name}'. "
                f"Creating secret..."
            )
            auth_str = f"{gws['username']}:{gws['password']}"
            b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode(
                "utf-8"
            )
            secret_version = self.secret_manager.create_secret_with_version(
                f"{sanitized_id}-basic", f"Basic {b64_auth}"
            )
            if secret_version:
                ps_api_authentication["api_key_config"] = {
                    "key_name": "Authorization",
                    "request_location": 1,
                    "api_key_secret_version": secret_version,
                }
        elif gws.get("serviceAgentAuth") == "ID_TOKEN":
            logger.info(
                f"  -> Found Service Agent ID Token auth for webhook "
                f"'{display_name}'."
            )
            ps_api_authentication["service_agent_id_token_auth_config"] = {}
        elif "requestHeaders" in gws:
            req_headers = gws["requestHeaders"]
            headers_list = []

            if isinstance(req_headers, dict):
                headers_list = list(req_headers.items())
            elif isinstance(req_headers, list):
                for item in req_headers:
                    if isinstance(item, dict):
                        if "key" in item and "value" in item:
                            headers_list.append((item["key"], item["value"]))
                        else:
                            headers_list.extend(item.items())
                    elif isinstance(item, str):
                        headers_list.append((item, ""))

            for k, v in headers_list:
                if (
                    k.lower() == "authorization"
                    or "api-key" in k.lower()
                    or "x-api-key" in k.lower()
                ):
                    logger.info(
                        f"  -> Found raw auth header for webhook "
                        f"'{display_name}'. Creating secret..."
                    )
                    secret_version = (
                        self.secret_manager.create_secret_with_version(
                            f"{sanitized_id}-auth", str(v)
                        )
                    )
                    if secret_version:
                        ps_api_authentication["api_key_config"] = {
                            "key_name": k,
                            "request_location": 1,
                            "api_key_secret_version": secret_version,
                        }
                    break

        if ps_api_authentication:
            toolset_payload["open_api_toolset"]["api_authentication"] = (
                ps_api_authentication
            )

        webhook_meta = {
            "webhook_type": webhook_type,
            "method": method.upper(),
            "original_uri": uri,
            "request_body_template": gws.get("requestBody", ""),
            "parameter_mapping": gws.get("parameterMapping", []),
        }

        return {
            "type": "TOOLSET",
            "id": sanitized_id,
            "payload": toolset_payload,
            "operation_ids": [operation_id],
            "webhook_meta": webhook_meta,
        }
