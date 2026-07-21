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

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cxas_scrapi.utils.changelog_utils import ChangelogUtils
    from cxas_scrapi.utils.eval_utils import EvalUtils
    from cxas_scrapi.utils.gcs_utils import GCSUtils
    from cxas_scrapi.utils.google_sheets_utils import GoogleSheetsUtils
    from cxas_scrapi.utils.rate_limiter import RateLimiter
    from cxas_scrapi.utils.secret_manager_utils import SecretManagerUtils

_DYNAMIC_IMPORTS: dict[str, str] = {
    "ChangelogUtils": "cxas_scrapi.utils.changelog_utils",
    "EvalUtils": "cxas_scrapi.utils.eval_utils",
    "GCSUtils": "cxas_scrapi.utils.gcs_utils",
    "GoogleSheetsUtils": "cxas_scrapi.utils.google_sheets_utils",
    "RateLimiter": "cxas_scrapi.utils.rate_limiter",
    "SecretManagerUtils": "cxas_scrapi.utils.secret_manager_utils",
}


def __getattr__(name: str) -> Any:
    if name in _DYNAMIC_IMPORTS:
        module = importlib.import_module(_DYNAMIC_IMPORTS[name])
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ChangelogUtils",
    "EvalUtils",
    "GCSUtils",
    "GoogleSheetsUtils",
    "RateLimiter",
    "SecretManagerUtils",
]
