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
import json
import os
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cxas_scrapi.core import traces as traces_mod
from cxas_scrapi.core.traces import Traces
from cxas_scrapi.utils.tracing.audio_analysis import ANALYSIS_REGISTRY
from cxas_scrapi.utils.tracing.trace_config import GeminiMetric

APP = "projects/p/locations/l/apps/a"


def _conv(
    cid="c1",
    source="LIVE",
    input_types=("INPUT_TYPE_TEXT",),
    start="2026-05-01T00:00:00",
    end="2026-05-01T00:01:00",
    turns=None,
):
    """Builds a fake CES Conversation-like object."""
    return SimpleNamespace(
        name=f"{APP}/conversations/{cid}",
        source=SimpleNamespace(name=source),
        input_types=list(input_types),
        start_time=(datetime.datetime.fromisoformat(start) if start else None),
        end_time=datetime.datetime.fromisoformat(end) if end else None,
        turns=turns or [],
    )


def _conv_dict(
    cid="c1",
    source="LIVE",
    input_types=("INPUT_TYPE_TEXT",),
    start="2026-05-01T00:00:00",
    end="2026-05-01T00:01:00",
    turns=None,
):
    return {
        "name": f"{APP}/conversations/{cid}",
        "source": source,
        "input_types": list(input_types),
        "start_time": start,
        "end_time": end,
        "turns": turns
        or [
            {
                "messages": [
                    {"role": "user", "chunks": [{"text": "hi"}]},
                    {
                        "role": "agent_x",
                        "chunks": [
                            {"text": "hello"},
                            {
                                "tool_call": {
                                    "display_name": "lookup",
                                    "args": {"q": 1},
                                }
                            },
                            {
                                "tool_response": {
                                    "display_name": "lookup",
                                    "response": {"r": 2},
                                }
                            },
                            {"agent_transfer": {"target_agent": "agent_b"}},
                        ],
                    },
                ]
            }
        ],
    }


@pytest.fixture
def traces_obj(tmp_path, monkeypatch):
    """Traces with no app dir; audio config from trace.yaml only."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(traces_mod.TraceConfig, "_pick_path", lambda *_: None)
    return Traces(app_name=APP, app_dir=str(tmp_path / "missing"))


def test_init_with_missing_app_dir_logs_and_continues(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    t = Traces(app_name=APP, app_dir=str(tmp_path / "no-app-dir"))
    assert t.app_config is None


def test_init_with_app_dir_loads_app_config(tmp_path, monkeypatch):
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "loggingSettings": {
                    "audioRecordingConfig": {"gcsBucket": "gs://from-app-json"}
                }
            }
        )
    )
    monkeypatch.chdir(tmp_path)
    t = Traces(app_name=APP, app_dir=str(tmp_path))
    assert t.app_config is not None
    assert t.app_config.audio_bucket() == "gs://from-app-json"


def test_list_filters_by_channel(traces_obj):
    text_conv = _conv("c-text", input_types=["INPUT_TYPE_TEXT"])
    audio_conv = _conv("c-audio", input_types=["INPUT_TYPE_AUDIO"])
    multi_conv = _conv(
        "c-multi", input_types=["INPUT_TYPE_TEXT", "INPUT_TYPE_AUDIO"]
    )
    traces_obj.history = MagicMock()
    traces_obj.history.list_conversations.return_value = [
        text_conv,
        audio_conv,
        multi_conv,
    ]

    rows_audio = traces_obj.list(channel_filter="AUDIO")
    assert [r["id"] for r in rows_audio] == ["c-audio"]

    rows_text = traces_obj.list(channel_filter="TEXT")
    assert [r["id"] for r in rows_text] == ["c-text"]

    rows_multi = traces_obj.list(channel_filter="MULTIMODAL")
    assert [r["id"] for r in rows_multi] == ["c-multi"]

    rows_all = traces_obj.list()
    assert len(rows_all) == 3


def test_list_respects_limit(traces_obj):
    traces_obj.history = MagicMock()
    traces_obj.history.list_conversations.return_value = [
        _conv("a"),
        _conv("b"),
        _conv("c"),
    ]
    rows = traces_obj.list(limit=2)
    assert [r["id"] for r in rows] == ["a", "b"]


def test_get_normalized(traces_obj):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    n = traces_obj.get_normalized("c1")
    assert n["conversation_id"] == "c1"
    assert n["num_turns"] == 1


def test_get_report_all_formats(traces_obj):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()

    j = traces_obj.get_report("c1", fmt="json")
    assert json.loads(j)["conversation_id"] == "c1"

    md = traces_obj.get_report("c1", fmt="md")
    assert "Conversation `c1`" in md

    md2 = traces_obj.get_report("c1", fmt="markdown")
    assert md == md2

    text = traces_obj.get_report("c1", fmt="text")
    assert "USER:" in text

    html = traces_obj.get_report("c1", fmt="html")
    assert "<html" in html


def test_get_report_unknown_format_raises(traces_obj):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    with pytest.raises(ValueError, match="Unknown format"):
        traces_obj.get_report("c1", fmt="xml")


def test_get_report_with_logs_audio_analysis_triage(traces_obj):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.get_logs = MagicMock(return_value=[{"msg": "log"}])
    traces_obj.download_audio = MagicMock(return_value="/tmp/audio.wav")
    traces_obj.analyze_audio = MagicMock(
        return_value={"agent_cutoff": {"result": "PASS"}}
    )
    traces_obj.triage = MagicMock(return_value={"hallucination": "none"})

    out = traces_obj.get_report(
        "c1",
        fmt="json",
        with_logs=True,
        with_audio=True,
        with_analysis=True,
        with_triage=True,
    )
    payload = json.loads(out)
    assert "Cloud Logs" in payload["extras"]
    assert "Saved to" in payload["extras"]["Audio"]
    assert "Audio Analysis" in payload["extras"]
    assert "Transcript Triage" in payload["extras"]


def test_get_report_audio_download_failure(traces_obj):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.download_audio = MagicMock(side_effect=RuntimeError("fail"))
    out = traces_obj.get_report("c1", fmt="json", with_audio=True)
    payload = json.loads(out)
    assert "Download failed" in payload["extras"]["Audio"]


def test_get_report_no_audio_returns_message(traces_obj):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.download_audio = MagicMock(return_value=None)
    out = traces_obj.get_report("c1", fmt="json", with_audio=True)
    payload = json.loads(out)
    assert payload["extras"]["Audio"] == "No audio available."


def test_get_logs_when_app_disables_cloud_logging(tmp_path, monkeypatch):
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "loggingSettings": {
                    "cloudLoggingSettings": {"enableCloudLogging": False}
                }
            }
        )
    )
    monkeypatch.chdir(tmp_path)
    t = Traces(app_name=APP, app_dir=str(tmp_path))
    msg = t.get_logs("c1", normalized={"start_time": None, "end_time": None})
    assert "not enabled" in msg


def test_get_logs_no_project_id(traces_obj):
    traces_obj.project_id = None
    msg = traces_obj.get_logs(
        "c1", normalized={"start_time": None, "end_time": None}
    )
    assert "project ID is unknown" in msg


def test_get_logs_invokes_cloud_logs_client(traces_obj, monkeypatch):
    fake_client = MagicMock()
    fake_client.fetch.return_value = [{"timestamp": "t", "message": "m"}]
    monkeypatch.setattr(
        traces_mod, "CloudLogsClient", MagicMock(return_value=fake_client)
    )
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    rows = traces_obj.get_logs("c1")
    assert rows == [{"timestamp": "t", "message": "m"}]
    fake_client.fetch.assert_called_once()


def test_resolve_audio_uri_uses_payload_audio_uri(traces_obj):
    traces_obj.history = MagicMock()
    d = _conv_dict(
        turns=[
            {
                "messages": [
                    {
                        "role": "agent_x",
                        "chunks": [
                            {"payload": {"audioUri": "gs://from-payload/a.wav"}}
                        ],
                    }
                ]
            }
        ]
    )
    traces_obj.history.get_conversation.return_value = d
    assert traces_obj.resolve_audio_uri("c1") == "gs://from-payload/a.wav"


def test_resolve_audio_uri_no_bucket_returns_none(traces_obj):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    assert traces_obj.resolve_audio_uri("c1") is None


def test_resolve_audio_uri_uses_trace_config_override(traces_obj, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.trace_config.audio.bucket_override = "gs://override"
    traces_obj.trace_config.audio.search_bucket = False
    uri = traces_obj.resolve_audio_uri("c1")
    assert uri == "gs://override/c1.wav"


def test_resolve_audio_uri_search_bucket_finds_via_listing(
    traces_obj, monkeypatch
):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.trace_config.audio.bucket_override = "gs://b"
    traces_obj.trace_config.audio.search_bucket = True
    fake_gcs = MagicMock()
    fake_gcs.exists.return_value = False
    fake_gcs.find_first.return_value = (
        "gs://b/projnum/us/app-x/2026-05-01/c1/full-session.wav"
    )
    monkeypatch.setattr(
        traces_mod, "GCSUtils", MagicMock(return_value=fake_gcs)
    )
    uri = traces_obj.resolve_audio_uri("c1")
    assert uri.endswith("/c1/full-session.wav")
    fake_gcs.find_first.assert_called_once()


def test_resolve_audio_uri_search_bucket_returns_none_when_missing(
    traces_obj, monkeypatch
):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.trace_config.audio.bucket_override = "gs://b"
    traces_obj.trace_config.audio.search_bucket = True
    fake_gcs = MagicMock()
    fake_gcs.exists.side_effect = RuntimeError("nope")
    fake_gcs.find_first.return_value = None
    monkeypatch.setattr(
        traces_mod, "GCSUtils", MagicMock(return_value=fake_gcs)
    )
    assert traces_obj.resolve_audio_uri("c1") is None


def test_resolve_audio_uri_uses_app_config_bucket(tmp_path, monkeypatch):
    (tmp_path / "app.json").write_text(
        json.dumps(
            {
                "loggingSettings": {
                    "audioRecordingConfig": {"gcsBucket": "gs://from-app"}
                }
            }
        )
    )
    monkeypatch.chdir(tmp_path)
    t = Traces(app_name=APP, app_dir=str(tmp_path))
    t.history = MagicMock()
    t.history.get_conversation.return_value = _conv_dict()
    t.trace_config.audio.search_bucket = False
    assert t.resolve_audio_uri("c1") == "gs://from-app/c1.wav"


def test_download_audio_success(traces_obj, tmp_path, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.trace_config.audio.bucket_override = "gs://b"
    traces_obj.trace_config.audio.search_bucket = False
    traces_obj.trace_config.audio.download_dir = str(tmp_path / "audio")

    fake_gcs = MagicMock()
    fake_gcs.download_to_file.return_value = str(tmp_path / "audio" / "c1.wav")
    monkeypatch.setattr(
        traces_mod, "GCSUtils", MagicMock(return_value=fake_gcs)
    )
    out = traces_obj.download_audio("c1")
    assert out.endswith("c1.wav")
    fake_gcs.download_to_file.assert_called_once()


def test_download_audio_no_uri_returns_none(traces_obj):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    assert traces_obj.download_audio("c1") is None


_FAKE_FILES = [
    "gs://b/p/conv-1/METADATA.json",
    "gs://b/p/conv-1/full-session.wav",
    "gs://b/p/conv-1/agent-turn-1.wav",
    "gs://b/p/conv-1/agent-turn-2.wav",
    "gs://b/p/conv-1/user-turn-1.wav",
]


def _patch_audio_files(traces_obj, monkeypatch, files=_FAKE_FILES):
    traces_obj.list_audio_files = MagicMock(return_value=files)
    monkeypatch.setattr(
        traces_mod.genai.types.Part, "from_uri", lambda **kw: "audio_part"
    )


def test_analyze_audio_no_files(traces_obj, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.list_audio_files = MagicMock(return_value=[])
    assert traces_obj.analyze_audio("c1") == {
        "error": "No audio files found for this conversation."
    }


def test_analyze_audio_runs_named_subset(traces_obj, monkeypatch):
    _patch_audio_files(traces_obj, monkeypatch)
    fake_gem = MagicMock()
    fake_gem.generate_with_parts.side_effect = [
        "PASS — voices match",
        "FAIL — pause at 0:42",
    ]
    monkeypatch.setattr(
        traces_mod, "GeminiGenerate", MagicMock(return_value=fake_gem)
    )
    out = traces_obj.analyze_audio(
        "c1", metrics=["agent_voice_consistency", "no_long_pauses"]
    )
    assert set(out.keys()) == {"agent_voice_consistency", "no_long_pauses"}
    assert out["agent_voice_consistency"]["result"] == "PASS"
    assert out["no_long_pauses"]["result"] == "FAIL"
    # Voice consistency only sees agent-turn files
    assert all(
        "agent-turn" in f
        for f in out["agent_voice_consistency"]["files_analyzed"]
    )
    # No-long-pauses only sees full-session
    assert out["no_long_pauses"]["files_analyzed"] == [
        "gs://b/p/conv-1/full-session.wav"
    ]


def test_analyze_audio_runs_all_when_metrics_none(traces_obj, monkeypatch):
    _patch_audio_files(traces_obj, monkeypatch)
    fake_gem = MagicMock()
    fake_gem.generate_with_parts.return_value = "PASS — looks fine"
    monkeypatch.setattr(
        traces_mod, "GeminiGenerate", MagicMock(return_value=fake_gem)
    )
    out = traces_obj.analyze_audio("c1")
    assert set(out.keys()) == set(ANALYSIS_REGISTRY.keys())


def test_analyze_audio_skips_unknown_metric(traces_obj, monkeypatch, caplog):
    _patch_audio_files(traces_obj, monkeypatch)
    fake_gem = MagicMock()
    fake_gem.generate_with_parts.return_value = "PASS — ok"
    monkeypatch.setattr(
        traces_mod, "GeminiGenerate", MagicMock(return_value=fake_gem)
    )
    out = traces_obj.analyze_audio(
        "c1", metrics=["agent_cutoff", "definitely_not_real"]
    )
    assert set(out.keys()) == {"agent_cutoff"}


def test_analyze_audio_skip_when_filter_returns_empty(traces_obj, monkeypatch):
    """If a registered analysis sees no matching files, it returns SKIP."""
    _patch_audio_files(
        traces_obj,
        monkeypatch,
        # No agent-turn-*.wav files — voice consistency must skip.
        files=["gs://b/p/conv-1/full-session.wav"],
    )
    fake_gem = MagicMock()
    monkeypatch.setattr(
        traces_mod, "GeminiGenerate", MagicMock(return_value=fake_gem)
    )
    out = traces_obj.analyze_audio("c1", metrics=["agent_voice_consistency"])
    assert out["agent_voice_consistency"]["result"] == "SKIP"
    fake_gem.generate_with_parts.assert_not_called()


def test_analyze_audio_uses_yaml_prompt_override(traces_obj, monkeypatch):
    _patch_audio_files(traces_obj, monkeypatch)
    traces_obj.trace_config.gemini.audio_metrics = {
        "agent_cutoff": GeminiMetric(prompt="custom override prompt"),
    }
    fake_gem = MagicMock()
    fake_gem.generate_with_parts.return_value = "PASS"
    monkeypatch.setattr(
        traces_mod, "GeminiGenerate", MagicMock(return_value=fake_gem)
    )
    traces_obj.analyze_audio("c1", metrics=["agent_cutoff"])
    args, kwargs = fake_gem.generate_with_parts.call_args
    parts = kwargs["parts"]
    # Last part is the prompt string; preceding ones are audio Parts.
    assert parts[-1] == "custom override prompt"


def test_analyze_audio_handles_gemini_none(traces_obj, monkeypatch):
    _patch_audio_files(traces_obj, monkeypatch)
    fake_gem = MagicMock()
    fake_gem.generate_with_parts.return_value = None
    monkeypatch.setattr(
        traces_mod, "GeminiGenerate", MagicMock(return_value=fake_gem)
    )
    out = traces_obj.analyze_audio("c1", metrics=["agent_cutoff"])
    assert out["agent_cutoff"]["result"] == "ERROR"


def test_analyze_audio_handles_unparseable_response(traces_obj, monkeypatch):
    _patch_audio_files(traces_obj, monkeypatch)
    fake_gem = MagicMock()
    fake_gem.generate_with_parts.return_value = "no verdict here"
    monkeypatch.setattr(
        traces_mod, "GeminiGenerate", MagicMock(return_value=fake_gem)
    )
    out = traces_obj.analyze_audio("c1", metrics=["agent_cutoff"])
    assert out["agent_cutoff"]["result"] == "UNKNOWN"
    assert out["agent_cutoff"]["justification"] == "no verdict here"


def test_list_audio_files_uses_gcs_listing(traces_obj, monkeypatch):
    traces_obj.trace_config.audio.bucket_override = "gs://b"
    fake_gcs = MagicMock()
    fake_gcs.find_dir_for_conversation.return_value = "p/d/conv-1/"
    fake_gcs.list_with_prefix.return_value = _FAKE_FILES
    monkeypatch.setattr(
        traces_mod, "GCSUtils", MagicMock(return_value=fake_gcs)
    )
    assert traces_obj.list_audio_files("conv-1") == _FAKE_FILES
    fake_gcs.find_dir_for_conversation.assert_called_once_with(
        "gs://b", conversation_id="conv-1"
    )


def test_list_audio_files_returns_empty_when_no_bucket(traces_obj):
    assert traces_obj.list_audio_files("c1") == []


def test_list_audio_files_returns_empty_when_dir_not_found(
    traces_obj, monkeypatch
):
    traces_obj.trace_config.audio.bucket_override = "gs://b"
    fake_gcs = MagicMock()
    fake_gcs.find_dir_for_conversation.return_value = None
    monkeypatch.setattr(
        traces_mod, "GCSUtils", MagicMock(return_value=fake_gcs)
    )
    assert traces_obj.list_audio_files("c1") == []


def test_parse_pass_fail_branches():
    assert traces_mod._parse_pass_fail(None)["result"] == "ERROR"
    assert traces_mod._parse_pass_fail("PASS — all good")["result"] == "PASS"
    assert traces_mod._parse_pass_fail("FAIL — bad call")["result"] == "FAIL"
    # Sentence with PASS/FAIL embedded as a separate token.
    assert (
        traces_mod._parse_pass_fail("Result is PASS overall")["result"]
        == "PASS"
    )
    assert (
        traces_mod._parse_pass_fail("Final verdict: FAIL — bad")["result"]
        == "FAIL"
    )
    assert traces_mod._parse_pass_fail("nothing useful")["result"] == "UNKNOWN"


def test_triage_runs_metrics(traces_obj, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    fake_gem = MagicMock()
    fake_gem.generate.side_effect = lambda prompt, **_: f"resp:{prompt[:20]}"
    monkeypatch.setattr(
        traces_mod, "GeminiGenerate", MagicMock(return_value=fake_gem)
    )
    out = traces_obj.triage("c1", metrics=["hallucination"])
    assert list(out.keys()) == ["hallucination"]


def test_triage_runs_all_metrics_when_none_specified(traces_obj, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    fake_gem = MagicMock()
    fake_gem.generate.return_value = "x"
    monkeypatch.setattr(
        traces_mod, "GeminiGenerate", MagicMock(return_value=fake_gem)
    )
    out = traces_obj.triage("c1")
    assert set(out.keys()) == set(
        traces_obj.trace_config.gemini.triage_metrics.keys()
    )


def test_replay_with_diff(traces_obj, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()

    fake_sess = MagicMock()
    fake_sess.create_session_id.return_value = "sid"
    fake_sess.run.return_value = "raw"
    fake_sess.get_structured_response.return_value = {
        "agent_text": "different reply",
    }
    monkeypatch.setattr(
        traces_mod, "Sessions", MagicMock(return_value=fake_sess)
    )

    out = traces_obj.replay("c1")
    assert out["original"] == ["hello"]
    assert out["replay"] == ["different reply"]
    assert "+different reply" in out["diff"]


def test_replay_handles_session_run_failure(traces_obj, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    fake_sess = MagicMock()
    fake_sess.create_session_id.return_value = "sid"
    fake_sess.run.side_effect = RuntimeError("nope")
    monkeypatch.setattr(
        traces_mod, "Sessions", MagicMock(return_value=fake_sess)
    )
    out = traces_obj.replay("c1", diff=False)
    assert out["replay"] == ["<replay error: nope>"]
    assert "diff" not in out


def test_aggregate_stats(traces_obj, monkeypatch):
    convs = [
        _conv("a", input_types=["INPUT_TYPE_TEXT"]),
        _conv("b", input_types=["INPUT_TYPE_AUDIO"]),
    ]
    traces_obj.history = MagicMock()
    traces_obj.history.list_conversations.return_value = convs

    def fake_get_normalized(cid):
        return {
            "conversation_id": cid,
            "source": "LIVE",
            "channel": "AUDIO" if cid == "b" else "TEXT",
            "start_time": "2026-05-01T00:00:00",
            "end_time": "2026-05-01T00:00:30",
            "entries": (
                [
                    {"kind": "tool_call", "tool": "lookup"},
                    {"kind": "agent_transfer", "target": "agent_b"},
                ]
                if cid == "a"
                else [{"kind": "tool_call", "tool": "calc"}]
            ),
        }

    traces_obj.get_normalized = fake_get_normalized
    stats = traces_obj.aggregate_stats(time_filter="7d")
    assert stats["total"] == 2
    assert stats["per_source"]["LIVE"] == 2
    assert stats["per_channel"]["TEXT"] == 1
    assert stats["per_channel"]["AUDIO"] == 1
    assert stats["success_rate_no_transfer"] == 0.5
    assert stats["duration_seconds"]["p50"] == 30.0
    tools = dict(stats["top_tools"])
    assert tools["lookup"] == 1 and tools["calc"] == 1


def test_aggregate_stats_handles_fetch_error(traces_obj, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.list_conversations.return_value = [_conv("a")]
    traces_obj.get_normalized = MagicMock(side_effect=RuntimeError("err"))
    stats = traces_obj.aggregate_stats(time_filter="7d")
    assert stats["total"] == 0


def test_bundle_creates_zip(traces_obj, tmp_path, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.trace_config.audio.bucket_override = "gs://b"
    traces_obj.trace_config.audio.search_bucket = False

    audio_local = tmp_path / "c1.wav"
    audio_local.write_bytes(b"audio")
    fake_gcs = MagicMock()
    fake_gcs.download_to_file.return_value = str(audio_local)
    monkeypatch.setattr(
        traces_mod, "GCSUtils", MagicMock(return_value=fake_gcs)
    )

    fake_logs_client = MagicMock()
    fake_logs_client.fetch.return_value = [{"msg": "x"}]
    monkeypatch.setattr(
        traces_mod,
        "CloudLogsClient",
        MagicMock(return_value=fake_logs_client),
    )

    fake_gem = MagicMock()
    fake_gem.generate_with_parts.return_value = "audio finding"
    fake_gem.generate.return_value = "triage finding"
    monkeypatch.setattr(
        traces_mod, "GeminiGenerate", MagicMock(return_value=fake_gem)
    )
    monkeypatch.setattr(
        traces_mod.genai.types.Part, "from_uri", lambda **kw: "audio_part"
    )

    out = tmp_path / "out.zip"
    traces_obj.bundle(
        "c1",
        out_path=str(out),
        with_analysis=True,
        with_triage=True,
    )
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
    assert "transcript.json" in names
    assert "transcript.md" in names
    assert "report.html" in names
    assert "logs.json" in names
    assert "metadata.json" in names
    assert "gemini_analysis.json" in names
    assert "triage.json" in names
    assert "c1.wav" in names


def test_bundle_skips_failing_audio_download(traces_obj, tmp_path, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.download_audio = MagicMock(side_effect=RuntimeError("nope"))
    out = tmp_path / "out.zip"
    traces_obj.bundle("c1", out_path=str(out), with_logs=False, with_audio=True)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
    assert "transcript.json" in names
    assert "c1.wav" not in names


def test_report_bug_requires_bucket(traces_obj):
    with pytest.raises(ValueError, match="bug_report.bucket"):
        traces_obj.report_bug("c1", reason="r")


def test_report_bug_uploads_artifacts(traces_obj, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.trace_config.bug_report.bucket = "gs://bugs"
    traces_obj.trace_config.audio.bucket_override = "gs://b"
    traces_obj.trace_config.audio.search_bucket = False

    fake_gcs = MagicMock()
    fake_gcs.upload_string.side_effect = lambda uri, *_args, **_kw: uri
    fake_gcs.upload_file.side_effect = lambda uri, *_args, **_kw: uri
    fake_gcs.download_to_file.return_value = "/tmp/c1.wav"
    monkeypatch.setattr(
        traces_mod, "GCSUtils", MagicMock(return_value=fake_gcs)
    )

    # Pretend the audio file exists locally so upload_file is reached.
    monkeypatch.setattr(os.path, "exists", lambda p: p == "/tmp/c1.wav")

    fake_logs = MagicMock()
    fake_logs.fetch.return_value = [{"msg": "x"}]
    monkeypatch.setattr(
        traces_mod, "CloudLogsClient", MagicMock(return_value=fake_logs)
    )
    fake_gem = MagicMock()
    fake_gem.generate_with_parts.return_value = "ok"
    monkeypatch.setattr(
        traces_mod, "GeminiGenerate", MagicMock(return_value=fake_gem)
    )
    monkeypatch.setattr(
        traces_mod.genai.types.Part, "from_uri", lambda **kw: "p"
    )
    monkeypatch.setattr(traces_mod, "_gcloud_account", lambda: "u@x.com")

    info = traces_obj.report_bug("c1", reason="bug", severity="high")
    assert info["reason"] == "bug"
    assert info["severity"] == "high"
    assert "transcript.json" in info["uploaded"]
    assert "audio" in info["uploaded"]
    assert "logs.json" in info["uploaded"]
    assert "gemini_analysis.json" in info["uploaded"]
    assert "environment.json" in info["uploaded"]
    assert "bug_report.json" in info["uploaded"]


def test_report_bug_skips_failing_artifacts(traces_obj, monkeypatch):
    traces_obj.history = MagicMock()
    traces_obj.history.get_conversation.return_value = _conv_dict()
    traces_obj.trace_config.bug_report.bucket = "gs://bugs"
    traces_obj.trace_config.bug_report.include = [
        "transcript",
        "logs",
        "audio",
        "gemini_analysis",
        "environment",
    ]

    fake_gcs = MagicMock()
    fake_gcs.upload_string.side_effect = lambda uri, *_a, **_k: uri
    monkeypatch.setattr(
        traces_mod, "GCSUtils", MagicMock(return_value=fake_gcs)
    )
    traces_obj.get_logs = MagicMock(side_effect=RuntimeError("logs fail"))
    traces_obj.download_audio = MagicMock(side_effect=RuntimeError("a fail"))
    traces_obj.analyze_audio = MagicMock(side_effect=RuntimeError("g fail"))
    info = traces_obj.report_bug("c1", reason="r")
    assert "audio" not in info["uploaded"]
    assert "gemini_analysis.json" not in info["uploaded"]


def test_console_url_format(traces_obj):
    # 1. Without source
    url = traces_obj.console_url("conv-1")
    assert url == (
        "https://ces.cloud.google.com/projects/p/locations/l/apps/a"
        "?panel=conversation_list&id=conv-1"
    )

    # 2. With source
    url_with_source = traces_obj.console_url("conv-1", source="LIVE")
    assert url_with_source == (
        "https://ces.cloud.google.com/projects/p/locations/l/apps/a"
        "?panel=conversation_list&id=conv-1&source=LIVE"
    )


def test_metadata_collects_fields(traces_obj, monkeypatch):
    monkeypatch.setattr(traces_mod, "_gcloud_account", lambda: "kr@x")
    monkeypatch.setattr(traces_mod, "_git_sha", lambda: "abc123")
    md = traces_obj._metadata()
    assert md["app_name"] == APP
    assert md["gcloud_account"] == "kr@x"
    assert md["git_sha"] == "abc123"


def test_helpers_pure_logic():
    assert traces_mod._enum_name(None) is None
    assert traces_mod._enum_name(SimpleNamespace(name="X")) == "X"
    assert traces_mod._ts_iso(None) is None
    assert traces_mod._ts_iso("s") == "s"
    assert traces_mod._ts_iso(datetime.datetime(2026, 5, 1)).startswith(
        "2026-05-01"
    )
    assert traces_mod._parse_iso(None) is None
    assert traces_mod._parse_iso("not-a-date") is None
    assert traces_mod._parse_iso("2026-05-01T00:00:00Z") == datetime.datetime(
        2026, 5, 1, tzinfo=datetime.timezone.utc
    )
    assert traces_mod._duration_seconds(None, None) is None
    assert (
        traces_mod._duration_seconds(
            "2026-05-01T00:00:00", "2026-05-01T00:00:30"
        )
        == 30.0
    )
    assert traces_mod._percentile([], 50) is None
    assert traces_mod._percentile([1, 2, 3, 4], 50) == 3
    assert traces_mod._top_n({"a": 1, "b": 5, "c": 2}, 2) == [
        ("b", 5),
        ("c", 2),
    ]


def test_git_sha_and_gcloud_account_failures(monkeypatch):
    def boom(*_a, **_kw):
        raise FileNotFoundError("nope")

    monkeypatch.setattr(traces_mod.subprocess, "check_output", boom)
    assert traces_mod._git_sha() is None
    assert traces_mod._gcloud_account() is None


def test_git_sha_and_gcloud_account_success(monkeypatch):
    monkeypatch.setattr(
        traces_mod.subprocess, "check_output", lambda *a, **k: b"abc\n"
    )
    assert traces_mod._git_sha() == "abc"
    assert traces_mod._gcloud_account() == "abc"


def test_agent_text_per_turn():
    n = {
        "entries": [
            {"kind": "user", "turn": 0, "text": "hi"},
            {"kind": "agent", "turn": 0, "text": "hello"},
            {"kind": "agent", "turn": 1, "text": "second"},
            {"kind": "agent", "turn": 0, "text": "world"},
        ]
    }
    assert traces_mod._agent_text_per_turn(n) == ["hello world", "second"]
