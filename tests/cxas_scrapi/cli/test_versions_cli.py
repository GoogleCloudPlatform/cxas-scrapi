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

"""Unit tests for versions_cli commands."""

import argparse
from typing import Any
from unittest.mock import MagicMock, patch
import pytest
from cxas_scrapi.cli.versions_cli import app_versions_list, app_versions_compare


class FakeProto:
    """Simulates a protobuf/pydantic message with dot access and class/instance to_dict."""
    def __init__(self, **kwargs: Any) -> None:
        self._dict = kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)

    def to_dict(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(self._dict)

    @classmethod
    def to_dict(cls, obj: Any) -> dict[str, Any]:
        return dict(getattr(obj, "_dict", {}))


def _ns(**kwargs: Any) -> argparse.Namespace:
    base = dict(
        app_name="projects/p/locations/l/apps/a",
        source="v1",
        target="v2",
        verbose=False,
        web=False,
        output=None,
    )
    base.update(kwargs)
    return argparse.Namespace(**base)


@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_list_success(mock_resolve: MagicMock, mock_versions_cls: MagicMock) -> None:
    """Test app_versions_list with populated versions list."""
    mock_resolve.return_value = (MagicMock(), "projects/p/locations/l/apps/a", "Test App")
    mock_inst = mock_versions_cls.return_value

    v1 = FakeProto(
        name="projects/p/locations/l/apps/a/versions/v1",
        display_name="Version 1.0",
        create_time="2026-06-01 12:00:00",
        description="Initial release",
        creator="alice@example.com",
    )
    v2 = FakeProto(
        name="projects/p/locations/l/apps/a/versions/v2",
        display_name="Version 2.0",
        create_time="2026-06-15 12:00:00",
        description="This description is intentionally very long to test table truncation behavior cleanly",
        creator="bob@example.com",
    )
    mock_inst.list_versions.return_value = [v1, v2]

    app_versions_list(_ns())
    mock_inst.list_versions.assert_called_once()


@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_list_empty(mock_resolve: MagicMock, mock_versions_cls: MagicMock) -> None:
    """Test app_versions_list when no versions exist."""
    mock_resolve.return_value = (MagicMock(), "projects/p/locations/l/apps/a", "Test App")
    mock_inst = mock_versions_cls.return_value
    mock_inst.list_versions.return_value = []

    app_versions_list(_ns())
    mock_inst.list_versions.assert_called_once()


@patch("cxas_scrapi.cli.versions_cli.Console")
@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_list_exception(mock_resolve: MagicMock, mock_versions_cls: MagicMock, mock_console: MagicMock) -> None:
    """Test app_versions_list handling API failure inside try block."""
    mock_resolve.return_value = (MagicMock(), "projects/p/locations/l/apps/a", "Test App")
    mock_versions_cls.side_effect = RuntimeError("API error")
    with pytest.raises(SystemExit) as exc:
        app_versions_list(_ns())
    assert exc.value.code == 1
    mock_console.return_value.print.assert_called()


@patch("cxas_scrapi.cli.versions_cli._generate_html_report")
@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_compare_summary_and_html(mock_resolve: MagicMock, mock_versions_cls: MagicMock, mock_html: MagicMock) -> None:
    """Test app_versions_compare with summary statistics and HTML generation."""
    mock_resolve.return_value = (MagicMock(), "projects/p/locations/l/apps/a", "Test App")
    mock_inst = mock_versions_cls.return_value

    agent1 = FakeProto(name="agent_a", display_name="Agent A", instruction="Be helpful", tools=["tools/tool_a"])
    agent2 = FakeProto(name="agent_a", display_name="Agent A", instruction="Be very helpful", tools=["tools/tool_a"])

    tool1 = FakeProto(name="tool_a", display_name="Tool A", description="Find orders")
    guardrail1 = FakeProto(name="gr_a", display_name="GR A", policy="No PII")
    toolset1 = FakeProto(name="ts_a", display_name="TS A")

    snap1 = FakeProto(
        app=FakeProto(display_name="App V1", description="Old desc"),
        agents=[agent1],
        tools=[tool1],
        guardrails=[guardrail1],
        toolsets=[toolset1],
    )
    snap2 = FakeProto(
        app=FakeProto(display_name="App V2", description="New desc"),
        agents=[agent2],
        tools=[],
        guardrails=[],
        toolsets=[],
    )

    v1 = FakeProto(display_name="V1", snapshot=snap1)
    v2 = FakeProto(display_name="V2", snapshot=snap2)

    mock_inst.get_version.side_effect = [v1, v2]

    app_versions_compare(_ns())
    assert mock_inst.get_version.call_count == 2
    mock_html.assert_called_once()


@patch("cxas_scrapi.cli.versions_cli._print_verbose_diff")
@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_compare_verbose_and_markdown(mock_resolve: MagicMock, mock_versions_cls: MagicMock, mock_verbose: MagicMock, tmp_path: Any) -> None:
    """Test app_versions_compare with verbose output and markdown export."""
    mock_resolve.return_value = (MagicMock(), "projects/p/locations/l/apps/a", "Test App")
    mock_inst = mock_versions_cls.return_value

    agent1 = FakeProto(name="agent_a", display_name="Agent A", instruction="Hello", tools=[])
    agent2 = FakeProto(name="agent_b", display_name="Agent B", instruction="Hello B", tools=[])

    snap1 = FakeProto(
        app=FakeProto(display_name="App"),
        agents=[agent1],
        tools=[],
        guardrails=[],
        toolsets=[],
    )
    snap2 = FakeProto(
        app=FakeProto(display_name="App"),
        agents=[agent2],
        tools=[],
        guardrails=[],
        toolsets=[],
    )

    v1 = FakeProto(display_name="V1", snapshot=snap1)
    v2 = FakeProto(display_name="V2", snapshot=snap2)

    mock_inst.get_version.side_effect = [v1, v2]

    md_out = tmp_path / "report.md"
    app_versions_compare(_ns(verbose=True, output=str(md_out)))
    mock_verbose.assert_called_once()
    assert md_out.exists()
    assert "# Version Comparison Report" in md_out.read_text()


@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_compare_html_report_execution(mock_resolve: MagicMock, mock_versions_cls: MagicMock, tmp_path: Any) -> None:
    """Test full execution of _generate_html_report and resource drift branches inside app_versions_compare."""
    mock_resolve.return_value = (MagicMock(), "projects/p/locations/l/apps/a", "Test App")
    mock_inst = mock_versions_cls.return_value

    agent1 = FakeProto(name="agent_a", display_name="Agent A", instruction="Be helpful", tools=["tools/tool_a"])
    agent2 = FakeProto(name="agent_a", display_name="Agent A", instruction="Be more helpful", tools=["tools/tool_b"])
    agent3 = FakeProto(name="agent_c", display_name="Agent C", instruction="New agent", tools=[])

    tool1 = FakeProto(name="tool_a", display_name="Tool A", description="Old tool")
    tool2 = FakeProto(name="tool_a", display_name="Tool A", description="Modified tool")
    tool3 = FakeProto(name="tool_b", display_name="Tool B", description="Added tool")

    gr1 = FakeProto(name="gr_a", display_name="GR A", policy="No PII")
    gr2 = FakeProto(name="gr_a", display_name="GR A", policy="Modified policy")
    gr3 = FakeProto(name="gr_b", display_name="GR B", policy="Added guardrail")

    ts1 = FakeProto(name="ts_a", display_name="TS A", tools=["tool_a"])
    ts2 = FakeProto(name="ts_a", display_name="TS A", tools=["tool_a", "tool_b"])
    ts3 = FakeProto(name="ts_b", display_name="TS B", tools=[])

    snap1 = FakeProto(
        app=FakeProto(display_name="App V1", description="Old app"),
        agents=[agent1],
        tools=[tool1],
        guardrails=[gr1],
        toolsets=[ts1],
    )
    snap2 = FakeProto(
        app=FakeProto(display_name="App V2", description="New app"),
        agents=[agent2, agent3],
        tools=[tool2, tool3],
        guardrails=[gr2, gr3],
        toolsets=[ts2, ts3],
    )

    v1 = FakeProto(display_name="V1", snapshot=snap1)
    v2 = FakeProto(display_name="V2", snapshot=snap2)

    mock_inst.get_version.side_effect = [v1, v2]

    html_out = tmp_path / "compare.html"
    app_versions_compare(_ns(web=True, output=str(html_out)))
    assert html_out.exists()
    content = html_out.read_text()
    assert "Version Comparison Report" in content or "Version Diff" in content or "<!DOCTYPE html>" in content


@patch("cxas_scrapi.cli.versions_cli._generate_html_report")
@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_compare_full_drift_summary(mock_resolve: MagicMock, mock_versions_cls: MagicMock, mock_html: MagicMock) -> None:
    """Test app_versions_compare triggering all console summary and HTML diff branches across all resource types."""
    mock_resolve.return_value = (MagicMock(), "projects/p/locations/l/apps/a", "Test App")
    mock_inst = mock_versions_cls.return_value

    agent1 = FakeProto(name="a1", display_name="Agent 1", instruction="Old inst", tools=["tools/t1"])
    agent2 = FakeProto(name="a1", display_name="Agent 1", instruction="New inst", tools=["tools/t1", "tools/t2"])

    tool1 = FakeProto(name="t1", display_name="Tool 1", description="Old tool")
    tool2 = FakeProto(name="t1", display_name="Tool 1", description="New tool")
    tool_add = FakeProto(name="t2", display_name="Tool 2", description="Added tool")

    gr1 = FakeProto(name="g1", display_name="GR 1", policy="Old gr")
    gr2 = FakeProto(name="g1", display_name="GR 1", policy="New gr")
    gr_add = FakeProto(name="g2", display_name="GR 2", policy="Added gr")

    ts1 = FakeProto(name="ts1", display_name="TS 1", tools=["t1"])
    ts2 = FakeProto(name="ts1", display_name="TS 1", tools=["t1", "t2"])
    ts_add = FakeProto(name="ts2", display_name="TS 2", tools=[])

    snap1 = FakeProto(
        app=FakeProto(display_name="App"),
        agents=[agent1],
        tools=[tool1],
        guardrails=[gr1],
        toolsets=[ts1],
    )
    snap2 = FakeProto(
        app=FakeProto(display_name="App"),
        agents=[agent2],
        tools=[tool2, tool_add],
        guardrails=[gr2, gr_add],
        toolsets=[ts2, ts_add],
    )

    v1 = FakeProto(display_name="V1", snapshot=snap1)
    v2 = FakeProto(display_name="V2", snapshot=snap2)

    mock_inst.get_version.side_effect = [v1, v2]
    app_versions_compare(_ns())
    assert mock_inst.get_version.call_count == 2


@patch("cxas_scrapi.cli.versions_cli.Console")
@patch("cxas_scrapi.cli.versions_cli.Versions")
@patch("cxas_scrapi.cli.versions_cli._resolve_app_args")
def test_app_versions_compare_exception(mock_resolve: MagicMock, mock_versions_cls: MagicMock, mock_console: MagicMock) -> None:
    """Test exception handling inside try block in app_versions_compare."""
    mock_resolve.return_value = (MagicMock(), "projects/p/locations/l/apps/a", "Test App")
    mock_versions_cls.side_effect = RuntimeError("Network error")
    with pytest.raises(SystemExit) as exc:
        app_versions_compare(_ns())
    assert exc.value.code == 1
    mock_console.return_value.print.assert_called()


def test_print_verbose_diff_directly() -> None:
    """Test direct execution of _print_verbose_diff formatting lines."""
    from cxas_scrapi.cli.versions_cli import _print_verbose_diff
    console = MagicMock()
    diff_blocks = [
        {
            "title": "📝 Modified Agent: Agent A",
            "diff": "--- a\n+++ b\n@@ -1,3 +1,3 @@\n-old line [foo]\n+new line [bar]\n unchanged line",
        }
    ]
    _print_verbose_diff(console, diff_blocks)
    assert console.print.call_count >= 5


def test_resolve_report_path_default(tmp_path: Any, monkeypatch: Any) -> None:
    """Test _resolve_report_path default behavior and pointer file reading."""
    from cxas_scrapi.cli.versions_cli import _generate_html_report
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".active-project").write_text("test_proj\n")

    console = MagicMock()
    args = _ns(output=None)
    v1 = FakeProto(display_name="V1", snapshot=FakeProto(app=FakeProto(display_name="App"), agents=[], tools=[], guardrails=[], toolsets=[]))
    v2 = FakeProto(display_name="V2", snapshot=FakeProto(app=FakeProto(display_name="App"), agents=[], tools=[], guardrails=[], toolsets=[]))

    _generate_html_report(console, "App", args, v1, v2, [])
    expected_dir = tmp_path / "test_proj" / "eval-reports" / "comparisons"
    assert expected_dir.exists()



