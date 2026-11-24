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
import os
import re
import ssl
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from urllib.parse import unquote_plus, urlsplit


_HEADER_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]*\Z")


@dataclass(frozen=True, slots=True)
class BatchConfig:
    """Batch processor settings for spans or log records."""

    max_queue_size: int = 2048
    max_export_batch_size: int = 512
    schedule_delay: float = 5.0
    export_timeout: float = 30.0

    def __post_init__(self) -> None:
        if not _is_integer(self.max_queue_size) or self.max_queue_size < 1:
            raise ValueError("max_queue_size must be positive")
        if (
            not _is_integer(self.max_export_batch_size)
            or self.max_export_batch_size < 1
            or self.max_export_batch_size > self.max_queue_size
        ):
            raise ValueError("max_export_batch_size must be positive and no larger than max_queue_size")
        _validate_seconds(self.schedule_delay, "schedule_delay", allow_zero=False, min_millisecond=True)
        _validate_seconds(self.export_timeout, "export_timeout", allow_zero=True, min_millisecond=False)


@dataclass(frozen=True, slots=True)
class SdkConfig:
    """OpenTelemetry OTLP configuration.

    Explicit values take precedence over their OpenTelemetry environment variables.
    Durations in this Python API are expressed in seconds; OTEL duration environment
    variables continue to use milliseconds where required by the specification.
    """

    endpoint: str | None = None
    protocol: str | None = None
    service_name: str | None = None
    metric_interval: float | None = None
    export_timeout: float | None = None
    headers: Mapping[str, str] | None = None
    trusted_certificate: str | Path | None = None
    client_certificate: str | Path | None = None
    client_private_key: str | Path | None = None
    enabled: bool = True
    span_batch: BatchConfig = field(default_factory=BatchConfig)
    log_batch: BatchConfig = field(default_factory=lambda: BatchConfig(schedule_delay=1.0))

    def resolve(self, environ: Mapping[str, str] | None = None) -> ResolvedSdkConfig:
        env = os.environ if environ is None else environ
        protocol_value = _select(self.protocol, env.get("OTEL_EXPORTER_OTLP_PROTOCOL"), "grpc")
        if not isinstance(protocol_value, str):
            raise ValueError("Invalid OTEL_EXPORTER_OTLP_PROTOCOL")
        protocol = protocol_value.lower()
        if protocol not in {"grpc", "http/protobuf"}:
            raise ValueError("Invalid OTEL_EXPORTER_OTLP_PROTOCOL")

        default_endpoint = "http://localhost:4317" if protocol == "grpc" else "http://localhost:4318"
        endpoint = _select(self.endpoint, env.get("OTEL_EXPORTER_OTLP_ENDPOINT"), default_endpoint)
        if not isinstance(endpoint, str):
            raise ValueError("Invalid OTEL_EXPORTER_OTLP_ENDPOINT")
        parsed_endpoint = urlsplit(endpoint)
        if (
            parsed_endpoint.scheme.lower() not in {"http", "https"}
            or not parsed_endpoint.hostname
            or parsed_endpoint.username is not None
            or parsed_endpoint.password is not None
            or parsed_endpoint.query
            or parsed_endpoint.fragment
        ):
            raise ValueError("Invalid OTEL_EXPORTER_OTLP_ENDPOINT")
        try:
            parsed_endpoint.port
        except ValueError as error:
            raise ValueError("Invalid OTEL_EXPORTER_OTLP_ENDPOINT") from error
        endpoint = endpoint.rstrip("/")

        if self.service_name is not None:
            if not isinstance(self.service_name, str) or not self.service_name.strip():
                raise ValueError("service_name is empty")
            service_name = self.service_name
        else:
            service_name = _select(None, env.get("OTEL_SERVICE_NAME"), "unknown_service")
        if not isinstance(service_name, str):
            raise ValueError("Invalid OTEL_SERVICE_NAME")

        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")

        if self.metric_interval is not None:
            metric_interval = _validate_seconds(
                self.metric_interval, "metric_interval", allow_zero=False, min_millisecond=True
            )
        else:
            metric_interval = _milliseconds(env.get("OTEL_METRIC_EXPORT_INTERVAL"), 60_000) / 1000.0
        if not math.isfinite(metric_interval) or metric_interval < 0.001:
            raise ValueError("Invalid OTEL_METRIC_EXPORT_INTERVAL")

        if self.export_timeout is not None:
            export_timeout = _validate_seconds(
                self.export_timeout, "export_timeout", allow_zero=False, min_millisecond=True
            )
        else:
            export_timeout = _milliseconds(env.get("OTEL_EXPORTER_OTLP_TIMEOUT"), 10_000) / 1000.0
        if not math.isfinite(export_timeout) or export_timeout < 0.001:
            raise ValueError("Invalid OTEL_EXPORTER_OTLP_TIMEOUT")

        if self.headers is not None:
            configured_headers = dict(self.headers)
        else:
            configured_headers = _parse_headers(env.get("OTEL_EXPORTER_OTLP_HEADERS"))
        normalized_headers = _normalize_headers(configured_headers)

        ca_path_value = self.trusted_certificate
        if ca_path_value is None:
            ca_path_value = env.get("OTEL_EXPORTER_OTLP_CERTIFICATE")
        ca_path = Path(ca_path_value) if ca_path_value is not None else None
        if ca_path is not None and parsed_endpoint.scheme.lower() != "https":
            raise ValueError("OTEL_EXPORTER_OTLP_CERTIFICATE requires an https endpoint")
        ca_data = _read_certificate(ca_path, "Invalid OTEL_EXPORTER_OTLP_CERTIFICATE") if ca_path else None

        certificate_path = Path(self.client_certificate) if self.client_certificate is not None else None
        private_key_path = Path(self.client_private_key) if self.client_private_key is not None else None
        if (certificate_path is None) != (private_key_path is None):
            raise ValueError("Client certificate and private key must be configured together")
        if certificate_path is not None:
            if parsed_endpoint.scheme.lower() != "https":
                raise ValueError("Client TLS requires an https endpoint")
            certificate_data = _read_certificate(certificate_path, "Invalid OTLP client certificate")
            private_key_data = _read_file(private_key_path, "Invalid OTLP client private key")
            try:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.load_cert_chain(str(certificate_path), str(private_key_path))
            except (OSError, ssl.SSLError, ValueError) as error:
                raise ValueError("Invalid OTLP client certificate or private key") from error
        else:
            certificate_data = None
            private_key_data = None

        return ResolvedSdkConfig(
            endpoint=endpoint,
            protocol=protocol,
            service_name=service_name,
            metric_interval=metric_interval,
            export_timeout=export_timeout,
            headers=MappingProxyType(normalized_headers),
            trusted_certificate=ca_path,
            trusted_certificate_data=ca_data,
            client_certificate=certificate_path,
            client_certificate_data=certificate_data,
            client_private_key=private_key_path,
            client_private_key_data=private_key_data,
            enabled=bool(self.enabled),
            span_batch=self.span_batch,
            log_batch=self.log_batch,
        )


@dataclass(frozen=True, slots=True)
class ResolvedSdkConfig:
    endpoint: str
    protocol: str
    service_name: str
    metric_interval: float
    export_timeout: float
    headers: Mapping[str, str]
    trusted_certificate: Path | None
    trusted_certificate_data: bytes | None
    client_certificate: Path | None
    client_certificate_data: bytes | None
    client_private_key: Path | None
    client_private_key_data: bytes | None
    enabled: bool
    span_batch: BatchConfig
    log_batch: BatchConfig


def _select(explicit: str | None, environment: str | None, fallback: str) -> str:
    if explicit is not None:
        return explicit
    if environment is not None and environment.strip():
        return environment
    return fallback


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_seconds(value: object, name: str, *, allow_zero: bool, min_millisecond: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number of seconds")
    try:
        seconds = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} is outside the supported range") from error
    if not math.isfinite(seconds) or seconds < 0 or (seconds == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be finite and {qualifier}")
    if min_millisecond and 0 < seconds < 0.001:
        raise ValueError(f"{name} must be at least one millisecond")
    return seconds


def _milliseconds(raw: str | None, fallback: int) -> int:
    if raw is None or not raw.strip():
        return fallback
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError("Invalid OTEL duration environment variable") from error
    if value < 0:
        raise ValueError("Invalid OTEL duration environment variable")
    return value


def _parse_headers(raw: str | None) -> dict[str, str]:
    if raw is None or not raw.strip():
        return {}
    headers: dict[str, str] = {}
    try:
        for pair in raw.split(","):
            name, separator, value = pair.partition("=")
            if not separator or not name:
                raise ValueError
            if name in headers:
                raise ValueError
            headers[name] = unquote_plus(value, encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("Invalid OTEL_EXPORTER_OTLP_HEADERS") from error
    return headers


def _normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    try:
        for name, value in headers.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise ValueError
            normalized_name = name.lower()
            if not _HEADER_NAME.fullmatch(normalized_name):
                raise ValueError
            if any(ord(char) == 0x7F or ord(char) < 0x20 and char != "\t" for char in value):
                raise ValueError
            if normalized_name in normalized:
                raise ValueError
            normalized[normalized_name] = value
    except (AttributeError, ValueError) as error:
        raise ValueError("Invalid OTLP request headers") from error
    return normalized


def _read_file(path: Path, message: str) -> bytes:
    try:
        data = path.read_bytes()
        if not data:
            raise OSError
        return data
    except OSError as error:
        raise ValueError(message) from error


def _read_certificate(path: Path, message: str) -> bytes:
    data = _read_file(path, message)
    try:
        ssl.create_default_context(cafile=str(path))
    except (OSError, ssl.SSLError, ValueError) as error:
        raise ValueError(message) from error
    return data
