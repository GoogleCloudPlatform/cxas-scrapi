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

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from cxas_scrapi.cli import trace_cli

APP = "projects/p/locations/l/apps/a"


def _ns(**overrides):
    base = dict(
        app_name=APP,
        app_dir=".",
        env_file=None,
        environment=None,
        config=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def fake_traces(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(trace_cli, "Traces", MagicMock(return_value=fake))
    return fake


def test_register_smoke():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    trace_cli.register(sub)
    args = parser.parse_args(
        ["trace", "list", "--app-name", APP, "--time-filter", "1d"]
    )
    assert args.func == trace_cli.trace_list
    assert args.time_filter == "1d"


def test_trace_list_table(fake_traces, capsys):
    fake_traces.list.return_value = [
        {
            "id": "c1",
            "source": "LIVE",
            "channel": "TEXT",
            "start_time": "s",
            "end_time": "e",
            "ces_url": "u",
        }
    ]
    trace_cli.trace_list(
        _ns(
            time_filter="7d",
            source=None,
            channel=None,
            limit=None,
            format="table",
        )
    )
    out = capsys.readouterr().out
    assert "Conversations" in out
    assert "c1" in out


def test_trace_list_json(fake_traces, capsys):
    fake_traces.list.return_value = [{"id": "c1"}]
    trace_cli.trace_list(
        _ns(
            time_filter="7d",
            source=None,
            channel=None,
            limit=None,
            format="json",
        )
    )
    out = capsys.readouterr().out
    assert json.loads(out)[0]["id"] == "c1"


def test_trace_list_csv(fake_traces, capsys):
    fake_traces.list.return_value = [{"id": "c1", "ces_url": "u"}]
    trace_cli.trace_list(
        _ns(
            time_filter="7d",
            source=None,
            channel=None,
            limit=None,
            format="csv",
        )
    )
    out = capsys.readouterr().out
    assert "id,ces_url" in out
    assert "c1,u" in out


def test_trace_list_csv_empty_no_output(fake_traces, capsys):
    fake_traces.list.return_value = []
    trace_cli.trace_list(
        _ns(
            time_filter="7d",
            source=None,
            channel=None,
            limit=None,
            format="csv",
        )
    )
    assert capsys.readouterr().out == ""


def test_trace_list_failure_exits(fake_traces, capsys):
    fake_traces.list.side_effect = RuntimeError("boom")
    with pytest.raises(SystemExit):
        trace_cli.trace_list(
            _ns(
                time_filter="7d",
                source=None,
                channel=None,
                limit=None,
                format="json",
            )
        )


def test_trace_get_stdout(fake_traces, capsys):
    fake_traces.get_report.return_value = "MD report"
    trace_cli.trace_get(
        _ns(
            conversation_id="c1",
            format="md",
            with_logs=False,
            log_level=None,
            with_audio=False,
            with_analysis=False,
            with_triage=False,
            out=None,
        )
    )
    assert "MD report" in capsys.readouterr().out


def test_trace_get_writes_file(fake_traces, tmp_path, capsys):
    fake_traces.get_report.return_value = "json body"
    out_path = tmp_path / "out.json"
    trace_cli.trace_get(
        _ns(
            conversation_id="c1",
            format="json",
            with_logs=True,
            log_level="ERROR",
            with_audio=True,
            with_analysis=True,
            with_triage=True,
            out=str(out_path),
        )
    )
    assert out_path.read_text() == "json body"
    assert "Wrote" in capsys.readouterr().out


def test_trace_get_failure(fake_traces):
    fake_traces.get_report.side_effect = RuntimeError("boom")
    with pytest.raises(SystemExit):
        trace_cli.trace_get(
            _ns(
                conversation_id="c1",
                format="md",
                with_logs=False,
                log_level=None,
                with_audio=False,
                with_analysis=False,
                with_triage=False,
                out=None,
            )
        )


def test_trace_logs_text(fake_traces, capsys):
    fake_traces.get_logs.return_value = [
        {"timestamp": "t", "severity": "WARNING", "message": "hi"}
    ]
    trace_cli.trace_logs(
        _ns(conversation_id="c1", level="WARNING", format="text")
    )
    out = capsys.readouterr().out
    assert "WARNING" in out


def test_trace_logs_json(fake_traces, capsys):
    fake_traces.get_logs.return_value = [{"a": 1}]
    trace_cli.trace_logs(
        _ns(conversation_id="c1", level="ERROR", format="json")
    )
    assert json.loads(capsys.readouterr().out) == [{"a": 1}]


def test_trace_logs_string_response(fake_traces, capsys):
    fake_traces.get_logs.return_value = "Cloud logging not enabled"
    trace_cli.trace_logs(
        _ns(conversation_id="c1", level="WARNING", format="text")
    )
    assert "not enabled" in capsys.readouterr().out


def test_trace_logs_failure(fake_traces):
    fake_traces.get_logs.side_effect = RuntimeError("boom")
    with pytest.raises(SystemExit):
        trace_cli.trace_logs(
            _ns(conversation_id="c1", level="WARNING", format="text")
        )


def test_trace_audio_download_success(fake_traces, capsys):
    fake_traces.download_audio.return_value = "/tmp/a.wav"
    trace_cli.trace_audio_download(_ns(conversation_id="c1", out=None))
    assert "Downloaded to" in capsys.readouterr().out


def test_trace_audio_download_no_audio(fake_traces, capsys):
    fake_traces.download_audio.return_value = None
    with pytest.raises(SystemExit) as exc:
        trace_cli.trace_audio_download(_ns(conversation_id="c1", out=None))
    assert exc.value.code == 2


def test_trace_audio_download_failure(fake_traces):
    fake_traces.download_audio.side_effect = RuntimeError("boom")
    with pytest.raises(SystemExit):
        trace_cli.trace_audio_download(_ns(conversation_id="c1", out=None))


def test_trace_audio_analyze(fake_traces, capsys):
    fake_traces.analyze_audio.return_value = {"audio_cutoff": "ok"}
    trace_cli.trace_audio_analyze(
        _ns(conversation_id="c1", metric="audio_cutoff,voice_drift")
    )
    out = capsys.readouterr().out
    assert "audio_cutoff" in out


def test_trace_audio_analyze_failure(fake_traces):
    fake_traces.analyze_audio.side_effect = RuntimeError("boom")
    with pytest.raises(SystemExit):
        trace_cli.trace_audio_analyze(_ns(conversation_id="c1", metric=None))


def test_trace_triage(fake_traces, capsys):
    fake_traces.triage.return_value = {"hallucination": "none"}
    trace_cli.trace_triage(_ns(conversation_id="c1", metric=None))
    out = capsys.readouterr().out
    assert "hallucination" in out


def test_trace_triage_failure(fake_traces):
    fake_traces.triage.side_effect = RuntimeError("boom")
    with pytest.raises(SystemExit):
        trace_cli.trace_triage(_ns(conversation_id="c1", metric=None))


def test_trace_replay_md(fake_traces, capsys):
    fake_traces.replay.return_value = {
        "diff": "+changed",
        "original": ["a"],
        "replay": ["b"],
    }
    trace_cli.trace_replay(_ns(conversation_id="c1", diff=True, format="md"))
    out = capsys.readouterr().out
    assert "Replay diff" in out
    assert "+changed" in out


def test_trace_replay_md_no_diff(fake_traces, capsys):
    fake_traces.replay.return_value = {"original": ["a"], "replay": ["a"]}
    trace_cli.trace_replay(_ns(conversation_id="c1", diff=True, format="md"))
    out = capsys.readouterr().out
    assert "original" in out


def test_trace_replay_json(fake_traces, capsys):
    fake_traces.replay.return_value = {"diff": "+x"}
    trace_cli.trace_replay(_ns(conversation_id="c1", diff=True, format="json"))
    assert json.loads(capsys.readouterr().out) == {"diff": "+x"}


def test_trace_replay_failure(fake_traces):
    fake_traces.replay.side_effect = RuntimeError("boom")
    with pytest.raises(SystemExit):
        trace_cli.trace_replay(
            _ns(conversation_id="c1", diff=True, format="md")
        )


def test_trace_stats_markdown(fake_traces, capsys):
    fake_traces.aggregate_stats.return_value = {
        "time_filter": "7d",
        "total": 2,
        "success_rate_no_transfer": 0.5,
        "duration_seconds": {"p50": 1, "p95": 2, "median": 1.5},
        "per_source": {"LIVE": 2},
        "per_channel": {"TEXT": 2},
        "top_tools": [("lookup", 5)],
        "top_transfer_targets": [("agent_b", 1)],
    }
    trace_cli.trace_stats(
        _ns(
            time_filter="7d",
            source=None,
            channel=None,
            limit=200,
            format="md",
            out=None,
        )
    )
    out = capsys.readouterr().out
    assert "Trace stats" in out
    assert "lookup" in out
    # ASCII bar uses solid blocks
    assert "█" in out
    # numerals + units appear
    assert "5 calls" in out


def test_trace_stats_markdown_no_transfers_branch(fake_traces, capsys):
    fake_traces.aggregate_stats.return_value = {
        "time_filter": "7d",
        "total": 0,
        "success_rate_no_transfer": None,
        "duration_seconds": {"p50": None, "p95": None, "median": None},
        "per_source": {},
        "per_channel": {},
        "top_tools": [],
        "top_transfer_targets": [],
    }
    trace_cli.trace_stats(
        _ns(
            time_filter="7d",
            source=None,
            channel=None,
            limit=200,
            format="md",
            out=None,
        )
    )
    out = capsys.readouterr().out
    assert "no transfers" in out
    assert "_(no data)_" in out


def test_trace_stats_json_to_file(fake_traces, tmp_path, capsys):
    fake_traces.aggregate_stats.return_value = {"total": 0, "time_filter": "7d"}
    p = tmp_path / "stats.json"
    trace_cli.trace_stats(
        _ns(
            time_filter="7d",
            source=None,
            channel=None,
            limit=200,
            format="json",
            out=str(p),
        )
    )
    assert json.loads(p.read_text())["total"] == 0
    assert "Wrote" in capsys.readouterr().out


def test_trace_stats_failure(fake_traces):
    fake_traces.aggregate_stats.side_effect = RuntimeError("boom")
    with pytest.raises(SystemExit):
        trace_cli.trace_stats(
            _ns(
                time_filter="7d",
                source=None,
                channel=None,
                limit=200,
                format="md",
                out=None,
            )
        )


def test_trace_bundle(fake_traces, tmp_path, capsys):
    fake_traces.bundle.return_value = "/tmp/out.zip"
    trace_cli.trace_bundle(
        _ns(
            conversation_id="c1",
            out=str(tmp_path / "out.zip"),
            no_logs=False,
            no_audio=False,
            with_analysis=False,
            with_triage=False,
        )
    )
    assert "out.zip" in capsys.readouterr().out


def test_trace_bundle_failure(fake_traces):
    fake_traces.bundle.side_effect = RuntimeError("boom")
    with pytest.raises(SystemExit):
        trace_cli.trace_bundle(
            _ns(
                conversation_id="c1",
                out="x.zip",
                no_logs=False,
                no_audio=False,
                with_analysis=False,
                with_triage=False,
            )
        )


def test_trace_bug_report(fake_traces, capsys):
    fake_traces.report_bug.return_value = {"reason": "r"}
    trace_cli.trace_bug_report(
        _ns(conversation_id="c1", reason="r", severity="high")
    )
    out = capsys.readouterr().out
    assert json.loads(out) == {"reason": "r"}


def test_trace_bug_report_failure(fake_traces):
    fake_traces.report_bug.side_effect = RuntimeError("boom")
    with pytest.raises(SystemExit):
        trace_cli.trace_bug_report(
            _ns(conversation_id="c1", reason="r", severity="high")
        )


def test_trace_open_prints_and_runs_open(fake_traces, capsys, monkeypatch):
    fake_traces.get_normalized.return_value = {"source": "LIVE"}
    fake_traces.console_url.return_value = "https://x/y"
    monkeypatch.setattr(trace_cli.platform, "system", lambda: "Darwin")
    fake_run = MagicMock()
    monkeypatch.setattr(trace_cli.subprocess, "run", fake_run)
    trace_cli.trace_open(_ns(conversation_id="c1"))
    assert "https://x/y" in capsys.readouterr().out
    fake_run.assert_called_once()
    fake_traces.get_normalized.assert_called_once_with("c1")
    fake_traces.console_url.assert_called_once_with("c1", source="LIVE")


def test_trace_open_non_darwin(fake_traces, capsys, monkeypatch):
    fake_traces.get_normalized.return_value = {"source": "LIVE"}
    fake_traces.console_url.return_value = "https://x/y"
    monkeypatch.setattr(trace_cli.platform, "system", lambda: "Linux")
    fake_run = MagicMock()
    monkeypatch.setattr(trace_cli.subprocess, "run", fake_run)
    trace_cli.trace_open(_ns(conversation_id="c1"))
    fake_run.assert_not_called()
    fake_traces.get_normalized.assert_called_once_with("c1")
    fake_traces.console_url.assert_called_once_with("c1", source="LIVE")


def test_trace_open_failure(fake_traces):
    fake_traces.get_normalized.side_effect = RuntimeError("boom")
    with pytest.raises(SystemExit):
        trace_cli.trace_open(_ns(conversation_id="c1"))


def test_trace_open_subprocess_failure_silent(fake_traces, monkeypatch, capsys):
    fake_traces.get_normalized.return_value = {"source": "LIVE"}
    fake_traces.console_url.return_value = "https://x/y"
    monkeypatch.setattr(trace_cli.platform, "system", lambda: "Darwin")

    def boom(*_a, **_kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(trace_cli.subprocess, "run", boom)
    trace_cli.trace_open(_ns(conversation_id="c1"))
    assert "https://x/y" in capsys.readouterr().out
    fake_traces.get_normalized.assert_called_once_with("c1")
    fake_traces.console_url.assert_called_once_with("c1", source="LIVE")


@patch("cxas_scrapi.cli.trace_cli.Traces")
def test_build_traces_passes_through_args(mock_traces_cls):
    args = _ns(
        app_dir="/tmp/app",
        env_file="/tmp/env.json",
        environment="dev",
        config="/tmp/trace.yaml",
    )
    trace_cli._build_traces(args)
    _, kwargs = mock_traces_cls.call_args
    assert kwargs["app_dir"] == "/tmp/app"
    assert kwargs["env_file"] == "/tmp/env.json"
    assert kwargs["environment"] == "dev"
    assert kwargs["trace_config_path"] == "/tmp/trace.yaml"
