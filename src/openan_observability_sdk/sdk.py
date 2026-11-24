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
import threading
import time
from collections.abc import Sequence
from typing import Any

from opentelemetry._logs.export import LogRecordExporter, LogRecordExportResult
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricExportResult,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

from openan_observability_sdk.config import ResolvedSdkConfig, SdkConfig
from openan_observability_sdk.definitions import SignalCatalog, load_catalog
from openan_observability_sdk.observability import Observability


_LOG = logging.getLogger("openan_observability_sdk")


class ObservabilitySdk:
    """Owns the OpenTelemetry providers used by this SDK instance."""

    @classmethod
    def start(cls, config: SdkConfig | None = None) -> ObservabilitySdk:
        """Create an SDK instance with the supplied settings and environment defaults."""

        return cls((config or SdkConfig()).resolve())

    def __init__(self, config: ResolvedSdkConfig) -> None:
        self._config = config
        self._closed = False
        self._close_lock = threading.Lock()
        self._update_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._failure_lock = threading.Lock()
        self._export_failures = 0
        self._reported_failures = 0
        self._enabled = threading.Event()
        if config.enabled:
            self._enabled.set()

        self._catalog: SignalCatalog = load_catalog()
        resource = Resource.create({SERVICE_NAME: config.service_name})
        exporters = _build_exporters(config)
        self._span_exporter = _SwitchingSpanExporter(exporters[0], self._record_export_failure)
        self._metric_exporter = _SwitchingMetricExporter(exporters[1], self._record_export_failure)
        self._log_exporter = _SwitchingLogExporter(exporters[2], self._record_export_failure)

        self._tracer_provider = TracerProvider(resource=resource)
        self._tracer_provider.add_span_processor(
            BatchSpanProcessor(
                self._span_exporter,
                max_queue_size=config.span_batch.max_queue_size,
                schedule_delay_millis=_to_millis(config.span_batch.schedule_delay),
                max_export_batch_size=config.span_batch.max_export_batch_size,
                export_timeout_millis=_to_millis(config.span_batch.export_timeout),
            )
        )

        metric_reader = PeriodicExportingMetricReader(
            self._metric_exporter,
            export_interval_millis=_to_millis(config.metric_interval),
            export_timeout_millis=_to_millis(config.export_timeout),
        )
        self._meter_provider: SdkMeterProvider = SdkMeterProvider(resource=resource, metric_readers=[metric_reader])

        self._logger_provider = LoggerProvider(resource=resource)
        self._logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                self._log_exporter,
                max_queue_size=config.log_batch.max_queue_size,
                schedule_delay_millis=_to_millis(config.log_batch.schedule_delay),
                max_export_batch_size=config.log_batch.max_export_batch_size,
                export_timeout_millis=_to_millis(config.log_batch.export_timeout),
            )
        )

        self._observability = Observability(
            tracer=self._tracer_provider.get_tracer("openan.observability"),
            meter=self._meter_provider.get_meter("openan.observability"),
            logger=self._logger_provider.get_logger("openan.observability"),
            catalog=self._catalog,
            enabled=self._enabled,
        )

    def observe(self) -> Observability:
        """Return the signal recording facade associated with this SDK instance."""

        return self._observability

    @property
    def enabled(self) -> bool:
        return self._enabled.is_set()

    def set_enabled(self, value: bool) -> None:
        """Enable or disable creation of new telemetry records."""

        if not isinstance(value, bool):
            raise ValueError("enabled state must be a boolean")
        if value:
            self._enabled.set()
        else:
            self._enabled.clear()

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait for pending telemetry and report provider or exporter failures."""

        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be finite and non-negative")
        with self._flush_lock:
            previous_failures = self._reported_failures
            deadline = time.monotonic() + timeout
            results = []
            for provider in (self._tracer_provider, self._meter_provider, self._logger_provider):
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                results.append(provider.force_flush(timeout_millis=remaining_ms))
            with self._failure_lock:
                current_failures = self._export_failures
                self._reported_failures = current_failures
            return all(results) and current_failures == previous_failures

    def update_exporters(self, config: SdkConfig) -> bool:
        """Replace OTLP exporters while retaining the active providers and instruments.

        The service name, metric interval, and span/log batch settings are fixed when
        the providers are created. Use a new SDK instance to change those settings.
        """

        with self._update_lock:
            if self._closed:
                return False
            try:
                replacement_config = config.resolve()
            except (OSError, TypeError, ValueError):
                _LOG.warning("Observation exporter update rejected")
                return False
            if not _same_provider_settings(self._config, replacement_config):
                return False
            try:
                replacement = _build_exporters(replacement_config)
            except Exception:
                _LOG.warning("Observation exporter update rejected")
                return False
            if not self.flush():
                _shutdown_exporters(replacement)
                return False
            self._span_exporter.replace(replacement[0])
            self._metric_exporter.replace(replacement[1])
            self._log_exporter.replace(replacement[2])
            self._config = replacement_config
            self.set_enabled(replacement_config.enabled)
            return True

    def close(self) -> None:
        """Close providers and release exporter resources. Repeated calls are safe."""

        with self._update_lock, self._flush_lock:
            with self._close_lock:
                if self._closed:
                    return
                self._closed = True
            self._tracer_provider.shutdown()
            self._meter_provider.shutdown()
            self._logger_provider.shutdown()

    def __enter__(self) -> ObservabilitySdk:
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> None:
        self.close()

    def _record_export_failure(self) -> None:
        with self._failure_lock:
            self._export_failures += 1


def _to_millis(seconds: float) -> int:
    return int(seconds * 1000)


def _same_provider_settings(left: ResolvedSdkConfig, right: ResolvedSdkConfig) -> bool:
    return (
        left.service_name == right.service_name
        and left.metric_interval == right.metric_interval
        and left.span_batch == right.span_batch
        and left.log_batch == right.log_batch
    )


def _build_exporters(config: ResolvedSdkConfig) -> tuple[SpanExporter, MetricExporter, LogRecordExporter]:
    created: list[Any] = []
    timeout = config.export_timeout
    try:
        if config.protocol == "grpc":
            import grpc

            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            credentials = None
            if config.trusted_certificate_data is not None or config.client_certificate_data is not None:
                credentials = grpc.ssl_channel_credentials(
                    root_certificates=config.trusted_certificate_data,
                    private_key=config.client_private_key_data,
                    certificate_chain=config.client_certificate_data,
                )
            span = OTLPSpanExporter(
                endpoint=config.endpoint,
                headers=dict(config.headers),
                timeout=timeout,
                credentials=credentials,
            )
            created.append(span)
            metric = OTLPMetricExporter(
                endpoint=config.endpoint,
                headers=dict(config.headers),
                timeout=timeout,
                credentials=credentials,
            )
            created.append(metric)
            logs = OTLPLogExporter(
                endpoint=config.endpoint,
                headers=dict(config.headers),
                timeout=timeout,
                credentials=credentials,
            )
            created.append(logs)
        else:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            common = {
                "certificate_file": str(config.trusted_certificate) if config.trusted_certificate else None,
                "client_key_file": str(config.client_private_key) if config.client_private_key else None,
                "client_certificate_file": str(config.client_certificate) if config.client_certificate else None,
                "headers": dict(config.headers),
                "timeout": timeout,
            }
            span = OTLPSpanExporter(endpoint=f"{config.endpoint}/v1/traces", **common)
            created.append(span)
            metric = OTLPMetricExporter(endpoint=f"{config.endpoint}/v1/metrics", **common)
            created.append(metric)
            logs = OTLPLogExporter(endpoint=f"{config.endpoint}/v1/logs", **common)
            created.append(logs)
        return span, metric, logs
    except Exception:
        _shutdown_exporters(tuple(created))
        raise


def _shutdown_exporters(exporters: Sequence[Any]) -> None:
    for exporter in exporters:
        try:
            exporter.shutdown()
        except Exception:
            _LOG.warning("OTLP exporter shutdown failed")


class _SwitchingSpanExporter(SpanExporter):
    def __init__(self, delegate: SpanExporter, failed: Any) -> None:
        self._delegate = delegate
        self._failed = failed
        self._lock = threading.RLock()

    def export(self, spans):
        with self._lock:
            delegate = self._delegate
            try:
                result = delegate.export(spans)
            except Exception:
                self._failed()
                raise
            if result != SpanExportResult.SUCCESS:
                self._failed()
            return result

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        with self._lock:
            delegate = self._delegate
            return delegate.force_flush(timeout_millis) if hasattr(delegate, "force_flush") else True

    def shutdown(self) -> None:
        with self._lock:
            self._delegate.shutdown()

    def replace(self, replacement: SpanExporter) -> None:
        with self._lock:
            previous = self._delegate
            self._delegate = replacement
        previous.shutdown()


class _SwitchingMetricExporter(MetricExporter):
    def __init__(self, delegate: MetricExporter, failed: Any) -> None:
        super().__init__(
            preferred_temporality=delegate._preferred_temporality,
            preferred_aggregation=delegate._preferred_aggregation,
        )
        self._delegate = delegate
        self._failed = failed
        self._lock = threading.RLock()

    def get_aggregation_temporality(self, instrument_type):
        with self._lock:
            return self._delegate.get_aggregation_temporality(instrument_type)

    def get_default_aggregation(self, instrument_type):
        with self._lock:
            return self._delegate.get_default_aggregation(instrument_type)

    def export(self, metrics, timeout_millis: float = 10_000, **kwargs):
        with self._lock:
            delegate = self._delegate
            try:
                result = delegate.export(metrics, timeout_millis=timeout_millis, **kwargs)
            except Exception:
                self._failed()
                raise
            if result != MetricExportResult.SUCCESS:
                self._failed()
            return result

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        with self._lock:
            delegate = self._delegate
            return delegate.force_flush(timeout_millis) if hasattr(delegate, "force_flush") else True

    def shutdown(self, timeout_millis: int = 30_000, **kwargs) -> None:
        with self._lock:
            delegate = self._delegate
            try:
                delegate.shutdown(timeout_millis=timeout_millis, **kwargs)
            except TypeError:
                delegate.shutdown()

    def replace(self, replacement: MetricExporter) -> None:
        with self._lock:
            previous = self._delegate
            self._delegate = replacement
        previous.shutdown()


class _SwitchingLogExporter(LogRecordExporter):
    def __init__(self, delegate: LogRecordExporter, failed: Any) -> None:
        self._delegate = delegate
        self._failed = failed
        self._lock = threading.RLock()

    def export(self, batch):
        with self._lock:
            delegate = self._delegate
            try:
                result = delegate.export(batch)
            except Exception:
                self._failed()
                raise
            if result != LogRecordExportResult.SUCCESS:
                self._failed()
            return result

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        with self._lock:
            delegate = self._delegate
            return delegate.force_flush(timeout_millis) if hasattr(delegate, "force_flush") else True

    def shutdown(self, timeout_millis: int = 30_000, **kwargs) -> None:
        with self._lock:
            delegate = self._delegate
            try:
                delegate.shutdown(timeout_millis=timeout_millis, **kwargs)
            except TypeError:
                delegate.shutdown()

    def replace(self, replacement: LogRecordExporter) -> None:
        with self._lock:
            previous = self._delegate
            self._delegate = replacement
        previous.shutdown()
