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

import unittest

from cxas_scrapi.utils.dependency_manager import SessionDependencyManager


class TestSessionDependencyManager(unittest.TestCase):
    def test_resolve_session_id_memory_hit(self):
        manager = SessionDependencyManager()
        manager._memory_cache["test_test"] = "session_123"

        resolved = manager.resolve_session_id("test_test")
        self.assertEqual(resolved, "session_123")

    def test_resolve_session_id_miss(self):
        manager = SessionDependencyManager()
        resolved = manager.resolve_session_id("test_test")

        self.assertIsNone(resolved)

    def test_cache_session_id(self):
        manager = SessionDependencyManager()
        manager.cache_session_id("test_test", "session_789")

        self.assertEqual(manager._memory_cache["test_test"], "session_789")
        self.assertEqual(manager.resolve_session_id("test_test"), "session_789")
