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

"""Shared fixtures for redaction tests (all values are synthetic)."""

import copy
import re
import typing

import pytest

from cxas_scrapi.redaction.policy import RedactionPolicy

POLICY_DICT: dict[str, typing.Any] = {
    "version": 1,
    "categories": [
        {
            "id": "payment_card",
            "action": "must_redact",
            "match": "digits",
            "values": ["4111 1111 1111 1111"],
            "keys": ["card_number"],
            "allow_last": 4,
        },
        {"id": "security_code", "action": "must_redact", "keys": ["cvv"]},
        {
            "id": "crisis_line",
            "action": "must_not_redact",
            "match": "digits",
            "values": ["988", "911"],
        },
        {"id": "percent", "action": "must_not_redact", "values": ["20%"]},
        {
            "id": "operational",
            "action": "must_not_redact",
            "keys": ["status_code"],
        },
    ],
}


def _raw_turns() -> list[list[dict[str, typing.Any]]]:
    return [
        [
            {
                "role": "user",
                "chunks": [{"text": "Pay with card 4111 1111 1111 1111."}],
            },
            {
                "role": "Billing",
                "chunks": [
                    {
                        "toolCall": {
                            "tool": "t/take_payment",
                            "args": {
                                "card_number": "4111111111111111",
                                "cvv": "123",
                            },
                        }
                    }
                ],
            },
            {
                "role": "Billing",
                "chunks": [
                    {
                        "toolResponse": {
                            "tool": "t/take_payment",
                            "response": {"result": {"status_code": 200}},
                        }
                    }
                ],
            },
            {
                "role": "Billing",
                "chunks": [
                    {
                        "text": (
                            "Paid with the card ending in 1111. Your copay "
                            "is 20%."
                        )
                    }
                ],
            },
        ],
        [
            {
                "role": "user",
                "chunks": [{"text": "Who do I call in a crisis?"}],
            },
            {
                "role": "Billing",
                "chunks": [{"text": "Call or text 988, or dial 911."}],
            },
        ],
    ]


def fake_redact(text: str) -> str:
    """A deterministic stand-in for a DLP template.

    Redacts 16-digit card numbers (spaced or not) and any 3+ digit number,
    mimicking an over-broad custom rule.
    """
    text = re.sub(r"\b(?:\d[ -]?){15}\d\b", "[CREDIT_CARD_NUMBER]", text)
    return re.sub(r"\b\d{3,}\b", "[BROAD_NUMBER]", text)


def _redact_obj(obj: typing.Any) -> typing.Any:
    if isinstance(obj, dict):
        return {k: _redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_obj(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return fake_redact(repr(float(obj)))
    if isinstance(obj, str):
        return fake_redact(obj)
    return obj


@pytest.fixture
def policy() -> RedactionPolicy:
    return RedactionPolicy.model_validate(copy.deepcopy(POLICY_DICT))


@pytest.fixture
def raw_turns() -> list[list[dict[str, typing.Any]]]:
    return _raw_turns()


@pytest.fixture
def persisted_conversation() -> dict[str, typing.Any]:
    """The persisted (redacted) copy of ``raw_turns``."""
    return {
        "name": "projects/p/locations/l/apps/a/conversations/s1",
        "turns": [{"messages": _redact_obj(t)} for t in _raw_turns()],
    }


@pytest.fixture
def raw_conversation() -> dict[str, typing.Any]:
    """An unredacted conversation (as stored for evaluation runs)."""
    return {
        "name": "projects/p/locations/l/apps/a/conversations/eval-1",
        "turns": [{"messages": t} for t in _raw_turns()],
    }
