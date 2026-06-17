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

"""Shim for backwards compatibility with legacy reporting module."""

from cxas_scrapi.reporting.reporter import (
    generate_combined_report_from_dir,
    generate_html_report,
    generate_combined_html_report,
    load_golden_results,
    _load_sim_test_cases,
    _upload_to_gcs,
)
from cxas_scrapi.evals.runner import run_all_evals

__all__ = [
    "generate_combined_report_from_dir",
    "generate_html_report",
    "generate_combined_html_report",
    "load_golden_results",
    "_load_sim_test_cases",
    "_upload_to_gcs",
    "run_all_evals",
]
