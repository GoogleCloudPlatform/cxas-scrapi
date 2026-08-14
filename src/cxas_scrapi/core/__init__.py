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


"""Core module for CXAS Scrapi."""

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .common import Common
else:
    _LAZY_IMPORTS = {
        "Common": "cxas_scrapi.core.common",
    }

    def __getattr__(name: str) -> Any:
        if name in _LAZY_IMPORTS:
            module_path = _LAZY_IMPORTS[name]
            module = importlib.import_module(module_path)
            return getattr(module, name)
        raise AttributeError(f"module {__name__} has no attribute {name}")


__all__ = ["Common"]
