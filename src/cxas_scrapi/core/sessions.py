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
import mimetypes
import os
import re
import sys
import threading
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

import certifi
import requests
import websocket
from google.auth.transport.requests import Request
from google.cloud import ces_v1beta
from google.cloud.ces_v1beta import SessionServiceClient, types
from google.protobuf import json_format

try:
    from IPython.display import HTML, display  # noqa: F401

    HAS_IPYTHON = True
except ImportError:
    HAS_IPYTHON = False

from cxas_scrapi.core.audio_transformer import AudioTransformer
from cxas_scrapi.core.common import Common
from cxas_scrapi.core.conversation_history import ConversationHistory

logger = logging.getLogger(__name__)


class Modality(str, Enum):
    TEXT = "text"
    AUDIO = "audio"


BIDI_SESSION_URI = (
    "wss://ces.googleapis.com/ws/"
    "google.cloud.ces.v1.SessionService/BidiRunSession/locations/"
)
AUDIO_CHUNK_SIZE = 3200
CHUNK_DELAY = 0.1
SILENCE_PADDING_CHUNKS = 3
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2


class AgentTurnManager:
    """Manages the agent's turn by simulating audio playback time."""

    def __init__(
        self, sample_rate: int = SAMPLE_RATE, sample_width: int = SAMPLE_WIDTH
    ):
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.bytes_per_second = sample_rate * sample_width

        self.len_audio_bytes_received = 0
        self.turn_completed_flag = False
        self.first_audio_received_time = None
        self.lock = threading.Lock()

    def add_audio(self, audio_bytes: bytes):
        with self.lock:
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

    def is_agent_done_talking(self) -> bool:
        with self.lock:
            if not self.turn_completed_flag:
                return False

            if self.first_audio_received_time is None:
                return True  # Agent didn't send any audio

            audio_duration_seconds = (
                self.len_audio_bytes_received / self.bytes_per_second
            )
            current_playback_time = time.time() - self.first_audio_received_time

            return current_playback_time >= audio_duration_seconds


class BidiSessionHandler:
    """Handles the Bidi WebSocket session with the session service."""

    def __init__(
        self,
        location: str,
        token: str,
        config: Dict[str, Any],
        inputs: List[Dict[str, Any]],
    ):
        self.uri = BIDI_SESSION_URI + location
        self.token = token
        self.config = config
        self.inputs = inputs
        self.agent_turn_manager = AgentTurnManager()
        self.ws_app = None
        self.outputs = []

    def _send_silence(self, num_chunks: int):
        silence_chunk = b"\x00" * AUDIO_CHUNK_SIZE
        for _ in range(num_chunks):
            query_message = ces_v1beta.BidiSessionClientMessage(
                realtime_input=ces_v1beta.SessionInput(audio=silence_chunk)
            )
            query_json = json_format.MessageToJson(
                query_message._pb,
                preserving_proto_field_name=False,
                indent=None,
            )
            self.ws_app.send(query_json)
            time.sleep(CHUNK_DELAY)

    def _send_audio_message(
        self, audio_payload: Dict[str, Any], turn_index: int
    ):
        audio_bytes = audio_payload["audio"]

        logging.debug("Sending leading silence before turn %d...", turn_index)
        self._send_silence(
            SILENCE_PADDING_CHUNKS
        )  # 0.3 seconds of leading silence

        logging.debug("Sending audio chunks for turn %d...", turn_index)

        variables = audio_payload.get("variables")
        for i in range(0, len(audio_bytes), AUDIO_CHUNK_SIZE):
            chunk = audio_bytes[i : i + AUDIO_CHUNK_SIZE]

            input_args = {"audio": chunk}
            if i == 0 and variables:
                input_args["variables"] = variables

            query_message = ces_v1beta.BidiSessionClientMessage(
                realtime_input=ces_v1beta.SessionInput(**input_args)
            )
            query_json = json_format.MessageToJson(
                query_message._pb,
                preserving_proto_field_name=False,
                indent=None,
            )
            self.ws_app.send(query_json)
            time.sleep(CHUNK_DELAY)

        logging.debug(
            "Sending trailing silence for turn %d to trigger endpointing...",
            turn_index,
        )
        self._send_silence(
            SILENCE_PADDING_CHUNKS
        )  # 0.3 seconds of trailing silence

        logging.debug("Waiting for agent to finish turn %d...", turn_index)
        while not self.agent_turn_manager.is_agent_done_talking():
            self._send_silence(1)

        self.agent_turn_manager.reset()
        time.sleep(1)  # Small pause between turns

    def _send_inputs(self):
        try:
            logging.debug("Config dict: %s", self.config)
            config_message = ces_v1beta.BidiSessionClientMessage(
                config=ces_v1beta.SessionConfig(
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

            if not self.inputs:
                logging.debug("No inputs provided.")
                self.ws_app.close()
                return

            for idx, input_item in enumerate(self.inputs):
                if "audio" in input_item:
                    self._send_audio_message(input_item["audio"], idx)
                    continue

                # Handle non-audio structured inputs (event, text, variables)
                try:
                    session_input_pb = ces_v1beta.SessionInput()._pb
                    json_format.ParseDict(
                        input_item,
                        session_input_pb,
                        ignore_unknown_fields=False,
                    )
                    session_input = ces_v1beta.SessionInput(session_input_pb)

                    query_message = ces_v1beta.BidiSessionClientMessage(
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
                        logging.debug(
                            "Waiting for agent to finish processing turn %d...",
                            idx,
                        )
                        while (
                            not self.agent_turn_manager.is_agent_done_talking()
                        ):
                            time.sleep(1)

                        self.agent_turn_manager.reset()
                        time.sleep(1)

                except Exception as e:
                    logging.debug("Failed to send generic input: %s", e)

            logging.debug("All inputs sent and turns completed.")
            time.sleep(1)  # arbitrary short wait before disconnecting
            self.ws_app.close()

        except Exception as e:
            logging.debug("Error during send_inputs: %s", e)
            if self.ws_app:
                self.ws_app.close()

    def _on_open(self, ws):
        logging.debug("WebSocket connection opened")
        threading.Thread(target=self._send_inputs, daemon=True).start()

    def _on_message(self, ws, message):
        logging.debug("===============")
        logging.debug("Received message: %s...", message[:100])
        try:
            response_pb = ces_v1beta.BidiSessionServerMessage()._pb
            json_format.Parse(
                message,
                response_pb,
                ignore_unknown_fields=True,
            )
            response = ces_v1beta.BidiSessionServerMessage(response_pb)

            if response.session_output:
                self.outputs.append(response.session_output)

                if response.session_output.audio:
                    self.agent_turn_manager.add_audio(
                        response.session_output.audio
                    )

                if response.session_output.turn_completed:
                    logging.debug(
                        "Agent turn network payload completed. "
                        "Waiting for audio playback."
                    )
                    self.agent_turn_manager.mark_turn_completed()

        except Exception as e:
            logging.debug("Failed to parse message: %s", e)

    def _on_error(self, ws, error):
        logging.debug("WebSocket error: %s", error)

    def _on_close(self, ws, close_status_code, close_msg):
        logging.debug(
            "WebSocket connection closed with code %s and reason: %s",
            close_status_code,
            close_msg,
        )

    def run(self):
        logging.debug("Connecting to WebSocket: %s", self.uri)
        self.ws_app = websocket.WebSocketApp(
            self.uri,
            header={"Authorization": f"Bearer {self.token}"},
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

        logging.debug("Waiting for session to complete...")
        wst.join()

        return ces_v1beta.RunSessionResponse(outputs=self.outputs)


class Sessions(Common):
    def __init__(
        self,
        app_name: str,
        deployment_id: str = None,
        version_id: str = None,
        **kwargs,
    ):
        """Initializes the Sessions client."""
        super().__init__(app_name=app_name, **kwargs)

        # Initialize Sessions Client
        self.client = SessionServiceClient(
            credentials=self.creds,
            client_options=self.client_options,
            client_info=self.client_info,
        )

        self.app_name = app_name
        self.deployment_id = deployment_id
        self.version_id = version_id

    def _check_audio_requirements(self):
        """Checks if the necessary APIs are enabled and user has permissions."""
        if not self.project_id:
            raise ValueError(
                "Project ID could not be determined from the app_name. "
                "Audio preflight checks cannot be performed without a "
                "project ID."
            )

        services = ["ces.googleapis.com", "texttospeech.googleapis.com"]

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
    def get_file_data(file_path: str) -> Dict[str, Any]:
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

    def parse_result(self, res: Any):  # noqa: C901
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

    def _process_output_tool_calls(
        self, output: Any, tool_calls: list[Dict[str, Any]], session_ended: bool
    ) -> bool:
        """Processes tool calls from output.tool_calls."""
        tc_msg = getattr(output, "tool_calls", None)
        if tc_msg and hasattr(tc_msg, "tool_calls"):
            for tc in tc_msg.tool_calls:
                tool_name = getattr(tc, "display_name", "") or getattr(
                    tc, "tool", ""
                )
                args = (
                    Sessions._expand_pb_struct(tc.args)
                    if hasattr(tc, "args")
                    else {}
                )
                tool_calls.append({"action": tool_name, "args": args})
                if "end_session" in (tool_name or ""):
                    session_ended = True
        return session_ended

    def _process_diagnostic_info(
        self,
        output: Any,
        tool_calls: list[Dict[str, Any]],
        agent_transfer: Any,
        session_ended: bool,
    ) -> tuple[Any, bool]:
        """Processes diagnostic info from output."""
        diagnostic_info = getattr(output, "diagnostic_info", None)
        if diagnostic_info and hasattr(diagnostic_info, "messages"):
            for message in diagnostic_info.messages:
                for chunk in getattr(message, "chunks", []):
                    fc = getattr(chunk, "function_call", None)
                    if fc:
                        tc_name = getattr(fc, "name", "")
                        tc_args = (
                            Sessions._expand_pb_struct(fc.args)
                            if hasattr(fc, "args")
                            else {}
                        )
                        if tc_name and not any(
                            t["action"] == tc_name for t in tool_calls
                        ):
                            tool_calls.append(
                                {"action": tc_name, "args": tc_args}
                            )
                            if "end_session" in tc_name:
                                session_ended = True

                    fr = getattr(chunk, "function_response", None)
                    if fr:
                        fr_name = getattr(fr, "name", "")
                        fr_resp = (
                            Sessions._expand_pb_struct(fr.response)
                            if hasattr(fr, "response")
                            else {}
                        )
                        tool_calls.append(
                            {
                                "action": f"_response:{fr_name}",
                                "args": {},
                                "response": fr_resp,
                            }
                        )

                actions = getattr(message, "actions", None)
                if (
                    actions
                    and hasattr(actions, "transfer_to_agent")
                    and actions.transfer_to_agent
                ):
                    agent_transfer = actions.transfer_to_agent
        return agent_transfer, session_ended

    def get_structured_response(self, response) -> Dict[str, Any]:
        """Parse response, avoiding duplicate text from diagnostic info.

        Returns a dictionary with keys:
        - agent_text: Consolidated text response.
        - tool_calls: List of tool calls made by agent.
        - tool_responses: List of tool responses received.
        - agent_transfer: Target agent if a transfer occurred.
        - session_ended: Boolean indicating if session ended.
        """
        agent_texts = []
        tool_calls = []
        agent_transfer = None
        session_ended = False

        for output in response.outputs:
            if hasattr(output, "text") and output.text:
                agent_texts.append(output.text)

            session_ended = self._process_output_tool_calls(
                output, tool_calls, session_ended
            )
            agent_transfer, session_ended = self._process_diagnostic_info(
                output, tool_calls, agent_transfer, session_ended
            )

        agent_text = " ".join(agent_texts).strip() if agent_texts else ""

        return {
            "agent_text": agent_text,
            "tool_calls": [
                t
                for t in tool_calls
                if not t["action"].startswith("_response:")
            ],
            "tool_responses": [
                t for t in tool_calls if t["action"].startswith("_response:")
            ],
            "agent_transfer": agent_transfer,
            "session_ended": session_ended,
        }

    def async_bidi_run_session(
        self, config: dict, inputs: list[dict[str, Any]]
    ):
        handler = BidiSessionHandler(self.location, self.token, config, inputs)
        return handler.run()

    def make_text_request(self, config: dict, inputs: list[dict[str, Any]]):
        request = types.RunSessionRequest(config=config, inputs=inputs)
        return self.client.run_session(request=request)

    def run(  # noqa: C901
        self,
        session_id: str,
        text: Optional[str | list[str]] = None,
        dtmf: Optional[str] = None,
        event: Optional[str] = None,
        event_vars: Optional[Dict[str, Any]] = None,
        blob: bytes = None,
        blob_mime_type: str = "application/octet-stream",
        variables: Optional[Dict[str, Any]] = None,
        tool_responses: Optional[List[Dict[str, Any]]] = None,
        audio: bytes = None,
        audio_config: Optional[Dict[str, Any]] = None,
        input_audio_config: Optional[Dict[str, Any]] = None,
        output_audio_config: Optional[Dict[str, Any]] = None,
        deployment_id: Optional[str] = None,
        version_id: Optional[str] = None,
        historical_contexts: Optional[List[Dict[str, Any]] | str] = None,
        turn_count: Optional[int] = None,
        modality: Modality | str = Modality.TEXT,
        use_tool_fakes: bool = False,
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
            version_id: Overrides the default app version setting for this turn
                run.
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

        config = {"session": f"{self.app_name}/sessions/{session_id}"}
        if use_tool_fakes:
            config["use_tool_fakes"] = True
        inputs = []

        if modality == Modality.AUDIO:
            self._check_audio_requirements()
            config["input_audio_config"] = (
                input_audio_config
                or ces_v1beta.InputAudioConfig(
                    audio_encoding=ces_v1beta.AudioEncoding.LINEAR16,
                    sample_rate_hertz=SAMPLE_RATE,
                )
            )
            config["output_audio_config"] = (
                output_audio_config
                or ces_v1beta.OutputAudioConfig(
                    audio_encoding=ces_v1beta.AudioEncoding.LINEAR16,
                    sample_rate_hertz=SAMPLE_RATE,
                )
            )

        # Determine deployment/version
        if deployment_id or self.deployment_id:
            config["deployment"] = (
                f"{self.app_name}/deployments/"
                f"{deployment_id or self.deployment_id}"
            )
        if version_id or self.version_id:
            config["app_version"] = (
                f"{self.app_name}/app_versions/{version_id or self.version_id}"
            )

        if historical_contexts:
            parsed_contexts = []
            if isinstance(historical_contexts, str):
                ch = ConversationHistory(
                    app_name=self.app_name, creds=self.creds
                )
                conv = ch.get_conversation(historical_contexts)
                d = type(conv).to_dict(conv)
                if "turns" in d and d["turns"]:
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

        if variables and modality == Modality.TEXT:
            inputs.append({"variables": variables})

        if dtmf is not None:
            inputs.append({"dtmf": dtmf})

        if event is not None:
            event_payload = {"event": event}
            if event_vars:
                event_payload["variables"] = event_vars
            inputs.append({"event": event_payload})

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
                for input in text:
                    input_audio_bytes.append(
                        audio_transformer.text_to_speech_bytes(
                            text=input,
                            credentials=self.creds,
                            project_id=self.project_id,
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
                return self.async_bidi_run_session(config=config, inputs=inputs)
            elif inputs:
                return self.async_bidi_run_session(config=config, inputs=inputs)
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
                return types.RunSessionResponse(outputs=all_outputs)
            return final_response
        else:
            if text is None and not inputs:
                raise ValueError("Text or inputs must be provided.")
            raise ValueError("Modality must be either 'text' or 'audio'.")

    def send_event(
        self, unique_id: str, event_name: str, event_vars: Dict[str, Any]
    ):
        config = {"session": f"{self.app_name}/sessions/{unique_id}"}
        inputs = [{"event": {"event": event_name, "variables": event_vars}}]

        request = types.RunSessionRequest(config=config, inputs=inputs)

        return self.client.run_session(request=request)
