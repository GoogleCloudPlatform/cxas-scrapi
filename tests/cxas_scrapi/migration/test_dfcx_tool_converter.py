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

from unittest.mock import MagicMock

from cxas_scrapi.migration.dfcx_tool_converter import DFCXToolConverter


def test_sanitize_resource_id():
    assert DFCXToolConverter.sanitize_resource_id("valid_id") == "valid_id"
    assert DFCXToolConverter.sanitize_resource_id("invalid id") == "invalid_id"
    assert DFCXToolConverter.sanitize_resource_id("a" * 50) == "a" * 36
    assert DFCXToolConverter.sanitize_resource_id("abc") == "abc__"


def test_convert_cx_tool_to_ps_resource_openapi():
    mock_secret_manager = MagicMock()
    mock_reporter = MagicMock()
    converter = DFCXToolConverter(mock_secret_manager, mock_reporter)

    cx_tool = {
        "displayName": "Test Tool",
        "openApiSpec": {
            "textSchema": (
                "openapi: 3.0.0\n"
                "info:\n"
                "  title: Test\n"
                "  version: 1.0.0\n"
                "paths:\n"
                "  /test:\n"
                "    get:\n"
                "      operationId: getTest"
            )
        },
    }

    res = converter.convert_cx_tool_to_ps_resource(cx_tool)

    assert res["type"] == "TOOLSET"
    assert res["id"] == "Test_Tool"
    assert "open_api_toolset" in res["payload"]
    assert res["operation_ids"] == ["getTest"]


def test_convert_cx_tool_to_ps_resource_datastore():
    mock_secret_manager = MagicMock()
    mock_reporter = MagicMock()
    converter = DFCXToolConverter(mock_secret_manager, mock_reporter)

    cx_tool = {
        "displayName": "Test Datastore",
        "dataStoreSpec": {
            "dataStoreConnections": [
                {
                    "dataStore": (
                        "projects/123/locations/global/collections/"
                        "default_collection/dataStores/ds-id"
                    )
                }
            ]
        },
    }

    res = converter.convert_cx_tool_to_ps_resource(cx_tool)

    assert res["type"] == "TOOL"
    assert res["id"] == "Test_Datastore"
    assert "data_store_tool" in res["payload"]


def test_convert_webhook_to_openapi_toolset():
    mock_secret_manager = MagicMock()
    mock_reporter = MagicMock()
    converter = DFCXToolConverter(mock_secret_manager, mock_reporter)

    cx_webhook = {
        "displayName": "Test Webhook",
        "genericWebService": {
            "uri": "https://example.com/api",
            "httpMethod": "POST",
        },
    }

    res = converter.convert_webhook_to_openapi_toolset(cx_webhook)

    assert res["type"] == "TOOLSET"
    assert res["id"] == "webhook_Test_Webhook"
    assert "open_api_toolset" in res["payload"]
    assert res["operation_ids"] == ["post_webhook_Test_Webhook"]
