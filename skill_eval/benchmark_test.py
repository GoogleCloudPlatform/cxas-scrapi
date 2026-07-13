# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import time
from unittest import mock

from skill_eval.benchmark import Timer


def test_timer_initial_state():
    timer = Timer()
    assert timer.elapsed == datetime.timedelta()
    assert timer.duration == datetime.timedelta()
    assert timer._start_time is None


def test_timer_active_running():
    timer = Timer()
    with mock.patch.object(time, "perf_counter", side_effect=[10.0, 12.5]):
        timer.__enter__()
        assert timer.elapsed == datetime.timedelta(seconds=2.5)
        assert timer._start_time == 10.0


def test_timer_completed_zero_seconds():
    """Verifies that a timer finishing in 0.0 seconds reliably returns duration."""
    timer = Timer()
    with mock.patch.object(
        time, "perf_counter", side_effect=[100.0, 100.0, 200.0]
    ):
        with timer:
            pass
        # After block exit, _start_time should be None
        assert timer._start_time is None
        assert timer.duration == datetime.timedelta(0)
        # Even later when perf_counter is 200.0, elapsed should return the recorded duration
        assert timer.elapsed == datetime.timedelta(0)


def test_timer_completed_nonzero_seconds():
    timer = Timer()
    with mock.patch.object(
        time, "perf_counter", side_effect=[50.0, 55.2, 100.0]
    ):
        with timer:
            pass
        assert timer._start_time is None
        assert timer.duration == datetime.timedelta(seconds=5.2)
        assert timer.elapsed == datetime.timedelta(seconds=5.2)
