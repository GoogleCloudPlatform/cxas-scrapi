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
import webbrowser
from unittest.mock import MagicMock, create_autospec

import google.cloud.ces_v1beta as real_ces
import google.cloud.dialogflowcx_v3beta1 as real_dfcx
import google.cloud.dialogflowcx_v3beta1.services as real_dfcx_services
import google.cloud.dialogflowcx_v3beta1.types as real_dfcx_types
import pytest

# Global Test Constants
TEST_APP_NAME = "projects/mock-project/locations/mock-location/apps/mock-app-id"

# Mock the oauth token globally so tests don't try to
# invoke Application Default Credentials
os.environ["CXAS_OAUTH_TOKEN"] = "mock_token_for_tests"

# ==============================================================================
# Mocks setup: must run BEFORE importing any cxas_scrapi modules
# ==============================================================================
if "--run-online" not in sys.argv:
    mock_ces = create_autospec(real_ces)

    def enforce_transport(client_class: typing.Any) -> typing.Any:
        def _enforce(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
            if "transport" not in kwargs:
                raise ValueError(
                    "Client must be initialized with a transport from "
                    "get_grpc_transport to ensure correct telemetry."
                )
            return create_autospec(client_class, instance=True)

        return _enforce

    mock_ces.AgentServiceClient.side_effect = enforce_transport(
        real_ces.AgentServiceClient
    )
    mock_ces.AgentServiceClient.get_transport_class = MagicMock()

    mock_ces.EvaluationServiceClient.side_effect = enforce_transport(
        real_ces.EvaluationServiceClient
    )
    mock_ces.EvaluationServiceClient.get_transport_class = MagicMock()

    mock_ces.SessionServiceClient.side_effect = enforce_transport(
        real_ces.SessionServiceClient
    )
    mock_ces.SessionServiceClient.get_transport_class = MagicMock()

    mock_ces.ToolServiceClient.side_effect = enforce_transport(
        real_ces.ToolServiceClient
    )
    mock_ces.ToolServiceClient.get_transport_class = MagicMock()

    mock_ces.types = real_ces.types
    sys.modules["google.cloud.ces_v1beta"] = mock_ces

    # Mock google.cloud.dialogflowcx_v3beta1 for dfcx_exporter offline tests
    # using autospec
    mock_dfcx = create_autospec(real_dfcx)
    mock_dfcx_services = create_autospec(real_dfcx_services)
    mock_dfcx_types = create_autospec(real_dfcx_types)

    sys.modules["google.cloud.dialogflowcx_v3beta1"] = mock_dfcx
    sys.modules["google.cloud.dialogflowcx_v3beta1.services"] = (
        mock_dfcx_services
    )
    sys.modules["google.cloud.dialogflowcx_v3beta1.types"] = mock_dfcx_types

# Now safe to import GeminiGenerate (which triggers other cxas_scrapi imports)
from cxas_scrapi.utils.gemini import GeminiGenerate  # noqa: E402


def pytest_addoption(parser: typing.Any) -> None:
    parser.addoption(
        "--app-id",
        action="store",
        default=None,
        help="The CXAS App ID to run evaluations against",
    )
    parser.addoption(
        "--eval-dir",
        action="store",
        default=None,
        help="Directory containing evaluation YAML files",
    )
    parser.addoption(
        "--run-online",
        action="store_true",
        default=False,
        help="run tests that specifically rely on live API calls",
    )
    parser.addoption(
        "--reload",
        action="store_true",
        default=False,
        help="Delete existing evaluation with same display name before running",
    )


def pytest_configure(config: typing.Any) -> None:
    config.addinivalue_line(
        "markers", "online: mark test as requiring live API access"
    )


def pytest_collection_modifyitems(
    config: typing.Any, items: typing.Any
) -> None:
    if config.getoption("--run-online"):
        # --run-online given in cli: do not skip online tests
        return
    skip_online = pytest.mark.skip(reason="need --run-online option to run")
    for item in items:
        if "online" in item.keywords:
            item.add_marker(skip_online)


@pytest.fixture
def app_id() -> typing.Any:
    return TEST_APP_NAME


@pytest.fixture(autouse=True)
def mock_gemini_generate(
    request: typing.Any, monkeypatch: typing.Any
) -> typing.Any:
    """Centrally mock GeminiGenerate calls for all tests except test_gemini.py.

    This prevents unit tests from attempting real network calls to Gemini APIs,
    which results in long timeouts.
    """
    # Skip mocking for test_gemini.py so we can test the wrapper itself
    if "test_gemini" in request.module.__name__:
        yield
        return

    async def mock_generate_async(
        self: typing.Any,
        prompt: typing.Any,
        **kwargs: typing.Any,  # noqa: ANN001
    ) -> str:
        response_mime_type = kwargs.get("response_mime_type")
        if response_mime_type == "application/json":
            return "{}"
        return "mock_response"

    def mock_generate(self, prompt: typing.Any, **kwargs: typing.Any) -> str:  # noqa: ANN001
        response_mime_type = kwargs.get("response_mime_type")
        if response_mime_type == "application/json":
            return "{}"
        return "mock_response"

    monkeypatch.setattr(GeminiGenerate, "generate_async", mock_generate_async)
    monkeypatch.setattr(GeminiGenerate, "generate", mock_generate)
    yield


@pytest.fixture(autouse=True)
def mock_webbrowser(monkeypatch: typing.Any) -> None:
    """Globally mock webbrowser to prevent opening browsers during tests."""
    monkeypatch.setattr(webbrowser, "open", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        webbrowser, "open_new_tab", lambda *args, **kwargs: True
    )
