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

"""Redaction checks: verify what logging redaction keeps and removes.

* ``policy`` - the declarative ``redaction_policy.yaml`` contract.
* ``replay`` - offline replay of raw eval transcripts through DLP templates.
* ``live`` - end-to-end comparison of live session turns with the persisted,
  redacted conversation.
"""

from cxas_scrapi.redaction.checker import (
    CategoryStats,
    Finding,
    FindingKind,
    RedactionReport,
    check_pairs,
)
from cxas_scrapi.redaction.dlp import DlpClient, ReplayMode
from cxas_scrapi.redaction.live import (
    LiveRedactionCheck,
    LiveScriptConversation,
    LiveSessionCapture,
    load_live_script,
)
from cxas_scrapi.redaction.matching import digit_runs, spoken_variants
from cxas_scrapi.redaction.policy import (
    Action,
    Category,
    MatchMode,
    RedactionExpectations,
    RedactionPolicy,
    Surface,
    find_policy_file,
    load_policy,
)
from cxas_scrapi.redaction.replay import RedactionReplay

__all__ = [
    "Action",
    "Category",
    "CategoryStats",
    "DlpClient",
    "Finding",
    "FindingKind",
    "LiveRedactionCheck",
    "LiveScriptConversation",
    "LiveSessionCapture",
    "MatchMode",
    "RedactionExpectations",
    "RedactionPolicy",
    "RedactionReplay",
    "RedactionReport",
    "ReplayMode",
    "Surface",
    "check_pairs",
    "digit_runs",
    "find_policy_file",
    "load_live_script",
    "load_policy",
    "spoken_variants",
]
