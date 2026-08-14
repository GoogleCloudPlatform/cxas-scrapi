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

"""Tests for RateLimiter."""

import threading
import time
import unittest

from cxas_scrapi.utils.rate_limiter import RateLimiter


class TestRateLimiter(unittest.TestCase):
    def test_consume_immediate(self) -> None:
        # 60 RPM = 1 RPS. Default capacity = 1.0
        limiter = RateLimiter(requests_per_minute=60)

        # Can consume immediately once
        assert limiter.consume(1.0)
        # Second one should fail immediately
        assert not limiter.consume(1.0)

    def test_wait_and_consume(self) -> None:
        # 600 RPM = 10 RPS (0.1s per request)
        limiter = RateLimiter(requests_per_minute=600)

        start = time.time()
        limiter.wait_and_consume(1.0)  # Consumes the initial request
        limiter.wait_and_consume(1.0)  # Should block for ~0.1s
        duration = time.time() - start

        # Allow small margin for timer inaccuracy
        assert duration >= 0.09

    def test_thread_safety(self) -> None:
        # 1200 RPM = 20 RPS (0.05s per request)
        limiter = RateLimiter(requests_per_minute=1200)

        num_threads = 5
        results = []

        def worker() -> None:
            start = time.time()
            limiter.wait_and_consume(1.0)
            results.append(time.time() - start)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]

        start_time = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        total_duration = time.time() - start_time

        # With 5 threads and 20 RPS, it should take at least 4 * 0.05 = 0.2s
        # (First thread gets request immediately,
        # others wait 0.05, 0.10, 0.15, 0.20)
        assert total_duration >= 0.18


if __name__ == "__main__":
    unittest.main()
