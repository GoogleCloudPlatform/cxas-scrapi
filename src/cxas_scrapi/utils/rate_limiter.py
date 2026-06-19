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

"""Rate limiter utilities for CXAS Scrapi using the ratelimit package."""

import logging

from ratelimit import RateLimitException, limits, sleep_and_retry

logger = logging.getLogger(__name__)


class RateLimiter:
    """A thread-safe request pacing rate limiter.

    Useful for limiting request rates to APIs to avoid quota exhaustion.
    Delegates core lock-handling and pacing arithmetic to the PyPI 'ratelimit'
    library.
    """

    def __init__(self, requests_per_minute: float):
        """Initializes the RateLimiter.

        Args:
            requests_per_minute: The sustained rate allowed (RPM).
        """
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be greater than 0")

        # Strict uniform pacing: 1 request allowed every (60.0 / RPM) seconds.
        self.period = 60.0 / requests_per_minute

        # We instantiate the RateLimitDecorator directly
        self._limiter = limits(calls=1, period=self.period)

        # Define a dummy pacing function wrapped with limits decorator
        @self._limiter
        def _pace():
            pass

        self._pace_immediate = _pace
        self._pace_blocking = sleep_and_retry(_pace)

        logger.debug(
            f"Initialized RateLimiter with pacing of 1 request "
            f"every {self.period:.2f}s"
        )

    def consume(self, requests: float = 1.0) -> bool:
        """Attempts to consume requests immediately.

        Args:
            requests: Number of requests to consume.

        Returns:
            True if requests were successfully consumed, False otherwise.
        """
        try:
            for _ in range(int(requests)):
                self._pace_immediate()
            return True
        except RateLimitException:
            return False

    def wait_and_consume(self, requests: float = 1.0) -> None:
        """Consumes requests, blocking if necessary until they become available.

        Args:
            requests: Number of requests to consume.
        """
        for _ in range(int(requests)):
            self._pace_blocking()
