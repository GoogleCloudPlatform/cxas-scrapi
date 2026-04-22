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

import sys
from unittest.mock import MagicMock

import pytest

# Global Test Constants
TEST_APP_ID = "projects/df-reference/locations/us/apps/f39d3ab5-a463-4025-8437-31fd09685d6b"  # noqa: E501


def pytest_addoption(parser):
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


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "online: mark test as requiring live API access"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-online"):
        # --run-online given in cli: do not skip online tests
        return
    skip_online = pytest.mark.skip(reason="need --run-online option to run")
    for item in items:
        if "online" in item.keywords:
            item.add_marker(skip_online)


@pytest.fixture
def app_id():
    return TEST_APP_ID


# Create a mock module structure for google.cloud.ces_v1beta if not
# running online
if "--run-online" not in sys.argv:
    mock_ces = MagicMock()
    mock_ces.AgentServiceClient = MagicMock
    mock_ces.EvaluationServiceClient = MagicMock
    mock_ces.types = MagicMock()
    sys.modules["google.cloud.ces_v1beta"] = mock_ces

    # Mock google.cloud.dialogflowcx_v3beta1 for dfcx_exporter offline tests
    mock_dfcx = MagicMock()
    mock_dfcx_services = MagicMock()
    mock_dfcx_types = MagicMock()
    sys.modules["google.cloud.dialogflowcx_v3beta1"] = mock_dfcx
    sys.modules["google.cloud.dialogflowcx_v3beta1.services"] = (
        mock_dfcx_services
    )
    sys.modules["google.cloud.dialogflowcx_v3beta1.types"] = mock_dfcx_types
