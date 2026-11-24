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

import logging
import math
from collections.abc import Mapping
from threading import Event
from typing import Any

from opentelemetry._logs import Logger
from opentelemetry._logs.severity import SeverityNumber
from opentelemetry.context import Context, get_current
from opentelemetry.metrics import Meter
from opentelemetry.trace import Link, SpanKind as OtelSpanKind, Tracer, get_current_span, set_span_in_context

from openan_observability_sdk._attributes import clean, convert_general_log_attributes, select_extension, validate_attributes
from openan_observability_sdk.definitions import MetricDefinition, SignalCatalog, SignalDefinition
from openan_observability_sdk.enums import LogSeverity, SignalKind
from openan_observability_sdk.span import ObservationSpan


_DIAGNOSTICS = logging.getLogger("openan_observability_sdk")
_SEVERITY_NUMBERS = {
    LogSeverity.TRACE: SeverityNumber.TRACE,
    LogSeverity.DEBUG: SeverityNumber.DEBUG,
    LogSeverity.INFO: SeverityNumber.INFO,
    LogSeverity.WARN: SeverityNumber.WARN,
    LogSeverity.ERROR: SeverityNumber.ERROR,
    LogSeverity.FATAL: SeverityNumber.FATAL,
}


class Observability:
    """Records configured A2A-T, workflow, and agent signals."""

    def __init__(
        self,
        tracer: Tracer,
        meter: Meter,
        logger: Logger,
        catalog: SignalCatalog,
        enabled: Event,
    ) -> None:
        self._tracer = tracer
        self._logger = logger
        self._catalog = catalog
        self._enabled = enabled
        self._histograms: dict[str, Any] = {}
        self._counters: dict[str, Any] = {}
        for definition in catalog.metrics.values():
            if definition.histogram:
                self._histograms[definition.name] = meter.create_histogram(definition.name, unit=definition.unit)
            else:
                self._counters[definition.name] = meter.create_counter(definition.name, unit=definition.unit)

    def trace(
        self,
        name: str,
        kind: SignalKind | str,
        attributes: Mapping[str, Any] | None = None,
        parent: ObservationSpan | None = None,
    ) -> ObservationSpan:
        """Start a span. Rejected signals return a safe no-op handle."""

        current_context = get_current()
        if not self._enabled.is_set():
            return ObservationSpan(None, current_context, None, self._catalog)
        try:
            if not isinstance(name, str) or not name:
                raise ValueError("span name is required")
            span_kind = _span_kind(kind)
            attrs = {} if attributes is None else attributes
            a2a = self._catalog.a2a
            is_a2a = name in a2a.trace_names
            is_stream_event = name in {a2a.stream_event, a2a.stream_error}
            if is_a2a:
                if span_kind not in {OtelSpanKind.CLIENT, OtelSpanKind.SERVER}:
                    raise ValueError("A2A spans require CLIENT or SERVER kind")
                if name in a2a.client_only_traces and span_kind != OtelSpanKind.CLIENT:
                    raise ValueError("A2A trace is client-only")
                if name in a2a.server_only_traces and span_kind != OtelSpanKind.SERVER:
                    raise ValueError("A2A trace is server-only")
                extension = select_extension(attrs, self._catalog.spans, self._catalog)
                if name == a2a.stream_negotiation:
                    definition = SignalDefinition(
                        extension=a2a.stream_negotiation_span.extension,
                        allowed=a2a.span.allowed | a2a.stream_negotiation_span.allowed,
                        required=a2a.stream_negotiation_span.required,
                    )
                elif extension is not None:
                    if name not in {a2a.message, a2a.stream}:
                        raise ValueError("A2A extension attributes are only supported on message traces")
                    definition = SignalDefinition(
                        extension=extension.extension,
                        allowed=a2a.span.allowed | extension.allowed,
                        required=extension.required,
                    )
                else:
                    definition = a2a.stream_event_span if name == a2a.stream_event else a2a.span
            else:
                if span_kind != OtelSpanKind.INTERNAL:
                    raise ValueError("Workflow and agent spans require INTERNAL kind")
                definition = self._catalog.named_trace(name)
                if definition is None:
                    raise ValueError("unknown span name")

            converted = validate_attributes(attrs, "S", definition, self._catalog)
            if parent is not None and not isinstance(parent, ObservationSpan):
                raise ValueError("parent must be an ObservationSpan")

            if is_stream_event:
                linked_context = parent.span_context if parent is not None else get_current_span().get_span_context()
                links = [Link(linked_context)] if linked_context is not None and linked_context.is_valid else []
                span_context = Context()
            else:
                links = []
                span_context = parent.context if parent is not None else current_context
            span = self._tracer.start_span(
                name,
                context=span_context,
                kind=span_kind,
                attributes=converted,
                links=links,
            )
            return ObservationSpan(span, set_span_in_context(span, span_context), definition, self._catalog)
        except (AttributeError, TypeError, ValueError):
            _DIAGNOSTICS.warning("Observation trace rejected")
            return ObservationSpan(None, current_context, None, self._catalog)

    def metric(self, metric_id: str, value: int | float, attributes: Mapping[str, Any] | None = None) -> None:
        """Record a metric sample; invalid names, values, or dimensions are discarded."""

        if not self._enabled.is_set():
            return
        try:
            if not isinstance(metric_id, str) or not metric_id:
                raise ValueError("metric name is required")
            definition = self._metric_definition(metric_id)
            if definition is None:
                raise ValueError("unknown metric")
            if metric_id.startswith("gen_ai.agent.a2at.") and definition.a2a:
                raise ValueError("A2A metric names do not use the A2A-T prefix")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("metric value must be numeric")
            numeric_value = float(value)
            if not math.isfinite(numeric_value) or numeric_value < 0:
                raise ValueError("metric value must be finite and non-negative")
            if definition.integer and not isinstance(value, int):
                raise ValueError("metric value must be an integer")
            if definition.integer and (value < 0 or value > 2**63 - 1):
                raise ValueError("metric value must fit a non-negative signed 64-bit integer")

            converted = validate_attributes(
                attributes,
                "M",
                None,
                self._catalog,
                allowed=definition.allowed,
            )
            supplied = {key for key in definition.allowed if self._catalog.attributes[key].name in converted}
            if not definition.required.issubset(supplied):
                raise ValueError("required metric dimension is missing")
            if definition.a2a:
                converted["gen_ai.system"] = "a2a"
            if definition.histogram:
                self._histograms[definition.name].record(numeric_value, attributes=converted)
            else:
                self._counters[definition.name].add(int(value), attributes=converted)
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
            _DIAGNOSTICS.warning("Observation metric rejected")

    def log(
        self,
        name: str,
        severity: LogSeverity | str,
        body: str,
        attributes: Mapping[str, Any] | None = None,
        *,
        span: ObservationSpan | None = None,
        context: Context | None = None,
    ) -> None:
        """Emit a structured log record associated with a span or context."""

        if not self._enabled.is_set():
            return
        try:
            if not isinstance(name, str) or not name or not isinstance(body, str):
                raise ValueError("log name and body are required")
            if span is not None and context is not None:
                raise ValueError("provide span or context, not both")
            normalized_severity = severity if isinstance(severity, LogSeverity) else LogSeverity(str(severity).upper())
            if name == "gen_ai.agent.a2at.message":
                definition = select_extension(attributes or {}, self._catalog.logs, self._catalog)
                if definition is None:
                    raise ValueError("message logs require a supported extension")
            else:
                definition = self._catalog.named_log(name)
                if definition is None:
                    raise ValueError("unknown log name")
            converted = validate_attributes(attributes, "L", definition, self._catalog)
            log_context = span.context if span is not None else context
            self._logger.emit(
                event_name=name,
                severity_number=_SEVERITY_NUMBERS[normalized_severity],
                severity_text=normalized_severity.value,
                body=clean(body),
                attributes=converted,
                context=log_context,
            )
        except (KeyError, TypeError, ValueError):
            _DIAGNOSTICS.warning("Observation log rejected")

    def log_record(
        self,
        severity: LogSeverity | str,
        body: str,
        attributes: Mapping[str, Any] | None = None,
        *,
        event_name: str | None = None,
        timestamp: int | None = None,
        exception: BaseException | None = None,
        context: Context | None = None,
    ) -> None:
        """Emit a general OpenTelemetry log record without a structured A2A-T schema."""

        if not self._enabled.is_set():
            return
        try:
            if not isinstance(body, str):
                raise ValueError("log body is required")
            if event_name is not None and not isinstance(event_name, str):
                raise ValueError("event name must be a string")
            if timestamp is not None and (not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0):
                raise ValueError("timestamp must be non-negative nanoseconds")
            if exception is not None and not isinstance(exception, BaseException):
                raise ValueError("exception must be an exception")
            normalized_severity = severity if isinstance(severity, LogSeverity) else LogSeverity(str(severity).upper())
            converted = convert_general_log_attributes(attributes)
            if exception is not None:
                exception_type = f"{type(exception).__module__}.{type(exception).__qualname__}"
                converted["exception.type"] = exception_type
                converted["exception.message"] = clean(f"{type(exception).__name__}: {exception}")
            self._logger.emit(
                event_name=clean(event_name) if event_name is not None and event_name.strip() else None,
                timestamp=timestamp,
                context=context if context is not None else get_current(),
                severity_number=_SEVERITY_NUMBERS[normalized_severity],
                severity_text=normalized_severity.value,
                body=clean(body),
                attributes=converted,
            )
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError, OverflowError):
            _DIAGNOSTICS.warning("Observation general log rejected")

    def _metric_definition(self, metric_id: str) -> MetricDefinition | None:
        short = metric_id
        if short.startswith("gen_ai.agent.a2at."):
            short = short[len("gen_ai.agent.a2at.") :]
        definition = self._catalog.metrics.get(short)
        if definition is not None:
            return definition
        return next((item for item in self._catalog.metrics.values() if item.name == metric_id), None)


def _span_kind(value: SignalKind | str) -> OtelSpanKind:
    name = value.value if isinstance(value, SignalKind) else str(value).upper()
    try:
        return OtelSpanKind[name]
    except KeyError as error:
        raise ValueError("unsupported span kind") from error
