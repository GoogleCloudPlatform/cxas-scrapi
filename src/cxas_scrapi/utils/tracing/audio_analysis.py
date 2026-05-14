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

"""Registry of audio analysis types for the `cxas trace audio analyze` command.

Each analysis declares (a) a stable name, (b) a Gemini prompt, and (c) a
file-filter that picks which audio files in a conversation it cares about
(e.g. `agent-turn-*.wav` vs `full-session.wav`). Conversations are stored in
GCS as a directory of files per conversation, so passing only the relevant
files keeps the Gemini call focused and cheap.

To add a new analysis: subclass `AudioAnalysis`, append an instance to
`_ALL_ANALYSES`, and the `ANALYSIS_REGISTRY` will pick it up automatically.

Prompt overrides can be supplied via `trace.yaml` under
`gemini.audio_metrics.<analysis_name>.prompt` — the runtime resolves overrides
in `Traces.analyze_audio` and falls back to the prompt defined here.
"""

import enum
from abc import ABC, abstractmethod


class AnalysisType(str, enum.Enum):
    """String-valued enum (compatible with Python 3.10+; `enum.StrEnum`
    is only available from 3.11)."""

    AGENT_VOICE_CONSISTENCY = "agent_voice_consistency"
    NO_LONG_PAUSES = "no_long_pauses"
    AGENT_HAVING_TROUBLE = "agent_having_trouble"
    AGENT_LOOPING = "agent_looping"
    AGENT_CUTOFF = "agent_cutoff"

    def __str__(self) -> str:
        return self.value


class AudioAnalysis(ABC):
    """Base class for audio analyses."""

    @property
    @abstractmethod
    def name(self) -> AnalysisType:
        """Stable name; must match the AnalysisType enum value."""

    @property
    @abstractmethod
    def prompt(self) -> str:
        """The Gemini prompt to run on the filtered audio files."""

    @abstractmethod
    def filter_files(self, files_in_conversation: list[str]) -> list[str]:
        """Picks the subset of conversation audio files this analysis needs."""


class VoiceConsistencyAnalysis(AudioAnalysis):
    """Analysis for voice consistency."""

    @property
    def name(self) -> AnalysisType:
        return AnalysisType.AGENT_VOICE_CONSISTENCY

    @property
    def prompt(self) -> str:
        return """
# Setup
- You are a voice analysis assistant that will analyze conversation audio/wav files.
- You will be given a list of audio clips / files. Each clip contains the audio of one person speaking.

# Task
- Your task is to determine if all the audio files are from the same speaker.
- You should analyze the voice profile (including pitch, timbre, and accent) to determine if the files are from the same speaker.
- For finding outlier audio profiles, look for distinct differences (e.g. male vs female voice / pitch, different timbres) rather than minor fluctuations.

# Output
- Report PASS if all audio files are from the same speaker and provide a justification.
- Report FAIL if any of the audio files are from a different speaker and provide a justification.
"""

    def filter_files(self, files_in_conversation: list[str]) -> list[str]:
        return [f for f in files_in_conversation if "agent-turn" in f]


class NoLongPausesAnalysis(AudioAnalysis):
    """Analysis for long pauses."""

    @property
    def name(self) -> AnalysisType:
        return AnalysisType.NO_LONG_PAUSES

    @property
    def prompt(self) -> str:
        return """
# Setup
- You are a voice analysis assistant that will analyze conversation audio/wav files.
- You will be given an audio clip of a conversation between two people.

# Task
- Your task is to determine if the conversation contains any pauses longer than 20 seconds.
- For finding pauses, look for distinct differences (e.g. silence or non-speech sounds like music or ambient noise) rather than minor fluctuations.
- If there is a pause between 0 and 19 seconds, that should not be considered a long pause.

# Output
- Report PASS if the audio clip does not contain long pauses and provide a justification.
- Report FAIL if the audio clip contains a long pause and provide a justification.
"""

    def filter_files(self, files_in_conversation: list[str]) -> list[str]:
        return [f for f in files_in_conversation if "full-session" in f]


class AgentHavingTroubleAnalysis(AudioAnalysis):
    """Analysis for agent having trouble."""

    @property
    def name(self) -> AnalysisType:
        return AnalysisType.AGENT_HAVING_TROUBLE

    @property
    def prompt(self) -> str:
        return """
# Setup
- You are a voice analysis assistant that will analyze conversation audio/wav files.
- You will be given a list of audio clips / files. Each clip contains the audio of one person speaking.

# Task
- Your task is to determine if any of the audio clips contain a person saying they are having trouble.

# Output
- Report PASS if none of the audio files contain a person saying they are having trouble and provide a justification.
- Report FAIL if any of the audio files contain a person saying they are having trouble and provide a justification.
"""

    def filter_files(self, files_in_conversation: list[str]) -> list[str]:
        return [f for f in files_in_conversation if "agent-turn" in f]


class AgentLoopingAnalysis(AudioAnalysis):
    """Analysis for agent looping."""

    @property
    def name(self) -> AnalysisType:
        return AnalysisType.AGENT_LOOPING

    @property
    def prompt(self) -> str:
        return """
# Setup
- You are a voice analysis assistant that will analyze conversation audio/wav files.
- You will be given a list of audio clips / files. All audio clips are of the virtual agent speaking in a conversation.
- A common issue with virtual agents is to get stuck in a loop, repeating the same sentence or phrase multiple times.

# Task
- Your task is to determine if the speaker repeats the same sentence or phrase multiple times in the conversation.
- The repetition could be exact or contain minor variations, but the sentiment should be the same. Questions and statements never have the same sentiment and should not be considered to repeat.
- The repetition could occur in the same file or across multiple files.

# Output
- Report PASS if none of the audio files contain a person repeating the same sentence or phrase multiple times and provide a justification.
- Report FAIL if any of the audio files contain a person repeating the same sentence or phrase multiple times and provide a justification.
"""

    def filter_files(self, files_in_conversation: list[str]) -> list[str]:
        return [f for f in files_in_conversation if "agent-turn" in f]


class AgentCutoffAnalysis(AudioAnalysis):
    """Analysis for agent sentence cutoff."""

    @property
    def name(self) -> AnalysisType:
        return AnalysisType.AGENT_CUTOFF

    @property
    def prompt(self) -> str:
        return """
# Setup
- You are a voice analysis assistant that will analyze conversation audio/wav files.
- You will be given a list of audio clips / files. All audio clips are of the virtual agent speaking in a conversation.

# Task
- Your task is to determine if any of the agent's sentences are cutoff or abruptly ended before the sentence is completed.
- This could happen due to system issues or early termination of the turn.

# Output
- Report PASS if none of the audio files contain a sentence that is cutoff and provide a justification.
- Report FAIL if any of the audio files contain a sentence that is cutoff and provide a justification.
"""

    def filter_files(self, files_in_conversation: list[str]) -> list[str]:
        return [f for f in files_in_conversation if "agent-turn" in f]


_ALL_ANALYSES: list[AudioAnalysis] = [
    VoiceConsistencyAnalysis(),
    NoLongPausesAnalysis(),
    AgentHavingTroubleAnalysis(),
    AgentLoopingAnalysis(),
    AgentCutoffAnalysis(),
]

ANALYSIS_REGISTRY: dict[str, AudioAnalysis] = {
    str(analysis.name): analysis for analysis in _ALL_ANALYSES
}
