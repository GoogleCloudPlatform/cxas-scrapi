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

from cxas_scrapi.utils.tracing import cloud_logging as cl_mod


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
            cl_mod.CloudLogsClient(project_id="p", filter_template="severity")


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
    rows = client.fetch(conversation_id="x", start_time=None, end_time=None)
    assert rows == []


@patch.object(cl_mod, "logging_v2")
def test_fetch_swallows_exception(mock_lv2):
    mock_client = MagicMock()
    mock_lv2.Client.return_value = mock_client
    mock_client.list_entries.side_effect = RuntimeError("boom")
    client = cl_mod.CloudLogsClient(
        project_id="p",
        filter_template=(
            'severity >= "{level}" AND timestamp >= "{start_time}" '
            'AND timestamp <= "{end_time}" AND id="{conversation_id}"'
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
            'severity >= "{level}" AND timestamp >= "{start_time}" '
            'AND timestamp <= "{end_time}" AND id="{conversation_id}"'
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
            'severity >= "{level}" AND timestamp >= "{start_time}" '
            'AND timestamp <= "{end_time}" AND id="{conversation_id}"'
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


@patch.object(cl_mod, "logging_v2")
def test_batch_fetch_groups_by_conversation_id(mock_lv2):
    mock_client = MagicMock()
    mock_lv2.Client.return_value = mock_client
    e1 = SimpleNamespace(
        payload="hi",
        timestamp=datetime.datetime(2026, 5, 1, 12, 0, 1),
        severity="WARNING",
        log_name="ln",
        resource=None,
        labels={"conversation_id": "c-1"},
        trace=None,
    )
    e2 = SimpleNamespace(
        payload="bye",
        timestamp=datetime.datetime(2026, 5, 1, 12, 0, 2),
        severity="ERROR",
        log_name="ln",
        resource=None,
        labels={"conversation_id": "c-2"},
        trace=None,
    )
    mock_client.list_entries.return_value = [e1, e2]

    client = cl_mod.CloudLogsClient(
        project_id="p",
        filter_template=(
            'severity >= "{level}" AND timestamp >= "{start_time}" '
            'AND timestamp <= "{end_time}" '
            'AND labels.conversation_id="{conversation_id}"'
        ),
    )
    out = client.batch_fetch(
        conversation_ids=["c-1", "c-2", "c-3"],
        start_time=datetime.datetime(2026, 5, 1),
        end_time=datetime.datetime(2026, 5, 2),
    )
    assert set(out.keys()) == {"c-1", "c-2", "c-3"}
    assert len(out["c-1"]) == 1 and out["c-1"][0]["message"] == "hi"
    assert len(out["c-2"]) == 1
    assert out["c-3"] == []
    # Verify the OR-combined filter went through
    _, kwargs = mock_client.list_entries.call_args
    assert " OR " in kwargs["filter_"]


@patch.object(cl_mod, "logging_v2")
def test_batch_fetch_empty_input(mock_lv2):
    mock_client = MagicMock()
    mock_lv2.Client.return_value = mock_client
    client = cl_mod.CloudLogsClient(
        project_id="p",
        filter_template=(
            'severity >= "{level}" AND timestamp >= "{start_time}" '
            'AND timestamp <= "{end_time}" '
            'AND labels.conversation_id="{conversation_id}"'
        ),
    )
    assert (
        client.batch_fetch(
            conversation_ids=[],
            start_time=datetime.datetime(2026, 5, 1),
            end_time=datetime.datetime(2026, 5, 2),
        )
        == {}
    )
    mock_client.list_entries.assert_not_called()


@patch.object(cl_mod, "logging_v2")
def test_batch_fetch_handles_exception(mock_lv2):
    mock_client = MagicMock()
    mock_lv2.Client.return_value = mock_client
    mock_client.list_entries.side_effect = RuntimeError("boom")
    client = cl_mod.CloudLogsClient(
        project_id="p",
        filter_template=(
            'severity >= "{level}" AND timestamp >= "{start_time}" '
            'AND timestamp <= "{end_time}" '
            'AND labels.conversation_id="{conversation_id}"'
        ),
    )
    out = client.batch_fetch(
        conversation_ids=["a", "b"],
        start_time=datetime.datetime(2026, 5, 1),
        end_time=datetime.datetime(2026, 5, 2),
    )
    assert out == {"a": [], "b": []}


def test_row_conversation_id_from_labels():
    assert (
        cl_mod._row_conversation_id({"labels": {"conversation_id": "c-7"}})
        == "c-7"
    )


def test_row_conversation_id_from_message_text():
    msg = 'jsonPayload.conversation_id="abcd-1234-5678"'
    assert cl_mod._row_conversation_id({"message": msg}) == "abcd-1234-5678"


def test_row_conversation_id_returns_none_when_missing():
    assert cl_mod._row_conversation_id({}) is None
    assert cl_mod._row_conversation_id({"message": "no id here"}) is None
    assert cl_mod._row_conversation_id({"labels": "not-a-dict"}) is None


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
