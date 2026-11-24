# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the License); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an AS IS BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from openan_observability_sdk.definitions import AttributeDefinition, SignalCatalog, SignalDefinition


_A2AT_PREFIX = "gen_ai.agent.a2at."
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_QUOTED_CREDENTIAL = re.compile(
    r"(?i)((?:token|password|passwd|secret|api[_-]?key|authorization)[\"']?\s*[:=]\s*)"
    r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
)
_CREDENTIAL = re.compile(
    r"(?i)(bearer\s+)[^\s,;\"']+|"
    r"((?:token|password|passwd|secret|api[_-]?key|authorization)[\"']?\s*[:=]\s*"
    r"[\"']?(?:bearer\s+)?)[^\s,;\"']+"
)


def clean(value: str) -> str:
    cleaned = _QUOTED_CREDENTIAL.sub(r"\1[REDACTED]", value)
    cleaned = _CREDENTIAL.sub(r"\1\2[REDACTED]", cleaned)
    cleaned = _URL.sub(lambda match: _clean_url(match.group(0)), cleaned)
    return cleaned[:2048]


def _clean_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if hostname is None:
            return "[REDACTED_URL]"
        host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
        port = parsed.port
        netloc = f"{host}:{port}" if port is not None else host
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except (TypeError, ValueError):
        return "[REDACTED_URL]"


def short_name(name: str, catalog: SignalCatalog) -> str:
    if name.startswith(catalog.a2a.attribute_prefix):
        return name[len(catalog.a2a.attribute_prefix) :]
    if name.startswith(_A2AT_PREFIX):
        return name[len(_A2AT_PREFIX) :]
    return name


def select_extension(
    attributes: Mapping[str, Any], definitions: Mapping[str, SignalDefinition], catalog: SignalCatalog
) -> SignalDefinition | None:
    if not isinstance(attributes, Mapping):
        raise ValueError("attributes must be a mapping")
    found = False
    extension_value: Any = None
    for raw_key, value in attributes.items():
        if isinstance(raw_key, str) and short_name(raw_key, catalog) == "extension.name":
            if found:
                raise ValueError("duplicate extension.name")
            found = True
            extension_value = value
    if not found:
        return None
    if not isinstance(extension_value, str):
        raise ValueError("extension.name must be a string")
    return definitions.get(extension_value.lower())


def validate_attributes(
    attributes: Mapping[str, Any] | None,
    signal: str,
    definition: SignalDefinition | None,
    catalog: SignalCatalog,
    *,
    require_fields: bool = True,
    allowed: frozenset[str] | None = None,
) -> dict[str, Any]:
    if attributes is None:
        attributes = {}
    if not isinstance(attributes, Mapping):
        raise ValueError("attributes must be a mapping")

    converted: dict[str, Any] = {}
    seen: set[str] = set()
    short_values: dict[str, Any] = {}
    for raw_key, value in attributes.items():
        if not isinstance(raw_key, str):
            raise ValueError("attribute names must be strings")
        key = short_name(raw_key, catalog)
        attribute = catalog.attributes.get(key)
        if attribute is None:
            raise ValueError("unknown attribute")
        if raw_key != key and raw_key != attribute.name:
            raise ValueError("attribute name must use its short or declared OpenTelemetry form")
        if key in seen:
            raise ValueError("duplicate attribute")
        if signal not in attribute.signals:
            raise ValueError("attribute is not allowed for this signal type")
        if allowed is not None and key not in allowed:
            raise ValueError("attribute is not allowed for this signal")
        converted_value = _convert_value(key, value, attribute)
        seen.add(key)
        short_values[key] = converted_value
        converted[attribute.name] = converted_value

    if definition is not None:
        if require_fields and not definition.required.issubset(seen):
            raise ValueError("required attribute is missing")
        extension = short_values.get("extension.name")
        task_type = short_values.get("task.type")
        if definition.extension is not None and extension != definition.extension:
            raise ValueError("extension.name does not match the selected extension")
        if task_type is not None and not task_type.strip():
            raise ValueError("task.type must not be blank")
    return converted


def _convert_value(key: str, value: Any, definition: AttributeDefinition) -> Any:
    if definition.value_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("attribute must be a boolean")
        return value
    if definition.value_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool) or value < -(2**63) or value > 2**63 - 1:
            raise ValueError("attribute must be a signed 64-bit integer")
        if key == "parts.number" and value < 0:
            raise ValueError("parts.number cannot be negative")
        return value
    if definition.value_type == "string_array":
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("attribute must be a non-empty string array")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("string arrays must contain non-blank strings")
        return [clean(item) for item in value]
    if not isinstance(value, str):
        raise ValueError("attribute must be a string")
    result = value
    if definition.values:
        result = value.lower()
        if result not in definition.values:
            raise ValueError("attribute has an unsupported value")
    if key == "gen_ai.system" and result != "a2a":
        raise ValueError("gen_ai.system must be a2a")
    if key == "operation.name" and not result.strip():
        raise ValueError("operation.name must not be blank")
    return clean(result)


def convert_general_log_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    if attributes is None:
        return {}
    if not isinstance(attributes, Mapping):
        raise ValueError("attributes must be a mapping")

    converted: dict[str, Any] = {}
    for key, value in attributes.items():
        if not isinstance(key, str) or not key.strip() or value is None:
            raise ValueError("invalid general log attribute")
        if isinstance(value, str):
            converted[key] = clean(value)
        elif isinstance(value, bool):
            converted[key] = value
        elif isinstance(value, int):
            if value < -(2**63) or value > 2**63 - 1:
                raise ValueError("integer log attributes must fit signed 64-bit range")
            converted[key] = value
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("floating-point log attributes must be finite")
            converted[key] = value
        elif isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            converted[key] = [clean(item) for item in value]
        else:
            raise ValueError("unsupported general log attribute type")
    return converted
