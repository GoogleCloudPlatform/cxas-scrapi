from __future__ import annotations

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

"""Utility modules for CXAS SCRAPI."""

import importlib
from typing import Any

_LAZY_EXPORTS = {
    "ChangelogUtils": "cxas_scrapi.utils.changelog_utils",
    "EvalUtils": "cxas_scrapi.utils.eval_utils",
    "GCSUtils": "cxas_scrapi.utils.gcs_utils",
    "GoogleSheetsUtils": "cxas_scrapi.utils.google_sheets_utils",
    "RateLimiter": "cxas_scrapi.utils.rate_limiter",
    "SecretManagerUtils": "cxas_scrapi.utils.secret_manager_utils",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        mod = importlib.import_module(_LAZY_EXPORTS[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_LAZY_EXPORTS.keys())
