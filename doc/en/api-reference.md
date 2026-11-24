# OpenAN Observability SDK for Python: API Reference

[简体中文](../zh/API参考.md) · [Development Guide](development-guide.md)

This reference describes the public API in the current development build. Python 3.10 or later is required.

## Package and entry point

Import public classes from `openan_observability_sdk`. Create one `ObservabilitySdk` when the application starts and close it when the application stops.

```python
from openan_observability_sdk import ObservabilitySdk, SdkConfig

with ObservabilitySdk.start(SdkConfig(service_name="diagnosis-agent")) as sdk:
    observe = sdk.observe()
    # Record traces, metrics, and logs with observe.
```

## `ObservabilitySdk`

### `start(config=None)`

Creates trace, metric, and log exporters and their OpenTelemetry providers. When omitted, `SdkConfig()` reads supported defaults from the environment.

### `observe()`

Returns the signal-recording facade associated with this SDK instance.

### `flush(timeout=10.0)`

Waits for pending traces, metrics, and logs. The timeout is in seconds. It returns `True` only when all providers finish in time and no exporter failure has been observed since the preceding `flush`.

### `close()`

Shuts down the OpenTelemetry providers and exporters. Repeated calls are safe. Use the SDK as a context manager so it closes when the block exits.

### Runtime controls

`set_enabled(bool)` controls creation of new SDK telemetry. Existing spans can still be ended after disabling.

`update_exporters(config)` replaces the OTLP exporters and returns whether the update succeeded. Service name, metric interval, and span/log batch settings are fixed when providers are created. Start a new SDK instance to change those settings.

## `SdkConfig` and `BatchConfig`

Explicit values take precedence over supported OpenTelemetry environment variables.

| Field | Environment variable | Default | Notes |
|---|---|---|---|
| `endpoint` | `OTEL_EXPORTER_OTLP_ENDPOINT` | gRPC: `http://localhost:4317`; HTTP/protobuf: `http://localhost:4318` | Collector base address |
| `protocol` | `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` or `http/protobuf` |
| `service_name` | `OTEL_SERVICE_NAME` | `unknown_service` | OpenTelemetry `service.name` |
| `metric_interval` | `OTEL_METRIC_EXPORT_INTERVAL` | 60 seconds | Field is seconds; environment value is milliseconds |
| `headers` | `OTEL_EXPORTER_OTLP_HEADERS` | none | Explicit mapping replaces environment headers |
| `trusted_certificate` | `OTEL_EXPORTER_OTLP_CERTIFICATE` | system trust store | PEM CA file; requires `https` |
| `client_certificate` + `client_private_key` | — | none | PEM client identity for mutual TLS; both are required |
| `export_timeout` | `OTEL_EXPORTER_OTLP_TIMEOUT` | 10 seconds | Field is seconds; must be at least one millisecond |
| `enabled` | — | `True` | Initial recording state |
| `span_batch` | — | queue 2048, batch 512, delay 5s | Span batch processor settings |
| `log_batch` | — | queue 2048, batch 512, delay 1s | Log batch processor settings |

For HTTP/protobuf, provide the Collector base URL; the SDK appends `/v1/traces`, `/v1/metrics`, and `/v1/logs`. Header values in the environment variable must be percent-encoded. Values in `SdkConfig.headers` are raw.

`BatchConfig.export_timeout` is accepted for API consistency, but the current OpenTelemetry Python batch processors do not pass it to exporters. Use `SdkConfig.export_timeout` to set the OTLP exporter timeout.

## `Observability`

The object returned by `sdk.observe()` records traces, metrics, and logs. Attribute names may use either a short YAML name or its declared OpenTelemetry name. Do not provide both forms for the same attribute.

Invalid signals are dropped and produce a diagnostic warning without business attribute values.

### `trace(name, kind, attributes=None, parent=None)`

Starts and returns an `ObservationSpan`. Workflow and agent spans use `SignalKind.INTERNAL`; A2A spans use `CLIENT` or `SERVER`. The optional parent sets the span parent. A2A stream-event and stream-error spans use the supplied parent, or the current span, as a Span Link.

```python
from openan_observability_sdk import ObservabilitySdk, SignalKind

with ObservabilitySdk.start() as sdk:
    observe = sdk.observe()
    attrs = {"workflow.id": "wf-001", "workflow.type": "diagnosis", "workflow.status": "running"}
    with observe.trace("a2at.workflow", SignalKind.INTERNAL, attrs) as span:
        span.ok()
```

### `metric(id, value, attributes=None)`

Records a metric sample. Values must be finite and non-negative. Counter values must be integers; histogram values may be integers or floats. The identifier may be the short YAML name or the OpenTelemetry metric name.

### `log(name, severity, body, attributes=None, *, span=None, context=None)`

Emits a structured log. A log can be associated with an `ObservationSpan` or an OpenTelemetry `Context`; when neither is supplied, the current context is used. Severity values are `TRACE`, `DEBUG`, `INFO`, `WARN`, `ERROR`, and `FATAL`.

### `log_record(severity, body, attributes=None, *, event_name=None, timestamp=None, exception=None, context=None)`

Emits a general OpenTelemetry log without a configured signal schema. `timestamp` is an integer number of nanoseconds since the Unix epoch. Attributes may contain strings, booleans, finite numbers, or string arrays. String values and exception details are sanitized before export.

A2A trace names use JSON-RPC method names such as `SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, `ListTasks`, and push-notification configuration methods. `GetExtendedAgentCard` and `SendPushNotification` are client-only; `SendStreamingMessage-negotiation` is server-only. Stream events may include task status and artifact attributes, and message spans may include transport, protocol version, model, and token usage.

## `ObservationSpan`

`ObservationSpan` is a context manager. Exiting it ends the span, and repeated `close()` or `end()` calls are safe.

| Method or property | Description |
|---|---|
| `valid` | Whether the signal passed validation and created a span |
| `context` / `span_context` | OpenTelemetry context and span context |
| `attributes(mapping)` | Adds attributes allowed by the trace definition |
| `ok()` | Sets span status to OK |
| `error(exception=None)` | Records an optional exception and sets status to ERROR |
| `make_current()` | Context manager that makes the span current |
| `close()` / `end()` | Ends the span |

## `TraceContextPropagation`

Use `inject(carrier)` and `extract_to_current(carrier)` to carry W3C Trace Context through caller-owned text carriers such as HTTP headers.

```python
headers: dict[str, str] = {}
with observe.trace("SendMessage", SignalKind.CLIENT, {"operation.name": "SendMessage"}) as client:
    with client.make_current():
        TraceContextPropagation.inject(headers)

with TraceContextPropagation.extract_to_current(headers):
    with observe.trace("SendMessage", SignalKind.SERVER, {"operation.name": "SendMessage"}) as server:
        server.ok()
```

## Signal name quick reference

The complete names, attributes, types, and metric dimensions are defined in the packaged YAML files.

| Category | Traces | Metrics | Logs |
|---|---|---|---|
| A2A / A2A-T | `SendMessage`, `SendStreamingMessage`, stream events/errors/negotiations, `GetTask`, `CancelTask`, `ListTasks`, push-notification methods | A2A operation and stream metrics; eight A2A-T metrics | `gen_ai.agent.a2at.message` |
| Workflow | Workflow and step spans | Workflow duration/count and step duration | Workflow and step events |
| Agent | Agent substep spans | Substep duration and token usage | Agent lifecycle and substep events |

See the [Development Guide](development-guide.md) for signal validation and YAML maintenance.
