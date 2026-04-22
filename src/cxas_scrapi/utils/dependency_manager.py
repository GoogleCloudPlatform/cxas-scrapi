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

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SessionDependencyManager:
    """Manages test dependencies and caches session IDs in memory."""

    def __init__(self):
        self._memory_cache = {}

    def resolve_session_id(self, test_name: str) -> Optional[str]:
        """Resolves a test name to a session ID from memory cache."""
        if test_name in self._memory_cache:
            logger.debug(f"Found session ID for {test_name} in memory cache.")
            return self._memory_cache[test_name]
        return None

    def cache_session_id(self, test_name: str, session_id: str):
        """Caches a session ID in memory."""
        self._memory_cache[test_name] = session_id
        logger.info(f"Cached session ID for {test_name} in memory.")
