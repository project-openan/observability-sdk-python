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

from dataclasses import dataclass
from importlib.resources import files
from types import MappingProxyType
from typing import Any, Mapping

import yaml


@dataclass(frozen=True, slots=True)
class AttributeDefinition:
    name: str
    signals: str
    value_type: str
    values: frozenset[str]


@dataclass(frozen=True, slots=True)
class SignalDefinition:
    extension: str | None
    allowed: frozenset[str]
    required: frozenset[str]


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    name: str
    histogram: bool
    integer: bool
    a2a: bool
    unit: str
    allowed: frozenset[str]
    required: frozenset[str]


@dataclass(frozen=True, slots=True)
class NamedSignalDefinition:
    name: str | None
    name_prefix: str | None
    signal: SignalDefinition

    def matches(self, candidate: str) -> bool:
        if self.name is not None:
            return candidate == self.name
        return (
            self.name_prefix is not None
            and candidate.startswith(self.name_prefix)
            and len(candidate) > len(self.name_prefix)
        )


@dataclass(frozen=True, slots=True)
class A2aProtocolDefinition:
    attribute_prefix: str
    message: str
    stream: str
    stream_event: str
    stream_error: str
    stream_negotiation: str
    trace_names: frozenset[str]
    client_only_traces: frozenset[str]
    server_only_traces: frozenset[str]
    span: SignalDefinition
    stream_event_span: SignalDefinition
    stream_negotiation_span: SignalDefinition


@dataclass(frozen=True, slots=True)
class SignalCatalog:
    attributes: Mapping[str, AttributeDefinition]
    metrics: Mapping[str, MetricDefinition]
    spans: Mapping[str, SignalDefinition]
    logs: Mapping[str, SignalDefinition]
    traces: Mapping[str, NamedSignalDefinition]
    named_logs: Mapping[str, NamedSignalDefinition]
    versions: Mapping[str, str]
    a2a: A2aProtocolDefinition

    def named_trace(self, name: str) -> SignalDefinition | None:
        return _find_named(self.traces, name)

    def named_log(self, name: str) -> SignalDefinition | None:
        return _find_named(self.named_logs, name)


def load_catalog() -> SignalCatalog:
    attributes: dict[str, AttributeDefinition] = {}
    metrics: dict[str, MetricDefinition] = {}
    spans: dict[str, SignalDefinition] = {}
    logs: dict[str, SignalDefinition] = {}
    traces: dict[str, NamedSignalDefinition] = {}
    named_logs: dict[str, NamedSignalDefinition] = {}
    versions: dict[str, str] = {}
    a2a: A2aProtocolDefinition | None = None

    for filename in ("a2at.yaml", "agent.yaml", "workflow.yaml"):
        resource = files("openan_observability_sdk").joinpath("config", filename)
        try:
            with resource.open("r", encoding="utf-8") as stream:
                root = yaml.safe_load(stream)
        except Exception as error:
            raise RuntimeError(f"Invalid signal configuration: {filename}") from error
        root = _mapping(root, filename)
        signal_name = _string(root.get("signal"), f"{filename}.signal")
        version = _string(root.get("version"), f"{filename}.version")
        if signal_name in versions:
            raise RuntimeError(f"Duplicate signal configuration: {signal_name}")
        versions[signal_name] = version

        for key, raw in _mapping(root.get("attributes", {}), f"{filename}.attributes").items():
            item = _mapping(raw, f"{filename}.attributes.{key}")
            value_type = _string(item.get("type"), f"{filename}.attributes.{key}.type").lower()
            if value_type not in {"string", "integer", "boolean", "string_array"}:
                raise RuntimeError(f"Unsupported attribute type at {filename}.attributes.{key}.type")
            values = frozenset(_strings(item.get("values", []), f"{filename}.attributes.{key}.values"))
            if values and key != "extension.name":
                raise RuntimeError(f"Only extension.name may declare values at {filename}.attributes.{key}")
            signals = item.get("signals", "SML")
            if not isinstance(signals, str) or not signals or any(character not in "SML" for character in signals):
                raise RuntimeError(f"Invalid attribute signals at {filename}.attributes.{key}.signals")
            definition = AttributeDefinition(
                name=_string(item.get("otel_name"), f"{filename}.attributes.{key}.otel_name"),
                signals=signals,
                value_type=value_type,
                values=values,
            )
            previous = attributes.get(key)
            if previous is not None and previous != definition:
                raise RuntimeError(f"Conflicting attribute definition: {key}")
            attributes[key] = definition

        for key, raw in _mapping(root.get("metrics", {}), f"{filename}.metrics").items():
            item = _mapping(raw, f"{filename}.metrics.{key}")
            attribute_spec = _mapping(item.get("attributes", {}), f"{filename}.metrics.{key}.attributes")
            metric_type = _string(item.get("type"), f"{filename}.metrics.{key}.type")
            if metric_type not in {"histogram", "counter"}:
                raise RuntimeError(f"Unsupported metric type at {filename}.metrics.{key}.type")
            value_type = _string(item.get("value_type"), f"{filename}.metrics.{key}.value_type")
            if value_type not in {"integer", "number"}:
                raise RuntimeError(f"Unsupported metric value type at {filename}.metrics.{key}.value_type")
            protocol = item.get("protocol")
            if protocol not in {None, "a2a"}:
                raise RuntimeError(f"Unsupported metric protocol at {filename}.metrics.{key}.protocol")
            definition = MetricDefinition(
                name=_string(item.get("otel_name"), f"{filename}.metrics.{key}.otel_name"),
                histogram=metric_type == "histogram",
                integer=value_type == "integer",
                a2a=protocol == "a2a",
                unit=_string(item.get("unit"), f"{filename}.metrics.{key}.unit", allow_empty=True),
                allowed=frozenset(
                    _strings(attribute_spec.get("allowed"), f"{filename}.metrics.{key}.attributes.allowed")
                ),
                required=frozenset(
                    _strings(attribute_spec.get("required"), f"{filename}.metrics.{key}.attributes.required")
                ),
            )
            if key in metrics:
                raise RuntimeError(f"Duplicate metric: {key}")
            metrics[key] = definition

        for extension, raw in _mapping(root.get("extensions", {}), f"{filename}.extensions").items():
            item = _mapping(raw, f"{filename}.extensions.{extension}")
            if extension in spans or extension in logs:
                raise RuntimeError(f"Duplicate extension: {extension}")
            spans[extension] = _signal(
                item.get("span_attributes"), f"{filename}.extensions.{extension}.span_attributes", extension
            )
            logs[extension] = _signal(
                item.get("log_attributes"), f"{filename}.extensions.{extension}.log_attributes", extension
            )

        _load_named_signals(root.get("traces", {}), f"{filename}.traces", traces)
        _load_named_signals(root.get("logs", {}), f"{filename}.logs", named_logs)

        if "a2a" in root:
            if a2a is not None:
                raise RuntimeError("Duplicate A2A protocol configuration")
            protocol = _mapping(root["a2a"], f"{filename}.a2a")
            special = _mapping(protocol.get("special_traces"), f"{filename}.a2a.special_traces")
            message = _string(special.get("message"), f"{filename}.a2a.special_traces.message")
            stream = _string(special.get("stream"), f"{filename}.a2a.special_traces.stream")
            stream_event = _string(special.get("stream_event"), f"{filename}.a2a.special_traces.stream_event")
            stream_error = _string(special.get("stream_error"), f"{filename}.a2a.special_traces.stream_error")
            stream_negotiation = _string(
                special.get("stream_negotiation"), f"{filename}.a2a.special_traces.stream_negotiation"
            )
            trace_names = frozenset(_strings(protocol.get("trace_names"), f"{filename}.a2a.trace_names"))
            client_only = frozenset(
                _strings(protocol.get("client_only_traces"), f"{filename}.a2a.client_only_traces")
            )
            server_only = frozenset(
                _strings(protocol.get("server_only_traces", []), f"{filename}.a2a.server_only_traces")
            )
            if (
                not {message, stream, stream_event, stream_error, stream_negotiation}.issubset(trace_names)
                or not client_only.issubset(trace_names)
                or not server_only.issubset(trace_names)
                or client_only.intersection(server_only)
            ):
                raise RuntimeError(f"A2A special traces must be listed at {filename}.a2a")
            a2a = A2aProtocolDefinition(
                attribute_prefix=_string(protocol.get("attribute_prefix"), f"{filename}.a2a.attribute_prefix"),
                message=message,
                stream=stream,
                stream_event=stream_event,
                stream_error=stream_error,
                stream_negotiation=stream_negotiation,
                trace_names=trace_names,
                client_only_traces=client_only,
                server_only_traces=server_only,
                span=_signal(protocol.get("span_attributes"), f"{filename}.a2a.span_attributes"),
                stream_event_span=_signal(
                    protocol.get("stream_event_attributes"), f"{filename}.a2a.stream_event_attributes"
                ),
                stream_negotiation_span=_signal(
                    protocol.get("stream_negotiation_attributes"),
                    f"{filename}.a2a.stream_negotiation_attributes",
                    "negotiation-t",
                ),
            )

    if a2a is None:
        raise RuntimeError("Missing A2A protocol configuration")
    return SignalCatalog(
        attributes=MappingProxyType(attributes),
        metrics=MappingProxyType(metrics),
        spans=MappingProxyType(spans),
        logs=MappingProxyType(logs),
        traces=MappingProxyType(traces),
        named_logs=MappingProxyType(named_logs),
        versions=MappingProxyType(versions),
        a2a=a2a,
    )


def _load_named_signals(
    value: Any, path: str, target: dict[str, NamedSignalDefinition]
) -> None:
    for key, raw in _mapping(value, path).items():
        item = _mapping(raw, f"{path}.{key}")
        name = item.get("name")
        name_prefix = item.get("name_prefix")
        if (name is None) == (name_prefix is None):
            raise RuntimeError(f"Provide exactly one name or name_prefix at {path}.{key}")
        if name is not None:
            name = _string(name, f"{path}.{key}.name")
        if name_prefix is not None:
            name_prefix = _string(name_prefix, f"{path}.{key}.name_prefix")
        if key in target:
            raise RuntimeError(f"Duplicate named signal: {key}")
        target[key] = NamedSignalDefinition(
            name=name,
            name_prefix=name_prefix,
            signal=_signal(item.get("attributes"), f"{path}.{key}.attributes"),
        )


def _signal(value: Any, path: str, extension: str | None = None) -> SignalDefinition:
    item = _mapping(value, path)
    return SignalDefinition(
        extension=extension,
        allowed=frozenset(_strings(item.get("allowed"), f"{path}.allowed")),
        required=frozenset(_strings(item.get("required"), f"{path}.required")),
    )


def _find_named(
    definitions: Mapping[str, NamedSignalDefinition], candidate: str
) -> SignalDefinition | None:
    return next((item.signal for item in definitions.values() if item.matches(candidate)), None)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise RuntimeError(f"Expected mapping at {path}")
    return value


def _string(value: Any, path: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise RuntimeError(f"Expected string at {path}")
    return value


def _strings(value: Any, path: str) -> list[str]:
    if not isinstance(value, list):
        raise RuntimeError(f"Expected list at {path}")
    result: list[str] = []
    for item in value:
        text = _string(item, path)
        if text in result:
            raise RuntimeError(f"Duplicate value at {path}")
        result.append(text)
    return result
