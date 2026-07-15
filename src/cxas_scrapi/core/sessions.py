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

import json
import logging
import math
import mimetypes
import os
import queue
import re
import struct
import sys
import threading
import time
import uuid
import wave
from enum import Enum
from typing import Any

import certifi
import requests
import websocket
from google.auth.transport.requests import Request
from google.cloud.ces_v1beta import SessionServiceClient, types
from google.protobuf import json_format

from cxas_scrapi.utils.rate_limiter import RateLimiter

try:
    from IPython.display import HTML, display

    HAS_IPYTHON = True
except ImportError:
    HAS_IPYTHON = False

from cxas_scrapi.core.audio_transformer import (
    AUDIO_CHANNELS,
    AUDIO_SAMPLE_RATE_HZ,
    AUDIO_SAMPLE_WIDTH,
    AudioTransformer,
)

try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None
from cxas_scrapi.core.common import DEFAULT_API_ENDPOINT, Common
from cxas_scrapi.core.conversation_history import ConversationHistory
from cxas_scrapi.core.response_parser import ParsedSessionResponse

logger = logging.getLogger(__name__)


class ScrapiRunSessionResponse:
    """Wrapper around types.RunSessionResponse.

    Supports arbitrary attributes.
    """

    def __init__(
        self, original_response: Any, agent_audio_paths: dict[int, str]
    ):
        self._original_response = original_response
        self.agent_audio_paths = agent_audio_paths

    def __getattr__(self, name: str):
        return getattr(self._original_response, name)


class Modality(str, Enum):
    TEXT = "text"
    AUDIO = "audio"


BIDI_SESSION_URI = (
    f"wss://{DEFAULT_API_ENDPOINT}/ws/"
    "google.cloud.ces.v1.SessionService/BidiRunSession/locations/"
)
AUDIO_CHUNK_SIZE = 3200
CHUNK_DELAY = 0.1
SILENCE_PADDING_CHUNKS = 3
SAMPLE_RATE = AUDIO_SAMPLE_RATE_HZ
SAMPLE_WIDTH = AUDIO_SAMPLE_WIDTH


# Clean WebSocket close codes (RFC 6455)
_WS_CLEAN_CLOSE_CODES = frozenset(
    {
        websocket.STATUS_NORMAL,
        websocket.STATUS_GOING_AWAY,
        websocket.STATUS_STATUS_NOT_AVAILABLE,
    }
)

# Extract error kind from WS close reason payload
_BIDI_KIND_RE = re.compile(r"generic::(\w+)")

# Best-effort trace ID extraction from WS close reason
_BIDI_TRACE_RE = re.compile(
    r"(?:trace[_-]?id|trace)[\"'\s:=]+([a-zA-Z0-9_\-]+)", re.IGNORECASE
)


class BidiSessionError(Exception):
    """Raised when the bidi WebSocket terminates abnormally.

    Carries structured information about the close-frame or
    connection-level error so callers can branch on:
      - close_status_code: WS close code (e.g., 1007, 1011); None
        for connection-level errors that closed without a frame
      - server_error_kind: kind parsed from
        "[ORIGINAL ERROR] generic::<kind>:" -- e.g., "resource_exhausted"
      - server_trace_id: best-effort trace id extraction (often None)
      - close_msg: raw close-reason string for fallback inspection
    """

    def __init__(
        self,
        message: str,
        close_status_code: int | None = None,
        close_msg: str | None = None,
        server_error_kind: str | None = None,
        server_trace_id: str | None = None,
    ):
        super().__init__(message)
        self.close_status_code = close_status_code
        self.close_msg = close_msg
        self.server_error_kind = server_error_kind
        self.server_trace_id = server_trace_id


# Maximum timeout per WebSocket turn run
_BIDI_RUN_TIMEOUT_S = 120


class AgentTurnManager:
    """Manages the agent's turn by simulating audio playback time."""

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        sample_width: int = SAMPLE_WIDTH,
        skip_playback_wait: bool = False,
    ):
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.bytes_per_second = sample_rate * sample_width
        self.skip_playback_wait = skip_playback_wait

        self.len_audio_bytes_received = 0
        self.turn_completed_flag = False
        self.first_audio_received_time = None
        self.expected_duration_seconds = None
        self.is_welcome_turn = False
        self.current_turn_index = None
        self.lock = threading.Lock()

    def _calculate_rms(self, audio_bytes: bytes) -> float:
        count = len(audio_bytes) // 2
        if count == 0:
            return 0.0
        shorts = struct.unpack(f"<{count}h", audio_bytes[: count * 2])
        sum_squares = sum(s * s for s in shorts)
        return math.sqrt(sum_squares / count)

    def add_audio(self, audio_bytes: bytes):
        with self.lock:
            if self.turn_completed_flag:
                return
            rms = self._calculate_rms(audio_bytes)
            if rms < 200.0:
                return
            if self.first_audio_received_time is None:
                self.first_audio_received_time = time.time()
            self.len_audio_bytes_received += len(audio_bytes)

    def mark_turn_completed(self):
        with self.lock:
            self.turn_completed_flag = True

    def reset(self):
        with self.lock:
            self.len_audio_bytes_received = 0
            self.turn_completed_flag = False
            self.first_audio_received_time = None
            self.expected_duration_seconds = None
            self.is_welcome_turn = False
            self.current_turn_index = None

    def prepare_for_turn(self, expected_turn_index: int):
        with self.lock:
            logging.debug(
                "Preparing manager for turn index %d. Resetting state.",
                expected_turn_index,
            )
            self.len_audio_bytes_received = 0
            self.turn_completed_flag = False
            self.first_audio_received_time = None
            self.expected_duration_seconds = None
            self.is_welcome_turn = expected_turn_index <= 1
            self.current_turn_index = expected_turn_index

    def update_turn_index(self, turn_idx: int) -> bool:
        with self.lock:
            if self.current_turn_index is None:
                self.current_turn_index = turn_idx
                return True
            if turn_idx != self.current_turn_index:
                return False
            return True

    def is_agent_done_talking(self) -> bool:
        with self.lock:
            if self.skip_playback_wait:
                return True

            if self.first_audio_received_time is None:
                if self.turn_completed_flag:
                    return True  # Agent didn't send any audio
                return False

            # If the network transfer is not completed, we cannot use exact
            # byte-based timing, so we use the text-based estimate if available.
            if not self.turn_completed_flag:
                if self.expected_duration_seconds is not None:
                    current_playback_time = (
                        time.time() - self.first_audio_received_time
                    )
                    return (
                        current_playback_time >= self.expected_duration_seconds
                    )
                return False

            # Once the network transfer is completed, we have all audio bytes.
            # We calculate the exact duration of the received audio.
            audio_duration_seconds = (
                self.len_audio_bytes_received / self.bytes_per_second
            )
            # Add a small buffer padding to allow local playback buffer to
            # finish
            audio_duration_seconds += 0.5

            current_playback_time = time.time() - self.first_audio_received_time
            res = current_playback_time >= audio_duration_seconds
            logging.debug(
                "is_agent_done_talking (exact byte-based): "
                "duration=%.2f, elapsed=%.2f -> %s",
                audio_duration_seconds,
                current_playback_time,
                res,
            )
            return res


class BidiSessionHandler:
    """Handles the Bidi WebSocket session with the session service."""

    def __init__(
        self,
        location: str,
        token: str,
        config: dict[str, Any],
        inputs: list[dict[str, Any]] | None = None,
        input_queue: queue.Queue | None = None,
        response_queue: queue.Queue | None = None,
        user_agent: str | None = None,
        turn_num: int | None = None,
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        bg_noise_snr: float = 15.0,
        skip_playback_wait: bool = True,
    ):
        self.uri = BIDI_SESSION_URI + location
        self.token = token
        self.config = config
        self.inputs = inputs
        self.input_queue = input_queue
        self.response_queue = response_queue
        self.user_agent = user_agent
        self.turn_num = turn_num
        self.capture_agent_audio = capture_agent_audio
        self.agent_turn_manager = AgentTurnManager(
            skip_playback_wait=skip_playback_wait,
        )
        self.ws_app: websocket.WebSocketApp | None = None
        self.outputs = []
        self.audio_lock = threading.Lock()
        self.current_turn_outputs = []
        self.current_agent_turn_idx = 0
        self.turn_audio_buffers = {}
        self.max_server_turn_idx = 0
        self.turn_audio_paths = {}
        self.is_sending_audio = False
        self.config_sent = False
        self.lock = threading.Lock()

        self._close_status_code: int | None = None
        self._close_msg: str | None = None
        self._connection_error: BaseException | None = None

        # Setup continuous background noise segment if provided
        self.bg_noise_segment = None
        self.bg_noise_cursor = 0
        if background_noise_file and AudioSegment is None:
            raise ImportError(
                "background_noise_file was provided, but pydub is not "
                "installed or failed to import. Please install pydub "
                "(and audioop-lts on Python 3.13+) to enable background noise."
            )
        if AudioSegment and background_noise_file:
            try:
                # Load and format to 16000Hz, 1ch, 16-bit (sample width 2) PCM
                segment = AudioSegment.from_file(background_noise_file)
                segment = (
                    segment.set_frame_rate(SAMPLE_RATE)
                    .set_channels(AUDIO_CHANNELS)
                    .set_sample_width(SAMPLE_WIDTH)
                )

                # Pre-scale the noise to match the target SNR relative to a
                # standard -20.0 dBFS speech level
                if segment.dBFS != float("-inf"):
                    target_noise_dbfs = -20.0 - bg_noise_snr
                    volume_change = target_noise_dbfs - segment.dBFS
                    self.bg_noise_segment = segment + volume_change
                    logging.debug(
                        "Pre-scaled continuous background noise: "
                        "target = %.2f dBFS, delta = %.2f dB",
                        target_noise_dbfs,
                        volume_change,
                    )
                else:
                    self.bg_noise_segment = segment - 15.0

            except Exception as ex:
                logging.warning(
                    f"Failed to load continuous background noise file: {ex}"
                )

    def _get_next_noise_chunk(self, duration_ms: int) -> bytes:
        """Extracts the next chunk of continuous background noise PCM bytes."""
        chunk_bytes_len = int(SAMPLE_RATE * SAMPLE_WIDTH * (duration_ms / 1000))

        if self.bg_noise_segment is None:
            return b"\x00" * chunk_bytes_len

        chunk_segment = self.bg_noise_segment[
            self.bg_noise_cursor : self.bg_noise_cursor + duration_ms
        ]
        self.bg_noise_cursor += duration_ms

        if self.bg_noise_cursor >= len(self.bg_noise_segment):
            self.bg_noise_cursor = 0

        if len(chunk_segment) < duration_ms:
            padding_len = duration_ms - len(chunk_segment)
            chunk_segment = chunk_segment + self.bg_noise_segment[:padding_len]

        return chunk_segment.raw_data[:chunk_bytes_len]

    def _send_silence(self, num_chunks: int):
        chunk_duration_ms = 100
        for _ in range(num_chunks):
            noise_chunk = self._get_next_noise_chunk(chunk_duration_ms)
            query_message = types.BidiSessionClientMessage(
                realtime_input=types.SessionInput(audio=noise_chunk)
            )
            query_json = json_format.MessageToJson(
                query_message._pb,
                preserving_proto_field_name=False,
                indent=None,
            )
            self.ws_app.send(query_json)
            time.sleep(CHUNK_DELAY)

    def _send_audio_message(
        self, audio_payload: dict[str, Any], turn_index: int
    ):
        with self.lock:
            self.is_sending_audio = True
        try:
            expected_idx = self.max_server_turn_idx + 1
            self.agent_turn_manager.prepare_for_turn(
                expected_turn_index=expected_idx
            )
            audio_bytes = audio_payload["audio"]
            variables = audio_payload.get("variables")

            if variables:
                logging.debug(
                    "Sending variables before audio chunks: %s", variables
                )
                var_message = types.BidiSessionClientMessage(
                    realtime_input=types.SessionInput(variables=variables)
                )
                var_json = json_format.MessageToJson(
                    var_message._pb,
                    preserving_proto_field_name=False,
                    indent=None,
                )
                self.ws_app.send(var_json)
                time.sleep(0.5)

            logging.debug(
                "Sending leading silence before turn %d...", turn_index
            )
            self._send_silence(
                SILENCE_PADDING_CHUNKS
            )  # 0.3 seconds of leading silence

            logging.debug("Sending audio chunks for turn %d...", turn_index)

            for i in range(0, len(audio_bytes), AUDIO_CHUNK_SIZE):
                chunk = audio_bytes[i : i + AUDIO_CHUNK_SIZE]

                # Dynamically mix continuous background noise chunk-by-chunk
                # in real-time
                if self.bg_noise_segment is not None:
                    try:
                        speech_seg = AudioSegment(
                            chunk,
                            frame_rate=SAMPLE_RATE,
                            sample_width=SAMPLE_WIDTH,
                            channels=AUDIO_CHANNELS,
                        )
                        noise_seg = self.bg_noise_segment[
                            self.bg_noise_cursor : self.bg_noise_cursor + 100
                        ]
                        self.bg_noise_cursor += 100
                        if self.bg_noise_cursor >= len(self.bg_noise_segment):
                            self.bg_noise_cursor = 0

                        mixed_seg = speech_seg.overlay(noise_seg)
                        chunk = mixed_seg.raw_data[: len(chunk)]
                    except Exception as ex:
                        logging.warning(
                            "Failed to overlay continuous noise chunk in "
                            f"real-time: {ex}"
                        )

                query_message = types.BidiSessionClientMessage(
                    realtime_input=types.SessionInput(audio=chunk)
                )
                query_json = json_format.MessageToJson(
                    query_message._pb,
                    preserving_proto_field_name=False,
                    indent=None,
                )
                self.ws_app.send(query_json)
                time.sleep(CHUNK_DELAY)

            logging.debug(
                "Sending trailing silence for turn %d to "
                "trigger endpointing...",
                turn_index,
            )
            self._send_silence(
                SILENCE_PADDING_CHUNKS
            )  # 0.3 seconds of trailing silence

            logging.debug("Waiting for agent to finish turn %d...", turn_index)
            while not self.agent_turn_manager.is_agent_done_talking():
                self._send_silence(1)

            if self.input_queue is None:
                self._save_and_increment_agent_audio()
                self.agent_turn_manager.reset()
            time.sleep(1)  # Small pause between turns
        finally:
            with self.lock:
                self.is_sending_audio = False

    def _send_inputs(self):
        try:
            logging.debug("Config dict: %s", self.config)
            config_message = types.BidiSessionClientMessage(
                config=types.SessionConfig(
                    session=self.config["session"],
                    input_audio_config=self.config.get("input_audio_config"),
                    output_audio_config=self.config.get("output_audio_config"),
                    use_tool_fakes=self.config.get("use_tool_fakes", False),
                    historical_contexts=self.config.get("historical_contexts"),
                )
            )
            config_json = json_format.MessageToJson(
                config_message._pb,
                preserving_proto_field_name=False,
                indent=None,
            )
            logging.debug("Sending config: %s", config_json)
            self.ws_app.send(config_json)
            with self.lock:
                self.config_sent = True

            if self.inputs is not None:
                for idx, input_item in enumerate(self.inputs):
                    self._send_single_input(input_item, idx)
            elif self.input_queue is not None:
                idx = 0
                while True:
                    input_item = self.input_queue.get()
                    if input_item is None:
                        logging.debug("Received exit sentinel in input queue.")
                        break
                    self._send_single_input(input_item, idx)
                    idx += 1
            else:
                logging.debug("No inputs or input_queue provided.")
                self.ws_app.close()
                return


            logging.debug("All inputs sent and turns completed.")
            time.sleep(1)  # arbitrary short wait before disconnecting
            self.ws_app.close()

        except Exception as e:
            logging.debug("Error during send_inputs: %s", e)
            if self.ws_app:
                self.ws_app.close()

    def _save_and_increment_agent_audio(self):
        with self.audio_lock:
            turn_index = self.current_agent_turn_idx
            buffer = self.turn_audio_buffers.get(turn_index, b"")
            buffer_copy = bytes(buffer)
            self.current_agent_turn_idx += 1

        if buffer_copy and self.capture_agent_audio:
            session_name = self.config.get("session", "")
            session_id = (
                session_name.split("/sessions/")[-1]
                if "/sessions/" in session_name
                else str(uuid.uuid4())
            )
            current_turn = (self.turn_num or 0) + turn_index
            os.makedirs(f"/tmp/scrapi_evals/{session_id}", exist_ok=True)
            wav_filename = (
                f"/tmp/scrapi_evals/{session_id}/turn_{current_turn}_agent.wav"
            )
            try:
                with wave.open(wav_filename, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(16000)
                    wav_file.writeframes(buffer_copy)
                self.turn_audio_paths[turn_index] = wav_filename
                logging.info(f"Wrote agent turn audio to {wav_filename}")
            except Exception as audio_err:
                logging.error(
                    f"Failed to write WAV file {wav_filename}: {audio_err}"
                )

    def _send_single_input(self, input_item: dict[str, Any], idx: int):
        if "audio" in input_item:
            self._send_audio_message(input_item["audio"], idx)
            return

        # Handle non-audio structured inputs (event, text, variables)
        try:
            session_input_pb = types.SessionInput()._pb
            json_format.ParseDict(
                input_item,
                session_input_pb,
                ignore_unknown_fields=False,
            )
            session_input = types.SessionInput(session_input_pb)

            query_message = types.BidiSessionClientMessage(
                realtime_input=session_input
            )
            query_json = json_format.MessageToJson(
                query_message._pb,
                preserving_proto_field_name=False,
                indent=None,
            )
            logging.debug("Sending non-audio input: %s", query_json)
            self.ws_app.send(query_json)

            if "text" in input_item or "event" in input_item:
                expected_idx = self.max_server_turn_idx + 1
                self.agent_turn_manager.prepare_for_turn(
                    expected_turn_index=expected_idx
                )
                logging.debug(
                    "Waiting for agent to finish processing turn %d...",
                    idx,
                )
                while not self.agent_turn_manager.is_agent_done_talking():
                    time.sleep(1)

                if self.input_queue is None:
                    self._save_and_increment_agent_audio()
                    self.agent_turn_manager.reset()

                time.sleep(1)
            elif "variables" in input_item:
                logging.debug(
                    "Sent variables, pausing to allow state update..."
                )
                time.sleep(0.5)

        except Exception as e:
            logging.debug("Failed to send generic input %s: %s", input_item, e)

    def _on_open(self, ws):
        logging.debug("WebSocket connection opened")
        threading.Thread(target=self._send_inputs, daemon=True).start()
        threading.Thread(target=self._silence_loop, daemon=True).start()

    def _silence_loop(self):
        while True:
            if (
                not self.ws_app
                or not self.ws_app.sock
                or not self.ws_app.sock.connected
            ):
                break

            with self.lock:
                should_send = self.config_sent and not self.is_sending_audio

            if should_send:
                try:
                    self._send_silence(1)
                except Exception as ex:
                    logging.debug("Silence loop error: %s", ex)
                    break
            else:
                time.sleep(0.1)

    def _on_message(self, ws, message):
        logging.debug("===============")
        logging.debug("Received message: %s...", message[:100])
        try:
            response_pb = types.BidiSessionServerMessage()._pb
            message_dict = json.loads(message)
            output = message_dict.get("sessionOutput") or message_dict.get(
                "session_output"
            )
            if isinstance(output, dict):
                diag = output.get("diagnosticInfo") or output.get(
                    "diagnostic_info"
                )
                if isinstance(diag, dict):
                    logging.debug("DIAG DUMP: %s", json.dumps(diag))
                    root_span = diag.get("rootSpan") or diag.get("root_span")
                    if isinstance(root_span, dict):
                        root_span.pop("execution_steps", None)
                        root_span.pop("executionSteps", None)

            json_format.ParseDict(
                message_dict,
                response_pb,
                ignore_unknown_fields=True,
            )
            response = types.BidiSessionServerMessage(response_pb)

            if response.session_output:
                # Check if this is an empty comfort noise or variables ack
                # packet
                has_text = bool(response.session_output.text)
                has_diag_messages = False
                diag = response.session_output.diagnostic_info
                if diag and diag.messages:
                    has_diag_messages = True

                has_execution = False
                if diag and diag.root_span and diag.root_span.child_spans:
                    has_execution = True

                has_audio_energy = False
                if response.session_output.audio:
                    rms = self.agent_turn_manager._calculate_rms(
                        response.session_output.audio
                    )
                    if rms >= 200.0:
                        has_audio_energy = True

                is_empty_response = (
                    not response.session_output.turn_completed
                    and not has_text
                    and not has_diag_messages
                    and not has_execution
                    and not has_audio_energy
                )

                if is_empty_response:
                    logging.debug(
                        "Ignoring empty/silent server response packet."
                    )
                    return

                turn_idx = response.session_output.turn_index
                self.max_server_turn_idx = max(
                    self.max_server_turn_idx, turn_idx
                )
                if not self.agent_turn_manager.update_turn_index(turn_idx):
                    return

                self.outputs.append(response.session_output)
                self.current_turn_outputs.append(response.session_output)
                self.agent_turn_manager.is_welcome_turn = (
                    response.session_output.turn_index <= 1
                )

                if response.session_output.audio:
                    self.agent_turn_manager.add_audio(
                        response.session_output.audio
                    )
                    with self.audio_lock:
                        idx = self.current_agent_turn_idx
                        if idx not in self.turn_audio_buffers:
                            self.turn_audio_buffers[idx] = bytearray()
                        self.turn_audio_buffers[idx].extend(
                            response.session_output.audio
                        )

                # Dynamically update estimated speech duration based on text
                # received so far
                parsed = ParsedSessionResponse(self.current_turn_outputs)
                agent_text = parsed.consolidated_agent_text
                if agent_text:
                    word_count = len(agent_text.split())
                    # Heuristic: 140 WPM (2.33 words/sec) + 1.5s padding
                    estimated_duration = (word_count / 2.33) + 1.5
                    logging.debug(
                        "Calculated text speech duration: %d words -> "
                        "%.2f seconds",
                        word_count,
                        estimated_duration,
                    )
                    self.agent_turn_manager.expected_duration_seconds = (
                        estimated_duration
                    )

                if response.session_output.turn_completed:
                    logging.debug(
                        "Agent turn network payload completed. "
                        "Waiting for audio playback."
                    )
                    self.agent_turn_manager.mark_turn_completed()

                    if self.response_queue is not None:
                        # Get the active buffer
                        buffer = self.turn_audio_buffers.get(
                            self.current_agent_turn_idx, b""
                        )
                        if buffer and self.capture_agent_audio:
                            session_name = self.config.get("session", "")
                            session_id = (
                                session_name.split("/sessions/")[-1]
                                if "/sessions/" in session_name
                                else str(uuid.uuid4())
                            )
                            current_turn = (
                                self.turn_num or 0
                            ) + self.current_agent_turn_idx
                            os.makedirs(
                                f"/tmp/scrapi_evals/{session_id}", exist_ok=True
                            )
                            wav_filename = (
                                f"/tmp/scrapi_evals/{session_id}/"
                                f"turn_{current_turn}_agent.wav"
                            )

                            try:
                                with wave.open(wav_filename, "wb") as wav_file:
                                    wav_file.setnchannels(1)
                                    wav_file.setsampwidth(2)
                                    wav_file.setframerate(16000)
                                    wav_file.writeframes(buffer)
                                self.turn_audio_paths[
                                    self.current_agent_turn_idx
                                ] = wav_filename
                                logging.info(
                                    f"Wrote agent turn audio to {wav_filename}"
                                )
                            except Exception as audio_err:
                                logging.error(
                                    f"Failed to write WAV file {wav_filename}:"
                                    f" {audio_err}"
                                )

                        original_response = types.RunSessionResponse(
                            outputs=list(self.current_turn_outputs)
                        )
                        turn_audio_path = {}
                        idx = self.current_agent_turn_idx
                        if idx in self.turn_audio_paths:
                            turn_audio_path[0] = self.turn_audio_paths[idx]

                        turn_response = ScrapiRunSessionResponse(
                            original_response=original_response,
                            agent_audio_paths=turn_audio_path,
                        )
                        self.response_queue.put(turn_response)
                        self.current_turn_outputs = []
                        self.current_agent_turn_idx += 1

            if response.end_session:
                if self.response_queue is not None:
                    self.response_queue.put(
                        {
                            "session_ended": True,
                            "end_session": response.end_session,
                        }
                    )
        except Exception as e:
            logging.debug("Failed to parse message: %s", e)

    def _on_error(self, ws, error):
        logging.debug("WebSocket error: %s", error)
        # Stash connection-level errors
        self._connection_error = error
        if self.response_queue is not None:
            self.response_queue.put(
                {
                    "session_ended": True,
                    "connection_error": error,
                }
            )

    def _on_close(self, ws, close_status_code, close_msg):
        logging.debug(
            "WebSocket connection closed with code %s and reason: %s",
            close_status_code,
            close_msg,
        )
        # Stash close status to verify clean closure
        self._close_status_code = close_status_code
        self._close_msg = close_msg
        if self.response_queue is not None:
            self.response_queue.put(
                {
                    "session_ended": True,
                    "connection_closed": True,
                    "close_status_code": close_status_code,
                    "close_msg": close_msg,
                }
            )

    def run(self):
        logging.debug("Connecting to WebSocket: %s", self.uri)
        self.ws_app = websocket.WebSocketApp(
            self.uri,
            header={
                "Authorization": f"Bearer {self.token}",
                "User-Agent": self.user_agent,
            },
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        wst = threading.Thread(
            target=self.ws_app.run_forever,
            kwargs={"sslopt": {"ca_certs": certifi.where()}},
        )
        wst.daemon = True
        wst.start()

        if self.input_queue is not None:
            logging.debug("BidiSessionHandler started in non-blocking mode.")
            return wst

        logging.debug("Waiting for session to complete...")
        wst.join(timeout=_BIDI_RUN_TIMEOUT_S)
        if wst.is_alive():
            # Force close connection on run timeout
            logging.warning(
                "Bidi session exceeded %ss without completing; forcing close.",
                _BIDI_RUN_TIMEOUT_S,
            )
            if self._connection_error is None:
                self._connection_error = TimeoutError(
                    f"bidi run timed out after {_BIDI_RUN_TIMEOUT_S}s "
                    "(no end-of-turn or close)"
                )
            try:
                self.ws_app.close()
            except Exception:
                pass
            wst.join(timeout=5)

        # Check for abnormal WebSocket closures
        if (
            self._close_status_code is not None
            and self._close_status_code not in _WS_CLEAN_CLOSE_CODES
        ):
            raw = self._close_msg or ""
            kind_match = _BIDI_KIND_RE.search(raw)
            trace_match = _BIDI_TRACE_RE.search(raw)
            kind = kind_match.group(1) if kind_match else None
            trace_id = trace_match.group(1) if trace_match else None
            summary = (
                f"CXAS bidi session closed by server "
                f"(code={self._close_status_code}"
                + (f", kind={kind}" if kind else "")
                + (f", trace_id={trace_id}" if trace_id else "")
                + ")"
            )
            raise BidiSessionError(
                summary,
                close_status_code=self._close_status_code,
                close_msg=raw,
                server_error_kind=kind,
                server_trace_id=trace_id,
            )

        # Handle connection errors without close frames
        if self._connection_error is not None and not self.outputs:
            raise BidiSessionError(
                f"CXAS bidi connection error: {self._connection_error}",
                close_status_code=None,
                close_msg=str(self._connection_error),
            )

        original_response = types.RunSessionResponse(outputs=self.outputs)
        return ScrapiRunSessionResponse(
            original_response=original_response,
            agent_audio_paths=self.turn_audio_paths,
        )


class BidiInteractiveSession:
    """Manages an active Bidi WebSocket session with GECX.

    Supports dynamic multi-turn interactions.
    """

    def __init__(
        self,
        sessions_client: "Sessions",
        session_id: str,
        config: dict[str, Any],
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        bg_noise_snr: float = 15.0,
        skip_playback_wait: bool = True,
        voice_config: dict[str, Any] | None = None,
    ):
        self.sessions_client = sessions_client
        self.session_id = session_id
        self.config = config
        self.capture_agent_audio = capture_agent_audio
        self.background_noise_file = background_noise_file
        self.bg_noise_snr = bg_noise_snr
        self.voice_config = voice_config

        self.input_queue = queue.Queue()
        self.response_queue = queue.Queue()

        self.handler = BidiSessionHandler(
            location=sessions_client.location,
            token=sessions_client.token,
            config=config,
            inputs=None,
            input_queue=self.input_queue,
            response_queue=self.response_queue,
            capture_agent_audio=capture_agent_audio,
            background_noise_file=background_noise_file,
            bg_noise_snr=bg_noise_snr,
            skip_playback_wait=skip_playback_wait,
        )
        self.wst = None

    def start(self):
        """Connects to the WebSocket gateway in the background."""
        self.wst = self.handler.run()
        # Wait a short moment to ensure thread has started and
        # websocket is opened.
        time.sleep(0.5)

    def send_turn(
        self, text: str, variables: dict[str, Any] | None = None
    ) -> Any:
        """Sends a user query and returns the agent's turn response."""
        if self.sessions_client.rate_limiter:
            self.sessions_client.rate_limiter.wait_and_consume()
        # Convert text to TTS audio bytes
        audio_transformer = AudioTransformer()
        lang_code = "en-US"
        if variables and "locale" in variables:
            lang_code = variables["locale"]

        if text.startswith("event:"):
            event_name = text[len("event:") :].strip()
            if variables:
                self.input_queue.put({"variables": variables})
            self.input_queue.put({"event": {"event": event_name}})
        else:
            current_voice_config = (self.voice_config or {}).copy()
            if "language_code" not in current_voice_config:
                current_voice_config["language_code"] = lang_code

            input_data = audio_transformer.text_to_speech_bytes(
                text=text,
                credentials=self.sessions_client.creds,
                project_id=self.sessions_client.project_id,
                background_noise_file=self.background_noise_file,
                voice_config=current_voice_config,
            )

            audio_payload = {
                "audio": input_data["audio_bytes"],
                "text": input_data["text"],
            }
            if variables:
                audio_payload["variables"] = variables

            # Put in queue
            self.input_queue.put({"audio": audio_payload})

        # Block wait for the response with timeout
        try:
            response = self.response_queue.get(timeout=90)
            return response
        except queue.Empty as err:
            logging.error(
                "Timeout waiting for agent turn response in "
                "BidiInteractiveSession"
            )
            raise TimeoutError(
                "Timeout waiting for agent response via WebSocket"
            ) from err

    def close(self):
        """Closes the WebSocket connection cleanly."""
        self.input_queue.put(None)  # Sentinel to close sending loop
        if self.wst is not None:
            self.wst.join(timeout=10)


class Sessions(Common):
    def __init__(
        self,
        app_name: str,
        deployment_id: str | None = None,
        rate_limiter: RateLimiter | None = None,
        **kwargs,
    ):
        """Initializes the Sessions client."""
        super().__init__(app_name=app_name, **kwargs)

        # Initialize Sessions Client
        self.client = SessionServiceClient(
            transport=self.get_grpc_transport(SessionServiceClient),
            client_info=self.client_info,
        )

        self.app_name = app_name
        self.deployment_id = deployment_id
        self.rate_limiter = rate_limiter
        self._creds_lock = threading.Lock()

    def _check_audio_requirements(self):
        """Checks if the necessary APIs are enabled and user has permissions."""
        if not self.project_id:
            raise ValueError(
                "Project ID could not be determined from the app_name. "
                "Audio preflight checks cannot be performed without a "
                "project ID."
            )

        services = ["ces.googleapis.com", "texttospeech.googleapis.com"]

        with self._creds_lock:
            try:
                self.creds.refresh(Request())
            except Exception as e:
                logger.debug(f"Failed to refresh credentials: {e}")

        headers = {"Authorization": f"Bearer {self.creds.token}"}

        http_forbidden = 403
        http_ok = 200

        for service in services:
            url = (
                f"https://serviceusage.googleapis.com/v1/projects/"
                f"{self.project_id}/services/{service}"
            )
            try:
                response = requests.get(url, headers=headers)
                if response.status_code == http_forbidden:
                    raise PermissionError(
                        f"Permission denied when checking service {service}. "
                        "Make sure you have permissions like "
                        "roles/serviceusage.serviceUsageConsumer."
                    )
                elif response.status_code != http_ok:
                    raise RuntimeError(
                        f"Failed to check service {service}. "
                        f"Status code: {response.status_code}"
                    )

                data = response.json()
                if data.get("state") != "ENABLED":
                    raise RuntimeError(
                        f"Service {service} is not enabled in project "
                        f"{self.project_id}. "
                        "Please enable it in the Google Cloud Console."
                    )
            except requests.RequestException as e:
                raise RuntimeError(
                    f"Network error when checking service {service}: {e}"
                ) from e

    def create_session_id(self) -> str:
        """Create a unique uuid4 string to use as the session ID."""
        return str(uuid.uuid4())

    @staticmethod
    def get_file_data(file_path: str) -> dict[str, Any]:
        """
        Reads a local file, returns a blob dict.
        """
        if not os.path.exists(file_path):
            logger.error(f"File not found at path: {file_path}")
            raise FileNotFoundError(
                f"The file specified at {file_path} was not found."
            )

        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type is None:
            mime_type = "application/octet-stream"

        with open(file_path, "rb") as f:
            raw_bytes = f.read()

        return {"mime_type": mime_type, "data": raw_bytes}

    @staticmethod
    def _expand_pb_struct(pb_struct):
        try:
            return json.loads(json_format.MessageToJson(pb_struct))
        except Exception:
            pass

        if hasattr(pb_struct, "items"):
            res = {}
            for k, v in pb_struct.items():
                res[k] = Sessions._expand_pb_struct(v)
            return res
        elif hasattr(pb_struct, "__iter__") and not isinstance(pb_struct, str):
            return [Sessions._expand_pb_struct(item) for item in pb_struct]
        else:
            return pb_struct

    def parse_result(self, res: Any):
        """
        Parses the CX Agent Studio session response to extract and print
        turn-by-turn interactions including User Queries, Agent Responses,
        Tool Calls, Tool Results, and Agent Transfers.
        Requires Jupyter Notebook or IPython environment for HTML rendering.
        """

        is_notebook = "ipykernel" in sys.modules

        if not is_notebook:
            # ANSI escape codes for terminal
            tool_call_font = "\033[1;31mTOOL CALL:\033[0m"
            tool_res_font = "\033[1;33mTOOL RESULT:\033[0m"
            query_font = "\033[1;32mUSER QUERY:\033[0m"
            response_font = "\033[1;35mAGENT RESPONSE:\033[0m"
            transfer_font = "\033[1;36mAGENT TRANSFER:\033[0m"
            payload_font = "\033[1;94mCUSTOM PAYLOAD:\033[0m"

            render = print

            def render_html(text):
                return text  # Pass-through for terminal

        elif HAS_IPYTHON:
            tool_call_font = "<font color='darkred'><b>TOOL CALL:</b></font>"
            tool_res_font = "<font color='goldenrod'><b>TOOL RESULT:</b></font>"
            query_font = "<font color='darkgreen'><b>USER QUERY:</b></font>"
            response_font = "<font color='purple'><b>AGENT RESPONSE:</b></font>"
            transfer_font = (
                "<font color='darkorange'><b>AGENT TRANSFER:</b></font>"
            )
            payload_font = "<font color='brown'><b>CUSTOM PAYLOAD:</b></font>"

            render = display
            render_html = HTML
        else:
            tool_call_font = "TOOL CALL:"
            tool_res_font = "TOOL RESULT:"
            query_font = "USER QUERY:"
            response_font = "AGENT RESPONSE:"
            transfer_font = "AGENT TRANSFER:"
            payload_font = "CUSTOM PAYLOAD:"

            render = print

            def render_html(text):
                return re.sub(r"<[^>]*>", "", text).strip()

        outputs = getattr(res, "outputs", [])
        if not outputs:
            return

        for output in outputs:
            diagnostic_info = getattr(output, "diagnostic_info", None)

            # If diagnostic_info is available, use it for a rich
            # turn-by-turn trace
            if diagnostic_info and hasattr(diagnostic_info, "messages"):
                messages = getattr(diagnostic_info, "messages", [])
                for message in messages:
                    role = getattr(message, "role", "")
                    chunks = getattr(message, "chunks", [])

                    for chunk in chunks:
                        # Depending on the generated class, WhichOneof is
                        # available on the internal _pb message
                        chunk_type = (
                            chunk._pb.WhichOneof("data")
                            if hasattr(chunk, "_pb")
                            else None
                        )

                        if chunk_type == "text":
                            if role.lower() == "user":
                                logging.debug(f"USER QUERY: {chunk.text}")
                                render(
                                    render_html(f"{query_font} {chunk.text}")
                                )
                            else:
                                logging.debug(
                                    f"AGENT RESPONSE: [{role}] {chunk.text}"
                                )
                                render(
                                    render_html(
                                        f"{response_font} [{role}] {chunk.text}"
                                    )
                                )

                        elif chunk_type == "transcript":
                            if role.lower() == "user":
                                logging.debug(f"USER QUERY: {chunk.transcript}")
                                render(
                                    render_html(
                                        f"{query_font} {chunk.transcript}"
                                    )
                                )
                            else:
                                logging.debug(
                                    f"AGENT RESPONSE: [{role}] "
                                    f"{chunk.transcript}"
                                )
                                render(
                                    render_html(
                                        f"{response_font} [{role}] "
                                        f"{chunk.transcript}"
                                    )
                                )

                        elif chunk_type == "tool_call":
                            tc = chunk.tool_call
                            tool_name = tc.display_name or tc.tool
                            expanded_args = Sessions._expand_pb_struct(tc.args)
                            logging.debug(
                                f"TOOL CALL: [{role}] {tool_name} -- "
                                f"Args: {expanded_args}"
                            )
                            render(
                                render_html(
                                    f"{tool_call_font} [{role}] {tool_name} -- "
                                    f"Args: {expanded_args}"
                                )
                            )

                        elif chunk_type == "tool_response":
                            tr = chunk.tool_response
                            tool_name = tr.display_name or tr.tool
                            expanded_response = Sessions._expand_pb_struct(
                                tr.response
                            )
                            logging.debug(
                                f"TOOL RESULT: [{role}] {tool_name} -- "
                                f"Result: {expanded_response}"
                            )
                            render(
                                render_html(
                                    f"{tool_res_font} [{role}] {tool_name} -- "
                                    f"Result: {expanded_response}"
                                )
                            )

                        elif chunk_type == "agent_transfer":
                            at = chunk.agent_transfer
                            logging.debug(
                                f"AGENT TRANSFER: [{role}] "
                                f"Transferred to {at.display_name}"
                            )
                            render(
                                render_html(
                                    f"{transfer_font} [{role}] "
                                    f"Transferred to {at.display_name}"
                                )
                            )

                        elif chunk_type == "payload":
                            expanded_payload = Sessions._expand_pb_struct(
                                chunk.payload
                            )
                            logging.debug(
                                f"CUSTOM PAYLOAD: [{role}] {expanded_payload}"
                            )
                            render(
                                render_html(
                                    f"{payload_font} [{role}] "
                                    f"{expanded_payload}"
                                )
                            )

    def get_structured_response(self, response) -> dict[str, Any]:
        """Parse response, avoiding duplicate text from diagnostic info.

        Returns a dictionary with keys:
        - agent_text: Consolidated text response.
        - tool_calls: List of tool calls made by agent.
        - tool_responses: List of tool responses received.
        - agent_transfer: Target agent if a transfer occurred.
        - session_ended: Boolean indicating if session ended.
        - citations: List of citations used for response generation.
        - payload: Custom payload dict if present.
        """
        parsed = ParsedSessionResponse(response)
        return {
            "agent_text": parsed.consolidated_agent_text,
            "tool_calls": [
                {"action": tc.name, "args": tc.args} for tc in parsed.tool_calls
            ],
            "tool_responses": [
                {
                    "action": f"_response:{tr.name}",
                    "args": {},
                    "response": tr.response,
                }
                for tr in parsed.tool_responses
            ],
            "agent_transfer": parsed.agent_transfer,
            "session_ended": parsed.session_ended,
            "citations": parsed.citations,
            "payload": (
                parsed.custom_payloads[0] if parsed.custom_payloads else None
            ),
        }

    def async_bidi_run_session(
        self,
        config: dict,
        inputs: list[dict[str, Any]],
        turn_num: int | None = None,
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        bg_noise_snr: float = 15.0,
    ):
        if self.rate_limiter:
            self.rate_limiter.wait_and_consume()
        with self._creds_lock:
            try:
                if hasattr(self.creds, "refresh"):
                    self.creds.refresh(Request())
            except Exception as e:
                logger.debug(
                    f"Failed to refresh credentials before Bidi session: {e}"
                )

        handler = BidiSessionHandler(
            self.location,
            self.token,
            config,
            inputs,
            user_agent=self.user_agent,
            turn_num=turn_num,
            capture_agent_audio=capture_agent_audio,
            background_noise_file=background_noise_file,
            bg_noise_snr=bg_noise_snr,
        )
        return handler.run()

    def create_interactive_session(
        self,
        session_id: str,
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        bg_noise_snr: float = 15.0,
        use_tool_fakes: bool = True,
        skip_playback_wait: bool = True,
        voice_config: dict[str, Any] | None = None,
    ) -> BidiInteractiveSession:
        """Creates and returns a new BidiInteractiveSession instance."""
        if self.rate_limiter:
            self.rate_limiter.wait_and_consume()
        with self._creds_lock:
            try:
                if hasattr(self.creds, "refresh"):
                    self.creds.refresh(Request())
            except Exception as e:
                logger.debug(
                    "Failed to refresh credentials before interactive session: "
                    f"{e}"
                )
        self._check_audio_requirements()

        config = {
            "session": f"{self.app_name}/sessions/{session_id}",
            "use_tool_fakes": use_tool_fakes,
            "input_audio_config": types.InputAudioConfig(
                audio_encoding=types.AudioEncoding.LINEAR16,
                sample_rate_hertz=SAMPLE_RATE,
            ),
            "output_audio_config": types.OutputAudioConfig(
                audio_encoding=types.AudioEncoding.LINEAR16,
                sample_rate_hertz=SAMPLE_RATE,
            ),
        }

        return BidiInteractiveSession(
            sessions_client=self,
            session_id=session_id,
            config=config,
            capture_agent_audio=capture_agent_audio,
            background_noise_file=background_noise_file,
            bg_noise_snr=bg_noise_snr,
            skip_playback_wait=skip_playback_wait,
            voice_config=voice_config,
        )

    def make_text_request(self, config: dict, inputs: list[dict[str, Any]]):
        if self.rate_limiter:
            self.rate_limiter.wait_and_consume()
        request = types.RunSessionRequest(config=config, inputs=inputs)
        return self.client.run_session(request=request)

    def run(
        self,
        session_id: str,
        text: str | list[str] | None = None,
        dtmf: str | None = None,
        event: str | None = None,
        event_vars: dict[str, Any] | None = None,
        blob: bytes | None = None,
        blob_mime_type: str = "application/octet-stream",
        variables: dict[str, Any] | None = None,
        tool_responses: list[dict[str, Any]] | None = None,
        audio: bytes | None = None,
        audio_config: dict[str, Any] | None = None,
        input_audio_config: dict[str, Any] | None = None,
        output_audio_config: dict[str, Any] | None = None,
        deployment_id: str | None = None,
        historical_contexts: list[dict[str, Any]] | str | None = None,
        turn_count: int | None = None,
        modality: Modality | str = Modality.TEXT,
        use_tool_fakes: bool = False,
        turn_num: int | None = None,
        capture_agent_audio: bool = False,
        background_noise_file: str | None = None,
        burst_noise_files: list[str] | None = None,
        voice_config: dict[str, Any] | None = None,
    ):
        """Sends inputs to a Conversational Agents Session and returns the
        response.

        Args:
            session_id: Unique UUID string or identifying string (e.g. 'test1')
                for the session.
            text: Text input from the user. Can give a single string or list of
                strings.
            dtmf: DTMF input from the user.
            event: Name of a system event to trigger (e.g. 'WELCOME').
            event_vars: Key-value map of variables to inject alongside the
                event.
            blob: Raw binary content (image, pdf, etc.) for multimodal inputs.
            blob_mime_type: Mime type for the blob (defaults to
                'application/octet-stream').
            variables: Key-value state maps to inject for the session turn.
            tool_responses: Pre-computed tool run outputs if mocking tool
                execution.
            audio: Raw audio bytes to send as user input.
            audio_config: Custom turn-specific audio configurations.
            input_audio_config: Custom gRPC properties for input audio
                (defaults to 16kHz linear PCM).
            output_audio_config: Custom gRPC properties for output audio
                (defaults to 16kHz linear PCM).
            deployment_id: Overrides the default deployment ID setting for this
                turn run.

            historical_contexts: An existing conversation ID (string) or raw
                list of dictionaries to pre-set past history.
            turn_count: Truncates historical context limits when pulling from a
                saved conversation ID.
            modality: Running via text (synced) or audio (asynchronous
                bidirectional streaming). Defaults to Modality.TEXT.
            use_tool_fakes: Use fake tools for the session if available.
                Defaults to False.
        """

        if isinstance(modality, str):
            try:
                modality = Modality(modality.lower())
            except ValueError as e:
                raise ValueError(
                    f"Invalid modality: {modality}. Must be 'text' or 'audio'."
                ) from e

        config = {
            "session": f"{self.app_name}/sessions/{session_id}",
            "use_tool_fakes": use_tool_fakes,
        }
        inputs = []

        if modality == Modality.AUDIO:
            self._check_audio_requirements()
            config["input_audio_config"] = (
                input_audio_config
                or types.InputAudioConfig(
                    audio_encoding=types.AudioEncoding.LINEAR16,
                    sample_rate_hertz=SAMPLE_RATE,
                )
            )
            config["output_audio_config"] = (
                output_audio_config
                or types.OutputAudioConfig(
                    audio_encoding=types.AudioEncoding.LINEAR16,
                    sample_rate_hertz=SAMPLE_RATE,
                )
            )

        # Determine deployment/version
        if deployment_id or self.deployment_id:
            config["deployment"] = (
                f"{self.app_name}/deployments/"
                f"{deployment_id or self.deployment_id}"
            )
        # app_version is not supported in SessionConfig, only deployment is.

        if historical_contexts:
            parsed_contexts = []
            if isinstance(historical_contexts, str):
                ch = ConversationHistory(
                    app_name=self.app_name, creds=self.creds
                )
                conv = ch.get_conversation(historical_contexts)
                d = type(conv).to_dict(conv)
                if d.get("turns"):
                    turns_to_process = d["turns"]
                    if turn_count is not None and turn_count > 0:
                        turns_to_process = turns_to_process[:turn_count]

                    for turn in turns_to_process:
                        msgs = turn.get("messages", [])
                        for m in msgs:
                            if "role" in m and "chunks" in m:
                                parsed_contexts.append(
                                    {"role": m["role"], "chunks": m["chunks"]}
                                )
            else:
                for ctx in historical_contexts:
                    if isinstance(ctx, dict):
                        if "role" in ctx and "chunks" in ctx:
                            parsed_contexts.append(ctx)
                        elif "user" in ctx:
                            parsed_contexts.append(
                                {
                                    "role": "user",
                                    "chunks": [{"text": str(ctx["user"])}],
                                }
                            )
                        elif "agent" in ctx or "model" in ctx:
                            role_name = ctx.get("name", "model")
                            text_val = ctx.get("text", "")

                            if not text_val:
                                val = ctx.get("agent") or ctx.get("model")
                                if isinstance(val, str):
                                    text_val = val

                            parsed_contexts.append(
                                {
                                    "role": role_name,
                                    "chunks": [{"text": str(text_val)}],
                                }
                            )
                        else:
                            parsed_contexts.append(ctx)
                    else:
                        raise ValueError(
                            f"historical_contexts must be a list of "
                            f"dictionaries. Received: {type(ctx)}"
                        )
            config["historical_contexts"] = parsed_contexts

        if variables:
            if modality == Modality.TEXT:
                inputs.append({"variables": variables})
            elif modality == Modality.AUDIO and text is None and audio is None:
                inputs.append({"variables": variables})

        if dtmf is not None:
            inputs.append({"dtmf": dtmf})

        if event is not None:
            if event_vars:
                inputs.append({"variables": event_vars})
            inputs.append({"event": {"event": event}})

        # Wrap blob input correctly
        if blob is not None:
            inputs.append({"blob": {"mime_type": blob_mime_type, "data": blob}})

        if audio is not None:
            audio_payload = {"audio": audio}
            if audio_config:
                audio_payload["config"] = audio_config
            if variables and modality == Modality.AUDIO:
                audio_payload["variables"] = variables
            inputs.append({"audio": audio_payload})

        # Wrap tool responses correctly
        if tool_responses is not None:
            inputs.append(
                {"tool_responses": {"tool_responses": tool_responses}}
            )

        if modality == Modality.AUDIO:
            if text is not None:
                if isinstance(text, str):
                    logger.warning(
                        "Single string input for audio modality introduces "
                        "minor latency before user utterances."
                    )
                    text = [text]
                audio_transformer = AudioTransformer()
                input_audio_bytes = []
                lang_code = "en-US"
                if variables and "locale" in variables:
                    lang_code = variables["locale"]

                for input in text:
                    current_voice_config = (voice_config or {}).copy()
                    if "language_code" not in current_voice_config:
                        current_voice_config["language_code"] = lang_code
                    input_audio_bytes.append(
                        audio_transformer.text_to_speech_bytes(
                            text=input,
                            credentials=self.creds,
                            project_id=self.project_id,
                            background_noise_file=background_noise_file,
                            burst_noise_files=burst_noise_files,
                            voice_config=current_voice_config,
                        )
                    )
                for input_data in input_audio_bytes:
                    # Construct input payload matching sessions.py expectation
                    audio_payload = {
                        "audio": input_data["audio_bytes"],
                        "text": input_data["text"],
                    }
                    if variables:
                        audio_payload["variables"] = variables
                    inputs.append({"audio": audio_payload})
                return self.async_bidi_run_session(
                    config=config,
                    inputs=inputs,
                    turn_num=turn_num,
                    capture_agent_audio=capture_agent_audio,
                    background_noise_file=background_noise_file,
                )
            elif inputs:
                return self.async_bidi_run_session(
                    config=config,
                    inputs=inputs,
                    turn_num=turn_num,
                    capture_agent_audio=capture_agent_audio,
                    background_noise_file=background_noise_file,
                )
            else:
                raise ValueError(
                    "Input payloads (text, audio, event, etc.) must be "
                    "provided for audio modality."
                )
        elif modality == Modality.TEXT:
            if text is not None and isinstance(text, str):
                text = [text]

            all_outputs = []
            final_response = None

            if text:
                for input in text:
                    inputs.append({"text": input})
                    response = self.make_text_request(config, inputs)
                    inputs.pop()

                    if response:
                        if hasattr(response, "outputs"):
                            all_outputs.extend(response.outputs)
                        final_response = response
            elif inputs:
                # Handle case where only event/blob/variables are provided
                # without text
                response = self.make_text_request(config, inputs)
                if response:
                    if hasattr(response, "outputs"):
                        all_outputs.extend(response.outputs)
                    final_response = response
            else:
                raise ValueError(
                    "Text or valid inputs (e.g. event) must be provided."
                )

            if final_response:
                return ScrapiRunSessionResponse(
                    original_response=types.RunSessionResponse(
                        outputs=all_outputs
                    ),
                    agent_audio_paths={},
                )
            return final_response
        else:
            if text is None and not inputs:
                raise ValueError("Text or inputs must be provided.")
            raise ValueError("Modality must be either 'text' or 'audio'.")

    def send_event(
        self, unique_id: str, event_name: str, event_vars: dict[str, Any]
    ):
        if self.rate_limiter:
            self.rate_limiter.wait_and_consume()
        config = {"session": f"{self.app_name}/sessions/{unique_id}"}
        inputs = [{"variables": event_vars}, {"event": {"event": event_name}}]

        request = types.RunSessionRequest(config=config, inputs=inputs)

        return self.client.run_session(request=request)
