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

"""Helper utilities for working with Proto-Plus and Protobuf descriptors."""

from typing import Any

from google.protobuf import descriptor


def get_proto_type_category(field: Any) -> str:
    """Classifies a Proto-Plus field into basic primitive categories.

    Args:
        field: A proto-plus Field.

    Returns:
        One of "bool", "float", "int", "string", "message", or "other".
    """
    p_type = getattr(field, "proto_type", None)
    if p_type is None and hasattr(field, "descriptor"):
        p_type = getattr(field.descriptor, "type", None)

    if p_type == descriptor.FieldDescriptor.TYPE_BOOL:
        return "bool"
    elif p_type in (
        descriptor.FieldDescriptor.TYPE_FLOAT,
        descriptor.FieldDescriptor.TYPE_DOUBLE,
    ):
        return "float"
    elif p_type in (
        descriptor.FieldDescriptor.TYPE_STRING,
        descriptor.FieldDescriptor.TYPE_BYTES,
    ):
        return "string"
    elif p_type == descriptor.FieldDescriptor.TYPE_MESSAGE:
        return "message"
    else:
        # All other descriptor types are numeric integers (int32, int64, enum,
        # etc.)
        return "int"


def cast_to_proto_type(val: Any, field: Any) -> Any:
    """Safely casts a resolved string value to match the field's proto type.

    Args:
        val: The resolved placeholder value (typically a string).
        field: The target proto-plus Field.

    Returns:
        The cast value (bool, float, int) if conversion is possible, or the
        original value as fallback.
    """
    if not isinstance(val, str):
        return val

    category = get_proto_type_category(field)
    if category == "bool":
        if val.lower() == "true":
            return True
        if val.lower() == "false":
            return False
    elif category == "float":
        try:
            return float(val)
        except ValueError:
            pass
    elif category == "int":
        try:
            return int(val)
        except ValueError:
            pass

    return val


def get_dummy_value_for_field(field: Any) -> Any:
    """Returns a dummy primitive value matching the field type.

    Used to sanitize unresolved environment placeholders during local
    schema linting.

    Args:
        field: The target proto-plus Field.

    Returns:
        A default typed value (True, 0.0, 0, or "dummy_placeholder").
    """
    category = get_proto_type_category(field)
    if category == "bool":
        return True
    elif category == "float":
        return 0.0
    elif category == "int":
        return 0
    return "dummy_placeholder"


def to_camel_case(s: str) -> str:
    """Converts a snake_case string to camelCase.

    Args:
        s: The snake_case string to convert.

    Returns:
        The camelCase string.
    """
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])
