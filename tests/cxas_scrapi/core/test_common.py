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

import os
import sys
import typing
from unittest.mock import MagicMock, patch

# Ensure correct path
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../src"))
)

from cxas_scrapi.core.common import DEFAULT_API_ENDPOINT, Common


def test_common_init() -> None:
    try:
        Common()
        print("PASS: Initialized Common with default creds (ADC).")
    except Exception as e:
        print(f"WARN: Failed to init with ADC (expected if no ADC): {e}")


@patch("cxas_scrapi.core.common.default")
def test_common_auth(mock_auth: typing.Any) -> None:
    # Temporarily clear the globally mocked oauth token for this specific test
    original_val = os.environ.pop("CXAS_OAUTH_TOKEN", None)
    try:
        mock_creds = MagicMock()
        mock_auth.return_value = (mock_creds, "project")
        common = Common()
        assert common.creds == mock_creds
    finally:
        if original_val is not None:
            os.environ["CXAS_OAUTH_TOKEN"] = original_val


def test_client_options() -> None:
    us_id = "projects/my-project/locations/us/agents/my-agent"
    opts = Common._get_client_options(us_id)
    assert opts["api_endpoint"] == DEFAULT_API_ENDPOINT

    eu_id = "projects/my-project/locations/eu/agents/my-agent"
    opts = Common._get_client_options(eu_id)
    assert opts["api_endpoint"] == DEFAULT_API_ENDPOINT


def test_project_id_extraction() -> None:
    assert (
        Common._get_project_id("projects/test-proj/locations/us/apps/abc")
        == "test-proj"
    )
    assert Common._get_project_id("invalid-format") is None


def test_location_extraction() -> None:
    assert (
        Common._get_location("projects/test-proj/locations/us/apps/abc") == "us"
    )
    assert Common._get_location("invalid-format") is None


def test_app_name_extraction() -> None:
    assert (
        Common._get_app_name(
            "projects/test-proj/locations/us/apps/abc/conversations/123"
        )
        == "projects/test-proj/locations/us/apps/abc"
    )
    assert (
        Common._get_app_name("projects/test-proj/locations/us/apps/abc")
        == "projects/test-proj/locations/us/apps/abc"
    )
    assert Common._get_app_name("invalid-format") is None


def test_session_id_extraction() -> None:
    assert (
        Common._get_session_id(
            "projects/test-proj/locations/us/apps/abc/sessions/sess-123"
        )
        == "sess-123"
    )
    assert Common._get_session_id("invalid-format") is None


def test_unwrap_value() -> None:
    assert Common.unwrap_value({"string_value": "hello"}) == "hello"
    assert Common.unwrap_value({"number_value": 42}) == 42
    assert Common.unwrap_value({"bool_value": True})
    assert Common.unwrap_value(
        {"list_value": {"values": [{"string_value": "a"}]}}
    ) == ["a"]


def test_unwrap_struct() -> None:
    struct = {
        "fields": [
            {"key": "name", "value": {"string_value": "test"}},
            {"key": "age", "value": {"number_value": 30}},
        ]
    }
    assert Common.unwrap_struct(struct) == {"name": "test", "age": 30}


def test_tokenize() -> None:
    text = '{ key: "value" }'
    tokens = list(Common._tokenize_textproto(text))
    kinds = [t[0] for t in tokens]
    assert kinds == ["LBRACE", "ID", "COLON", "STRING", "RBRACE"]


def test_parse() -> None:
    text = '{ key: "value" num: 42 bool_val: true }'
    tokens = Common._tokenize_textproto(text)
    res = Common._parse_textproto_tokens(tokens)
    assert res == {"key": "value", "num": "42", "bool_val": True}


def test_parse_textproto() -> None:
    text = '{ key: "value" nested: { inner: 1 } }'
    res = Common.parse_textproto(text)
    assert res == {"key": "value", "nested": {"inner": "1"}}


def test_ces_api_endpoint_override() -> None:
    import importlib  # noqa: PLC0415

    from cxas_scrapi.core import common  # noqa: PLC0415

    # Store original
    orig_endpoint = os.environ.get("CES_API_ENDPOINT")

    try:
        os.environ["CES_API_ENDPOINT"] = "custom.ces.googleapis.com"
        importlib.reload(common)
        assert common.DEFAULT_API_ENDPOINT == "custom.ces.googleapis.com"
    finally:
        # Restore original env and reload to restore state
        if orig_endpoint is not None:
            os.environ["CES_API_ENDPOINT"] = orig_endpoint
        else:
            os.environ.pop("CES_API_ENDPOINT", None)
        importlib.reload(common)


def test_ces_transport_override_grpc() -> None:
    # Default should be grpc
    common = Common(creds=MagicMock())
    mock_client = MagicMock()
    common.get_grpc_transport(mock_client)
    mock_client.get_transport_class.assert_called_with("grpc")


@patch.dict(os.environ, {"CES_TRANSPORT": "rest"})
def test_ces_transport_override_rest() -> None:
    common = Common(creds=MagicMock())
    mock_client = MagicMock()
    common.get_grpc_transport(mock_client)
    mock_client.get_transport_class.assert_called_with("rest")


if __name__ == "__main__":
    test_common_init()
    test_client_options()
    print("Done.")
