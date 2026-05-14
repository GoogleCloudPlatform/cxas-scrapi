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

import pytest
import yaml

from cxas_scrapi.utils.tracing import trace_config as tc_mod
from cxas_scrapi.utils.tracing.trace_config import TraceConfig


def _write(path, payload):
    with open(path, "w") as f:
        yaml.safe_dump(payload, f)


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        tc_mod, "USER_CONFIG_PATH", str(tmp_path / "no_user.yaml")
    )
    yield


def test_load_defaults_when_no_config_file_found():
    cfg = TraceConfig.load()
    assert cfg.audio.uri_pattern == "{bucket}/{conversation_id}.wav"
    assert cfg.cloud_logging.default_level == "WARNING"
    # Audio metrics now live in the registry; the YAML override map is empty
    # by default.
    assert cfg.gemini.audio_metrics == {}
    assert "hallucination" in cfg.gemini.triage_metrics
    assert (
        cfg.bug_report.path_template
        == "{model_version}/{date}/{user}/{severity}/{conversation_id}/"
    )


def test_load_explicit_path_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        TraceConfig.load(explicit_path=str(tmp_path / "nope.yaml"))


def test_explicit_path_overrides(tmp_path):
    p = tmp_path / "trace.yaml"
    _write(
        p,
        {
            "audio": {"download_dir": "./customdir"},
            "cloud_logging": {"default_level": "ERROR"},
        },
    )
    cfg = TraceConfig.load(explicit_path=str(p))
    assert cfg.audio.download_dir == "./customdir"
    assert cfg.cloud_logging.default_level == "ERROR"
    # Defaults still populate the rest.
    assert cfg.gemini.model == "gemini-3-flash-preview"
    assert cfg.gemini.thinking_level == "low"


def test_project_config_takes_precedence_over_user(tmp_path, monkeypatch):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / ".cxas").mkdir()
    _write(
        project_dir / ".cxas" / "trace.yaml",
        {"audio": {"download_dir": "./project"}},
    )
    user_path = tmp_path / "user.yaml"
    _write(user_path, {"audio": {"download_dir": "./user"}})
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(tc_mod, "USER_CONFIG_PATH", str(user_path))

    cfg = TraceConfig.load()
    assert cfg.audio.download_dir == "./project"


def test_user_config_used_when_no_project(monkeypatch, tmp_path):
    user_path = tmp_path / "user.yaml"
    _write(user_path, {"audio": {"download_dir": "./user-only"}})
    monkeypatch.setattr(tc_mod, "USER_CONFIG_PATH", str(user_path))
    cfg = TraceConfig.load()
    assert cfg.audio.download_dir == "./user-only"


def test_invalid_yaml_payload_raises(tmp_path):
    p = tmp_path / "trace.yaml"
    _write(p, {"audio": {"uri_pattern": 123}})  # int where str expected
    with pytest.raises(ValueError, match="Invalid trace config"):
        TraceConfig.load(explicit_path=str(p))


def test_empty_yaml_uses_defaults(tmp_path):
    p = tmp_path / "trace.yaml"
    p.write_text("")
    cfg = TraceConfig.load(explicit_path=str(p))
    assert cfg.audio.download_dir == "./.cxas/audio"
