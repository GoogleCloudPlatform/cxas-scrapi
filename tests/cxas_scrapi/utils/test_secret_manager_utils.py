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

from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.utils.secret_manager_utils import SecretManagerUtils


@patch(
    "cxas_scrapi.utils.secret_manager_utils.secretmanager.SecretManagerServiceClient"
)
def test_create_or_get_secret_existing(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_secret = MagicMock()
    mock_secret.name = "projects/test-project/secrets/my-secret"
    mock_client.list_secrets.return_value = [mock_secret]

    sm = SecretManagerUtils("test-project")
    res = sm.create_or_get_secret("my-secret")
    assert res == "projects/test-project/secrets/my-secret/versions/latest"
    mock_client.create_secret.assert_not_called()


@patch(
    "cxas_scrapi.utils.secret_manager_utils.secretmanager.SecretManagerServiceClient"
)
def test_create_or_get_secret_new(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_client.list_secrets.return_value = []
    mock_created = MagicMock()
    mock_created.name = "projects/test-project/secrets/new-secret"
    mock_client.create_secret.return_value = mock_created

    sm = SecretManagerUtils("test-project")
    res = sm.create_or_get_secret("new-secret", "my-payload")

    assert res == "projects/test-project/secrets/new-secret/versions/latest"
    mock_client.create_secret.assert_called_once_with(
        request={
            "parent": "projects/test-project",
            "secret_id": "new-secret",
            "secret": {"replication": {"automatic": {}}},
        }
    )
    mock_client.add_secret_version.assert_called_once_with(
        request={
            "parent": "projects/test-project/secrets/new-secret",
            "payload": {"data": b"my-payload"},
        }
    )


@patch(
    "cxas_scrapi.utils.secret_manager_utils.secretmanager.SecretManagerServiceClient"
)
def test_create_or_get_secret_missing_payload(mock_client_cls):
    mock_client = mock_client_cls.return_value
    mock_client.list_secrets.return_value = []

    sm = SecretManagerUtils("test-project")
    with pytest.raises(ValueError):
        sm.create_or_get_secret("missing-secret")
