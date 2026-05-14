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

"""Public `Traces` API for the `cxas trace` CLI surface.

Composes existing pieces (`ConversationHistory`, `Sessions`, `LatencyParser`,
`GeminiGenerate`, `GCSUtils`) with new helpers (`AppConfig`, `TraceConfig`,
`CloudLogsClient`, `trace_report` formatters) into one orchestration class
analogous to `ConversationHistory` and `Sessions`.

Methods are intentionally small and composable so they can also be called from
notebooks / SDK consumers, not only from the CLI.
"""

# `Traces.list` shadows the built-in `list` inside the class body, so we defer
# all annotation evaluation to keep `list[...]` parsing as a generic.
from __future__ import annotations

import datetime
import difflib
import json
import logging
import os
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import median
from typing import Any

from google import genai

from cxas_scrapi.core.common import Common
from cxas_scrapi.core.conversation_history import ConversationHistory
from cxas_scrapi.core.sessions import Modality, Sessions
from cxas_scrapi.utils.gcs_utils import GCSUtils
from cxas_scrapi.utils.gemini import GeminiGenerate
from cxas_scrapi.utils.tracing import trace_report
from cxas_scrapi.utils.tracing.app_config import AppConfig
from cxas_scrapi.utils.tracing.audio_analysis import (
    ANALYSIS_REGISTRY,
    AudioAnalysis,
)
from cxas_scrapi.utils.tracing.cloud_logging import CloudLogsClient
from cxas_scrapi.utils.tracing.trace_config import TraceConfig

logger = logging.getLogger(__name__)


class Traces(Common):
    """Orchestrates listing, fetching, enriching and analyzing conversations.

    Construction is cheap: it loads the trace config and (optionally) the
    pulled-app config, and instantiates a `ConversationHistory` client. The
    heavier clients (Cloud Logging, Gemini, GCS) are lazily created on first
    use so a `cxas trace list` call does not require any of those packages
    to be reachable.
    """

    def __init__(
        self,
        app_name: str,
        app_dir: str = ".",
        env_file: str | None = None,
        environment: str | None = None,
        trace_config_path: str | None = None,
        creds_path: str | None = None,
        creds_dict: dict[str, str] | None = None,
        creds: Any = None,
        scope: list[str] | None = None,
        **kwargs: Any,
    ):
        super().__init__(
            creds_path=creds_path,
            creds_dict=creds_dict,
            creds=creds,
            scope=scope,
            app_name=app_name,
            **kwargs,
        )
        self.history = ConversationHistory(
            app_name=app_name,
            creds_path=creds_path,
            creds_dict=creds_dict,
            creds=creds,
            scope=scope,
            **kwargs,
        )
        self.trace_config = TraceConfig.load(trace_config_path)
        # AppConfig is optional — `cxas trace list` works without a pulled app.
        self.app_config: AppConfig | None
        try:
            self.app_config = AppConfig.load(
                app_dir=app_dir,
                env_file=env_file,
                environment=environment,
            )
        except FileNotFoundError as e:
            logger.info(
                f"No local app.json found ({e}); audio/log discovery will "
                f"depend on `--bucket-override` / `trace.yaml` settings only."
            )
            self.app_config = None

    # -------------------------- listing & fetching --------------------------

    def list(
        self,
        time_filter: str | None = "7d",
        source_filter: str | None = None,
        channel_filter: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lists conversations as summary dicts ready for rendering."""
        convs = self.history.list_conversations(
            time_filter=time_filter,
            source_filter=source_filter,
        )
        rows: list[dict[str, Any]] = []
        for c in convs:
            normalized_channel = trace_report._channel_label(
                getattr(c, "input_types", []) or []
            )
            if channel_filter and normalized_channel != channel_filter.upper():
                continue
            rows.append(
                {
                    "id": c.name.split("/")[-1],
                    "name": c.name,
                    "source": _enum_name(getattr(c, "source", None)),
                    "channel": normalized_channel,
                    "start_time": _ts_iso(getattr(c, "start_time", None)),
                    "end_time": _ts_iso(getattr(c, "end_time", None)),
                    "ces_url": self.console_url(c.name.split("/")[-1]),
                }
            )
            if limit and len(rows) >= limit:
                break
        return rows

    def get_normalized(self, conversation_id: str) -> dict[str, Any]:
        """Fetches and normalizes a single conversation."""
        conv = self.history.get_conversation(conversation_id)
        return trace_report.normalize(conv)

    # ----------------------------- reporting --------------------------------

    def get_report(
        self,
        conversation_id: str,
        fmt: str = "json",
        with_logs: bool = False,
        log_level: str | None = None,
        with_audio: bool = False,
        with_analysis: bool = False,
        with_triage: bool = False,
    ) -> str:
        """Builds a single trace report in the requested format."""
        normalized = self.get_normalized(conversation_id)
        extras: dict[str, Any] = {}
        audio_local_path: str | None = None

        if with_logs:
            cloud_default = self.trace_config.cloud_logging.default_level
            extras["Cloud Logs"] = self.get_logs(
                conversation_id,
                level=log_level or cloud_default,
                normalized=normalized,
            )
        if with_audio:
            try:
                audio_local_path = self.download_audio(
                    conversation_id, normalized=normalized
                )
                extras["Audio"] = (
                    f"Saved to: {audio_local_path}"
                    if audio_local_path
                    else "No audio available."
                )
            except Exception as e:
                extras["Audio"] = f"Download failed: {e}"
        if with_analysis:
            extras["Audio Analysis"] = self.analyze_audio(
                conversation_id,
                normalized=normalized,
                audio_local_path=audio_local_path,
            )
        if with_triage:
            extras["Transcript Triage"] = self.triage(
                conversation_id, normalized=normalized
            )

        url = self.console_url(conversation_id)
        if fmt == "json":
            return trace_report.to_json(normalized, extras=extras)
        if fmt in ("md", "markdown"):
            return trace_report.to_markdown(
                normalized, console_url=url, extras=extras
            )
        if fmt == "text":
            return trace_report.to_text(normalized, extras=extras)
        if fmt == "html":
            return trace_report.to_html(
                normalized,
                console_url=url,
                extras=extras,
                audio_path=audio_local_path,
            )
        raise ValueError(f"Unknown format: {fmt}")

    # ----------------------------- cloud logs -------------------------------

    def get_logs(
        self,
        conversation_id: str,
        level: str = "WARNING",
        normalized: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | str:
        """Fetches Cloud Logging entries for a conversation."""
        if self.app_config and not self.app_config.cloud_logging_enabled():
            msg = (
                "Cloud Logging is not enabled in app.json "
                "(loggingSettings.cloudLoggingSettings.enableCloudLogging=false)."
            )
            logger.warning(msg)
            return msg

        if not self.project_id:
            return "Cannot fetch logs: project ID is unknown."

        client = CloudLogsClient(
            project_id=self.project_id,
            filter_template=self.trace_config.cloud_logging.filter_template,
            time_padding_seconds=(
                self.trace_config.cloud_logging.time_padding_seconds
            ),
            credentials=self.creds,
        )
        normalized = normalized or self.get_normalized(conversation_id)
        start = _parse_iso(normalized.get("start_time"))
        end = _parse_iso(normalized.get("end_time"))
        return client.fetch(
            conversation_id=conversation_id,
            start_time=start,
            end_time=end,
            level=level,
        )

    # ------------------------------- audio ----------------------------------

    def resolve_audio_uri(
        self,
        conversation_id: str,
        normalized: dict[str, Any] | None = None,
    ) -> str | None:
        """Resolves the GCS URI for a conversation's audio recording.

        Order:
          1. Read directly from a chunk on the conversation proto if any
             carries an `audioUri` (per-turn audio playback URIs sometimes
             appear in `payload` chunks).
          2. `audio.bucket_override` (from `trace.yaml`) or
             `app.loggingSettings.audioRecordingConfig.gcsBucket`
             (from `app.json`) formatted via `audio.uri_pattern`.
          3. If `audio.search_bucket` is enabled and the simple URI does
             not exist, list the bucket and return the first object whose
             name ends with `/{conversation_id}/{search_filename}`.
        """
        normalized = normalized or self.get_normalized(conversation_id)
        for entry in normalized.get("entries", []):
            payload = entry.get("payload") if isinstance(entry, dict) else None
            if isinstance(payload, dict) and payload.get("audioUri"):
                return payload["audioUri"]

        bucket = self.trace_config.audio.bucket_override or (
            self.app_config.audio_bucket() if self.app_config else None
        )
        if not bucket:
            return None
        pattern = self.trace_config.audio.uri_pattern
        candidate = pattern.format(
            bucket=bucket.rstrip("/"),
            project=self.project_id or "",
            location=self.location or "",
            app=(self.app_name or "").split("/")[-1],
            conversation_id=conversation_id,
        )

        if self.trace_config.audio.search_bucket:
            gcs = GCSUtils(creds=self.creds)
            try:
                if gcs.exists(candidate):
                    return candidate
            except Exception:
                pass
            suffix = (
                f"/{conversation_id}/{self.trace_config.audio.search_filename}"
            )
            found = gcs.find_first(bucket, suffix=suffix)
            if found:
                logger.info(f"Resolved audio via bucket search: {found}")
                return found
            return None
        return candidate

    def download_audio(
        self,
        conversation_id: str,
        dest_dir: str | None = None,
        normalized: dict[str, Any] | None = None,
    ) -> str | None:
        """Downloads audio to disk and returns the local path."""
        gcs_uri = self.resolve_audio_uri(conversation_id, normalized=normalized)
        if not gcs_uri:
            logger.warning(
                "No audio URI could be resolved. Set "
                "`loggingSettings.audioRecordingConfig.gcsBucket` in app.json "
                "or `audio.bucket_override` in .cxas/trace.yaml."
            )
            return None

        gcs = GCSUtils(creds=self.creds)
        dest_dir = dest_dir or self.trace_config.audio.download_dir
        os.makedirs(dest_dir, exist_ok=True)
        ext = os.path.splitext(gcs_uri)[1] or ".wav"
        dest_path = os.path.join(dest_dir, f"{conversation_id}{ext}")
        return gcs.download_to_file(gcs_uri, dest_path)

    def list_audio_files(self, conversation_id: str) -> list[str]:
        """Returns the list of GCS URIs of every audio file recorded for a
        conversation.

        Conversations are stored as a directory of files
        (`METADATA.json`, `full-session.wav`, `agent-turn-N.wav`,
        `user-turn-N.wav`); this method discovers the directory by scanning
        the configured audio bucket for an object matching
        `*/{conversation_id}/METADATA.json`, then lists everything under that
        prefix.
        """
        bucket = self.trace_config.audio.bucket_override or (
            self.app_config.audio_bucket() if self.app_config else None
        )
        if not bucket:
            return []
        gcs = GCSUtils(creds=self.creds)
        prefix = gcs.find_dir_for_conversation(
            bucket, conversation_id=conversation_id
        )
        if not prefix:
            return []
        return gcs.list_with_prefix(bucket, prefix=prefix)

    # ----------------------------- analysis ---------------------------------

    def analyze_audio(
        self,
        conversation_id: str,
        metrics: list[str] | None = None,
        normalized: dict[str, Any] | None = None,
        audio_local_path: str | None = None,
    ) -> dict[str, Any]:
        """Runs configured audio analyses over the conversation recording.

        Uses the analysis registry in `utils.audio_analysis` (5 built-in
        analyses: voice consistency, no long pauses, agent having trouble,
        agent looping, agent cutoff). Each analysis declares which audio
        files it needs (e.g. `agent-turn-*.wav` vs `full-session.wav`).
        Prompt overrides may be supplied via `trace.yaml` under
        `gemini.audio_metrics.<analysis_name>.prompt`.

        Returns `{analysis_name: {result: PASS|FAIL|SKIP|ERROR, justification,
        files_analyzed}}`.
        """
        files = self.list_audio_files(conversation_id)
        if not files:
            return {"error": "No audio files found for this conversation."}

        if metrics:
            selected: list[AudioAnalysis] = [
                ANALYSIS_REGISTRY[m] for m in metrics if m in ANALYSIS_REGISTRY
            ]
            unknown = [m for m in metrics if m not in ANALYSIS_REGISTRY]
            if unknown:
                logger.warning(
                    f"Skipping unknown audio analyses: {unknown}. "
                    f"Available: {sorted(ANALYSIS_REGISTRY)}"
                )
        else:
            selected = list(ANALYSIS_REGISTRY.values())

        gem = GeminiGenerate(
            project_id=self.project_id,
            credentials=self.creds,
            model_name=self.trace_config.gemini.model,
        )
        overrides = self.trace_config.gemini.audio_metrics
        mime_type = self.trace_config.audio.mime_type

        results: dict[str, Any] = {}
        for analysis in selected:
            analysis_files = analysis.filter_files(files)
            if not analysis_files:
                results[str(analysis.name)] = {
                    "result": "SKIP",
                    "justification": (
                        "No audio files matched this analysis filter."
                    ),
                    "files_analyzed": [],
                }
                continue
            prompt = analysis.prompt
            override = overrides.get(str(analysis.name))
            if override is not None:
                prompt = override.prompt
            parts = [
                genai.types.Part.from_uri(file_uri=f, mime_type=mime_type)
                for f in analysis_files
            ]
            parts.append(prompt)
            response = gem.generate_with_parts(
                parts=parts,
                thinking_level=self.trace_config.gemini.thinking_level,
            )
            results[str(analysis.name)] = {
                **_parse_pass_fail(response),
                "files_analyzed": analysis_files,
            }
        return results

    def triage(
        self,
        conversation_id: str,
        metrics: list[str] | None = None,
        normalized: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Runs configured Gemini text-only triage over the transcript."""
        normalized = normalized or self.get_normalized(conversation_id)
        transcript = trace_report.to_text(normalized)

        gem = GeminiGenerate(
            project_id=self.project_id,
            credentials=self.creds,
            model_name=self.trace_config.gemini.model,
        )
        all_metrics = self.trace_config.gemini.triage_metrics
        if metrics:
            selected = {k: v for k, v in all_metrics.items() if k in metrics}
        else:
            selected = all_metrics

        results: dict[str, Any] = {}
        for metric_name, metric in selected.items():
            prompt = (
                f"{metric.prompt}\n\nConversation transcript:\n{transcript}\n"
            )
            results[metric_name] = gem.generate(
                prompt=prompt,
                thinking_level=self.trace_config.gemini.thinking_level,
            )
        return results

    # ------------------------------- replay ---------------------------------

    def replay(
        self,
        conversation_id: str,
        diff: bool = True,
    ) -> dict[str, Any]:
        """Re-runs original user inputs against the current agent.

        Returns `{"original": [...], "replay": [...], "diff": "..."}`. The
        diff is a unified diff of the agent text per turn.
        """
        normalized = self.get_normalized(conversation_id)
        user_turns = [
            e
            for e in normalized["entries"]
            if e["kind"] == "user" and e.get("text")
        ]

        sess = Sessions(app_name=self.app_name, creds=self.creds)
        session_id = sess.create_session_id()

        original_agents = _agent_text_per_turn(normalized)
        replay_agents: list[str] = []
        for ut in user_turns:
            try:
                response = sess.run(
                    session_id=session_id,
                    text=ut["text"],
                    modality=Modality.TEXT,
                )
                structured = sess.get_structured_response(response)
                replay_agents.append(structured.get("agent_text", ""))
            except Exception as e:
                replay_agents.append(f"<replay error: {e}>")

        result: dict[str, Any] = {
            "conversation_id": conversation_id,
            "original": original_agents,
            "replay": replay_agents,
        }
        if diff:
            result["diff"] = "\n".join(
                difflib.unified_diff(
                    original_agents,
                    replay_agents,
                    fromfile="original",
                    tofile="replay",
                    lineterm="",
                )
            )
        return result

    # ------------------------------ stats -----------------------------------

    def aggregate_stats(
        self,
        time_filter: str = "7d",
        source_filter: str | None = None,
        channel_filter: str | None = None,
        limit: int = 200,
        max_workers: int = 8,
    ) -> dict[str, Any]:
        """Aggregates counts/latencies/top-N across recent conversations."""
        rows = self.list(
            time_filter=time_filter,
            source_filter=source_filter,
            channel_filter=channel_filter,
            limit=limit,
        )
        normalized_list: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(self.get_normalized, r["id"]): r["id"] for r in rows
            }
            for fut in as_completed(futures):
                try:
                    normalized_list.append(fut.result())
                except Exception as e:
                    logger.warning(f"Failed to fetch {futures[fut]}: {e}")

        per_source: dict[str, int] = {}
        per_channel: dict[str, int] = {}
        per_tool: dict[str, int] = {}
        per_transfer: dict[str, int] = {}
        durations: list[float] = []
        success_total = 0

        for n in normalized_list:
            per_source[n.get("source") or "?"] = (
                per_source.get(n.get("source") or "?", 0) + 1
            )
            per_channel[n.get("channel") or "?"] = (
                per_channel.get(n.get("channel") or "?", 0) + 1
            )
            transferred = False
            for e in n.get("entries", []):
                if e["kind"] == "tool_call":
                    per_tool[e.get("tool") or "?"] = (
                        per_tool.get(e.get("tool") or "?", 0) + 1
                    )
                if e["kind"] == "agent_transfer":
                    transferred = True
                    per_transfer[str(e.get("target") or "?")] = (
                        per_transfer.get(str(e.get("target") or "?"), 0) + 1
                    )
            if not transferred:
                success_total += 1
            d = _duration_seconds(n.get("start_time"), n.get("end_time"))
            if d is not None:
                durations.append(d)

        return {
            "time_filter": time_filter,
            "total": len(normalized_list),
            "per_source": per_source,
            "per_channel": per_channel,
            "success_rate_no_transfer": (
                success_total / len(normalized_list)
                if normalized_list
                else None
            ),
            "duration_seconds": {
                "p50": _percentile(durations, 50),
                "p95": _percentile(durations, 95),
                "median": median(durations) if durations else None,
            },
            "top_tools": _top_n(per_tool, 10),
            "top_transfer_targets": _top_n(per_transfer, 10),
        }

    # ----------------------------- bundle/bug -------------------------------

    def bundle(
        self,
        conversation_id: str,
        out_path: str,
        with_logs: bool = True,
        with_audio: bool = True,
        with_analysis: bool = False,
        with_triage: bool = False,
    ) -> str:
        """Creates a zip with transcript, logs, audio, and report."""
        normalized = self.get_normalized(conversation_id)
        out_path = os.path.abspath(out_path)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

        audio_path: str | None = None
        if with_audio:
            try:
                audio_path = self.download_audio(
                    conversation_id, normalized=normalized
                )
            except Exception as e:
                logger.warning(f"Audio download failed: {e}")

        logs: list[dict[str, Any]] | str | None = None
        if with_logs:
            logs = self.get_logs(conversation_id, normalized=normalized)

        analysis = (
            self.analyze_audio(conversation_id, normalized=normalized)
            if with_analysis
            else None
        )
        triage_result = (
            self.triage(conversation_id, normalized=normalized)
            if with_triage
            else None
        )

        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "transcript.json",
                trace_report.to_json(normalized, include_raw=True),
            )
            z.writestr(
                "transcript.md",
                trace_report.to_markdown(
                    normalized, console_url=self.console_url(conversation_id)
                ),
            )
            z.writestr(
                "report.html",
                trace_report.to_html(
                    normalized,
                    console_url=self.console_url(conversation_id),
                    audio_path=(
                        os.path.basename(audio_path) if audio_path else None
                    ),
                ),
            )
            if logs is not None:
                z.writestr("logs.json", json.dumps(logs, indent=2, default=str))
            if analysis is not None:
                z.writestr(
                    "gemini_analysis.json",
                    json.dumps(analysis, indent=2, default=str),
                )
            if triage_result is not None:
                z.writestr(
                    "triage.json",
                    json.dumps(triage_result, indent=2, default=str),
                )
            if audio_path and os.path.exists(audio_path):
                z.write(audio_path, arcname=os.path.basename(audio_path))
            z.writestr(
                "metadata.json",
                json.dumps(self._metadata(), indent=2, default=str),
            )
        return out_path

    def report_bug(
        self,
        conversation_id: str,
        reason: str,
        severity: str = "medium",
    ) -> dict[str, Any]:
        """Bundles the conversation and uploads it to the bug-report bucket."""
        bug_cfg = self.trace_config.bug_report
        if not bug_cfg.bucket:
            raise ValueError(
                "trace.yaml: bug_report.bucket is not set; cannot upload."
            )

        # Build a temp bundle in memory and upload artifacts individually.
        normalized = self.get_normalized(conversation_id)
        path = bug_cfg.path_template.format(
            model_version=(
                self.app_config.model_version() if self.app_config else None
            )
            or "unknown_model",
            date=datetime.date.today().isoformat(),
            user=_gcloud_account() or os.environ.get("USER") or "unknown_user",
            severity=severity,
            conversation_id=conversation_id,
        )
        base_uri = f"{bug_cfg.bucket.rstrip('/')}/{path.lstrip('/')}"

        gcs = GCSUtils(creds=self.creds)

        uploaded: dict[str, str] = {}
        if "transcript" in bug_cfg.include:
            uploaded["transcript.json"] = gcs.upload_string(
                f"{base_uri}transcript.json",
                trace_report.to_json(normalized, include_raw=True),
                content_type="application/json",
            )
            uploaded["transcript.md"] = gcs.upload_string(
                f"{base_uri}transcript.md",
                trace_report.to_markdown(
                    normalized,
                    console_url=self.console_url(conversation_id),
                ),
                content_type="text/markdown",
            )
        if "logs" in bug_cfg.include:
            try:
                logs = self.get_logs(conversation_id, normalized=normalized)
                uploaded["logs.json"] = gcs.upload_string(
                    f"{base_uri}logs.json",
                    json.dumps(logs, indent=2, default=str),
                    content_type="application/json",
                )
            except Exception as e:
                logger.warning(f"Skipping logs in bug report: {e}")
        if "audio" in bug_cfg.include:
            try:
                local = self.download_audio(
                    conversation_id, normalized=normalized
                )
                if local and os.path.exists(local):
                    uploaded["audio"] = gcs.upload_file(
                        f"{base_uri}{os.path.basename(local)}",
                        local,
                        content_type="audio/wav",
                    )
            except Exception as e:
                logger.warning(f"Skipping audio in bug report: {e}")
        if "gemini_analysis" in bug_cfg.include:
            try:
                analysis = self.analyze_audio(
                    conversation_id, normalized=normalized
                )
                uploaded["gemini_analysis.json"] = gcs.upload_string(
                    f"{base_uri}gemini_analysis.json",
                    json.dumps(analysis, indent=2, default=str),
                    content_type="application/json",
                )
            except Exception as e:
                logger.warning(f"Skipping Gemini analysis in bug report: {e}")
        if "environment" in bug_cfg.include:
            uploaded["environment.json"] = gcs.upload_string(
                f"{base_uri}environment.json",
                json.dumps(self._metadata(), indent=2, default=str),
                content_type="application/json",
            )

        bug_meta = {
            "conversation_id": conversation_id,
            "reason": reason,
            "severity": severity,
            "model_version": (
                self.app_config.model_version() if self.app_config else None
            ),
            "reporter": _gcloud_account() or os.environ.get("USER"),
            "reported_at": datetime.datetime.utcnow().isoformat() + "Z",
            "app_name": self.app_name,
        }
        uploaded["bug_report.json"] = gcs.upload_string(
            f"{base_uri}bug_report.json",
            json.dumps(bug_meta, indent=2, default=str),
            content_type="application/json",
        )
        return {"gcs_uri": base_uri, "uploaded": uploaded, **bug_meta}

    # ---------------------------- ui / helpers ------------------------------

    def console_url(self, conversation_id: str) -> str:
        base = self.trace_config.ui.ces_console_base.rstrip("/")
        return (
            f"{base}/projects/{self.project_id}/locations/{self.location}"
            f"/apps/{(self.app_name or '').split('/')[-1]}/conversations/"
            f"{conversation_id}"
        )

    def _metadata(self) -> dict[str, Any]:
        return {
            "app_name": self.app_name,
            "project_id": self.project_id,
            "location": self.location,
            "model_version": (
                self.app_config.model_version() if self.app_config else None
            ),
            "display_name": (
                self.app_config.display_name() if self.app_config else None
            ),
            "git_sha": _git_sha(),
            "gcloud_account": _gcloud_account(),
            "captured_at": datetime.datetime.utcnow().isoformat() + "Z",
        }


# ----------------------------- module helpers -------------------------------


def _enum_name(v: Any) -> str | None:
    if v is None:
        return None
    return getattr(v, "name", str(v))


def _ts_iso(ts: Any) -> str | None:
    if ts is None:
        return None
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    return str(ts)


def _parse_iso(s: str | None) -> datetime.datetime | None:
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _duration_seconds(start: str | None, end: str | None) -> float | None:
    s = _parse_iso(start)
    e = _parse_iso(end)
    if s and e:
        return (e - s).total_seconds()
    return None


def _percentile(values: list[float], p: int) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def _top_n(counts: dict[str, int], n: int) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]


def _agent_text_per_turn(normalized: dict[str, Any]) -> list[str]:
    by_turn: dict[int, list[str]] = {}
    for e in normalized.get("entries", []):
        if e["kind"] == "agent" and e.get("text"):
            by_turn.setdefault(e.get("turn", 0), []).append(e["text"])
    return [" ".join(by_turn[k]) for k in sorted(by_turn.keys())]


def _git_sha() -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return None


def _parse_pass_fail(response: Any) -> dict[str, str]:
    """Best-effort parser for the `PASS / FAIL + justification` Gemini reply.

    Falls back to `{result: ERROR, ...}` if Gemini returned None, and to
    `{result: UNKNOWN, justification: <full text>}` if neither PASS nor FAIL
    appears in the response.
    """
    if response is None:
        return {
            "result": "ERROR",
            "justification": "Gemini returned no response.",
        }
    text = response if isinstance(response, str) else str(response)
    upper = text.upper()
    if "PASS" in upper.split():
        result = "PASS"
    elif "FAIL" in upper.split():
        result = "FAIL"
    elif text.lstrip().upper().startswith("PASS"):
        result = "PASS"
    elif text.lstrip().upper().startswith("FAIL"):
        result = "FAIL"
    else:
        return {"result": "UNKNOWN", "justification": text.strip()}
    return {"result": result, "justification": text.strip()}


def _gcloud_account() -> str | None:
    try:
        out = subprocess.check_output(
            ["gcloud", "config", "get-value", "account"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip() or None
    except Exception:
        return None
