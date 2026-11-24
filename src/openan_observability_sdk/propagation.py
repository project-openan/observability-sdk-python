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

from collections.abc import Mapping, MutableMapping
from contextlib import contextmanager
from typing import Iterator

from opentelemetry.context import Context, attach, detach
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


class TraceContextPropagation:
    """W3C Trace Context helpers for caller-owned text carriers such as HTTP headers."""

    _propagator = TraceContextTextMapPropagator()

    @classmethod
    def inject(cls, carrier: MutableMapping[str, str]) -> MutableMapping[str, str]:
        """Write the current W3C trace context into ``carrier`` and return it."""

        if carrier is None:
            raise ValueError("carrier is required")
        cls._propagator.inject(carrier)
        return carrier

    @classmethod
    @contextmanager
    def extract_to_current(cls, carrier: Mapping[str, str]) -> Iterator[Context]:
        """Extract W3C headers and make the resulting context current in a ``with`` block."""

        if carrier is None:
            raise ValueError("carrier is required")
        extracted = cls._propagator.extract(carrier)
        token = attach(extracted)
        try:
            yield extracted
        finally:
            detach(token)
