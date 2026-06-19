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

"""Protobuf serialization and utility functions."""

import json
from typing import Any

from google.protobuf import json_format
from proto.marshal.collections import maps, repeated


def expand_pb_struct(pb_struct: Any) -> Any:
    """Helper to recursively convert protobuf Struct/Map/Message to standard
    Python dicts/lists.
    """
    if pb_struct is None:
        return None

    # Handle RepeatedComposite (List)
    if isinstance(pb_struct, repeated.RepeatedComposite):
        return [expand_pb_struct(item) for item in pb_struct]

    # Handle MapComposite (Dict)
    if isinstance(pb_struct, maps.MapComposite):
        return {k: expand_pb_struct(v) for k, v in pb_struct.items()}

    # Try standard Protobuf message serialization
    try:
        return json.loads(json_format.MessageToJson(pb_struct))
    except Exception:
        pass

    if hasattr(pb_struct, "items"):
        res = {}
        for k, v in pb_struct.items():
            res[k] = expand_pb_struct(v)
        return res
    elif hasattr(pb_struct, "__iter__") and not isinstance(pb_struct, str):
        return [expand_pb_struct(item) for item in pb_struct]
    else:
        return pb_struct
