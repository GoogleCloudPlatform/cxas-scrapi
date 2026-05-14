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

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.utils import cloud_logging as cl_mod


def _entry(ts, severity="WARNING", payload="hi", labels=None):
    return SimpleNamespace(
        payload=payload,
        timestamp=ts,
        severity=severity,
        log_name="projects/p/logs/req",
        resource=SimpleNamespace(type="api"),
        labels=labels or {},
        trace="t",
    )


@patch.object(cl_mod, "logging_v2")
def test_init_requires_dependency(mock_lv2):
    with patch.object(cl_mod, "logging_v2", None):
        with pytest.raises(ImportError, match="google-cloud-logging"):
            cl_mod.CloudLogsClient(
                project_id="p", filter_template="severity"
            )


@patch.object(cl_mod, "logging_v2")
def test_fetch_with_explicit_times(mock_lv2):
    mock_client = MagicMock()
    mock_lv2.Client.return_value = mock_client
    e = _entry(datetime.datetime(2026, 5, 10, 12, 0, 0))
    mock_client.list_entries.return_value = [e]

    client = cl_mod.CloudLogsClient(
        project_id="p",
        filter_template=(
            'severity >= "{level}" AND timestamp >= "{start_time}" '
            'AND timestamp <= "{end_time}" AND id="{conversation_id}"'
        ),
        time_padding_seconds=10,
    )
    rows = client.fetch(
        conversation_id="conv-1",
        start_time=datetime.datetime(2026, 5, 10, 11, 59, 0),
        end_time=datetime.datetime(2026, 5, 10, 12, 1, 0),
        level="warning",
    )
    args, kwargs = mock_client.list_entries.call_args
    assert kwargs["order_by"] == "timestamp asc"
    assert "WARNING" in kwargs["filter_"]
    assert "conv-1" in kwargs["filter_"]
    assert len(rows) == 1
    assert rows[0]["severity"] == "WARNING"
    assert rows[0]["resource_type"] == "api"
    assert rows[0]["message"] == "hi"


@patch.object(cl_mod, "logging_v2")
def test_fetch_defaults_when_no_times_given(mock_lv2):
    mock_client = MagicMock()
    mock_lv2.Client.return_value = mock_client
    mock_client.list_entries.return_value = []

    client = cl_mod.CloudLogsClient(
        project_id="p",
        filter_template=(
            'severity >= "{level}" AND timestamp >= "{start_time}" '
            'AND timestamp <= "{end_time}" AND id="{conversation_id}"'
        ),
    )
    rows = client.fetch(
        conversation_id="x", start_time=None, end_time=None
    )
    assert rows == []


@patch.object(cl_mod, "logging_v2")
def test_fetch_swallows_exception(mock_lv2):
    mock_client = MagicMock()
    mock_lv2.Client.return_value = mock_client
    mock_client.list_entries.side_effect = RuntimeError("boom")
    client = cl_mod.CloudLogsClient(
        project_id="p",
        filter_template=(
            "severity >= \"{level}\" AND timestamp >= \"{start_time}\" "
            "AND timestamp <= \"{end_time}\" AND id=\"{conversation_id}\""
        ),
    )
    assert (
        client.fetch(
            conversation_id="x",
            start_time=datetime.datetime(2026, 5, 1),
            end_time=datetime.datetime(2026, 5, 2),
        )
        == []
    )


@patch.object(cl_mod, "logging_v2")
def test_entry_to_row_with_dict_payload(mock_lv2):
    mock_client = MagicMock()
    mock_lv2.Client.return_value = mock_client
    e = SimpleNamespace(
        payload={"message": "hello", "extra": "data"},
        timestamp=datetime.datetime(2026, 5, 1),
        severity="ERROR",
        log_name="ln",
        resource=SimpleNamespace(type="cloud_function"),
        labels={"k": "v"},
        trace="trace-id",
    )
    mock_client.list_entries.return_value = [e]
    client = cl_mod.CloudLogsClient(
        project_id="p",
        filter_template=(
            "severity >= \"{level}\" AND timestamp >= \"{start_time}\" "
            "AND timestamp <= \"{end_time}\" AND id=\"{conversation_id}\""
        ),
    )
    rows = client.fetch(
        conversation_id="c",
        start_time=datetime.datetime(2026, 5, 1),
        end_time=datetime.datetime(2026, 5, 2),
    )
    assert rows[0]["message"] == "hello"
    assert rows[0]["labels"] == {"k": "v"}


@patch.object(cl_mod, "logging_v2")
def test_entry_to_row_no_resource_no_timestamp(mock_lv2):
    mock_client = MagicMock()
    mock_lv2.Client.return_value = mock_client
    e = SimpleNamespace(
        payload=None,
        timestamp=None,
        severity=None,
        log_name=None,
        resource=None,
        labels=None,
        trace=None,
    )
    mock_client.list_entries.return_value = [e]
    client = cl_mod.CloudLogsClient(
        project_id="p",
        filter_template=(
            "severity >= \"{level}\" AND timestamp >= \"{start_time}\" "
            "AND timestamp <= \"{end_time}\" AND id=\"{conversation_id}\""
        ),
    )
    rows = client.fetch(
        conversation_id="c",
        start_time=datetime.datetime(2026, 5, 1),
        end_time=datetime.datetime(2026, 5, 2),
    )
    assert rows[0]["message"] == ""
    assert rows[0]["labels"] == {}
    assert rows[0]["resource_type"] is None


def test_to_rfc3339_naive():
    dt = datetime.datetime(2026, 5, 1, 12, 30, 45)
    assert cl_mod._to_rfc3339(dt) == "2026-05-01T12:30:45Z"


def test_to_rfc3339_aware():
    dt = datetime.datetime(
        2026, 5, 1, 12, 30, 45, tzinfo=datetime.timezone.utc
    )
    s = cl_mod._to_rfc3339(dt)
    assert "2026-05-01T12:30:45" in s


def test_stringify_paths():
    assert cl_mod._stringify(None) == ""
    assert cl_mod._stringify("hi") == "hi"
    assert cl_mod._stringify({"a": 1}) == '{"a": 1}'

    class NotJsonable:
        def __repr__(self):
            return "NotJsonable()"

        def __str__(self):
            return "stringified"

    # Force json.dumps to fail by passing an object via patch.
    with patch.object(cl_mod.json, "dumps", side_effect=ValueError):
        assert cl_mod._stringify({"a": 1}) == "{'a': 1}"
