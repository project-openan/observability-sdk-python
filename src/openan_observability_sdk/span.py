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

from contextlib import contextmanager
from threading import Lock
from typing import Any, Iterator, Mapping

from opentelemetry.context import Context, attach, detach
from opentelemetry.trace import Span, SpanContext, Status, StatusCode

from openan_observability_sdk._attributes import validate_attributes
from openan_observability_sdk.definitions import SignalCatalog, SignalDefinition


class ObservationSpan:
    """Handle for a span created by :class:`Observability`."""

    def __init__(
        self,
        span: Span | None,
        context: Context,
        definition: SignalDefinition | None,
        catalog: SignalCatalog,
    ) -> None:
        self._span = span
        self._context = context
        self._definition = definition
        self._catalog = catalog
        self._ended = False
        self._lock = Lock()

    @property
    def context(self) -> Context:
        """OpenTelemetry context containing this span, if it was accepted."""

        return self._context

    @property
    def valid(self) -> bool:
        return self._span is not None

    @property
    def span_context(self) -> SpanContext | None:
        return self._span.get_span_context() if self._span is not None else None

    def attributes(self, attributes: Mapping[str, Any]) -> None:
        """Add attributes permitted by this span's signal definition."""

        if self._span is None or self._definition is None:
            return
        try:
            converted = validate_attributes(
                attributes,
                "S",
                self._definition,
                self._catalog,
                require_fields=False,
            )
            self._span.set_attributes(converted)
        except (TypeError, ValueError):
            return

    def ok(self) -> None:
        if self._span is not None:
            self._span.set_status(Status(StatusCode.OK))

    def error(self, error: BaseException | None = None) -> None:
        if self._span is not None:
            if error is not None:
                self._span.record_exception(error)
            self._span.set_status(Status(StatusCode.ERROR))

    @contextmanager
    def make_current(self) -> Iterator[ObservationSpan]:
        """Make this span's context current for the duration of a ``with`` block."""

        token = attach(self._context)
        try:
            yield self
        finally:
            detach(token)

    def close(self) -> None:
        if self._span is None:
            return
        with self._lock:
            if self._ended:
                return
            self._ended = True
        self._span.end()

    def end(self) -> None:
        self.close()

    def __enter__(self) -> ObservationSpan:
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> None:
        if exc is not None:
            self.error(exc)
        self.close()
