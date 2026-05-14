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

"""Trace-specific helpers for the `cxas trace` command surface.

Grouped here (instead of the flat `utils/`) to keep observability-related
modules separate from generic shared utilities. The orchestration class
`Traces` lives in `cxas_scrapi.core.traces` and composes these helpers.
"""
