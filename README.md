# OpenAN Observability SDK for Python

English | [简体中文](README.zh-CN.md)

A Python SDK for recording A2A / A2A-T, workflow, and agent observability signals. It validates signal names and attributes, maps them to OpenTelemetry data, and exports traces, metrics, and logs to an OpenTelemetry Collector over OTLP.

```text
Application code ──> OpenAN SDK ──> OpenTelemetry SDK ──OTLP──> Collector
                           ├── A2A / A2A-T
                           ├── Workflow
                           └── Agent / Substep
```

## Signal coverage

| Category | Traces | Metrics | Logs |
|---|---|---|---|
| A2A / A2A-T | A2A messages, tasks, and streaming calls; Task-T, Negotiation-T, Notification-T, and Authorization-T | A2A operation and streaming metrics; eight A2A-T metrics | A2A-T message events |
| Workflow | Workflow and step spans | Workflow duration/count and step duration | Workflow and step events |
| Agent | Agent substep spans | Substep duration and token usage | Agent lifecycle and substep events |

Signal definitions are bundled in [`src/openan_observability_sdk/config/`](src/openan_observability_sdk/config/). The current A2A configuration version is `1.0.0`, matching the Java SDK source used for this port.

See the [API reference](doc/en/api-reference.md) and [development guide](doc/en/development-guide.md) for public API details and signal-definition maintenance.

## Install

Python 3.10 or later is required. Install the package and its OpenTelemetry OTLP exporters:

```powershell
python -m pip install .
```

The SDK supports OTLP/gRPC and OTLP/HTTP with protobuf. Start a Collector that accepts OTLP on port `4317` for gRPC or `4318` for HTTP/protobuf before exporting telemetry.

## Quick start

```python
from openan_observability_sdk import LogSeverity, ObservabilitySdk, SdkConfig, SignalKind

config = SdkConfig(
    service_name="diagnosis-agent",
    endpoint="http://localhost:4317",
    protocol="grpc",
)

with ObservabilitySdk.start(config) as sdk:
    observe = sdk.observe()
    task = {"extension.name": "task-t", "task.type": "diagnosis"}

    with observe.trace("SendMessage", SignalKind.CLIENT, task) as span:
        with span.make_current():
            observe.log(
                "gen_ai.agent.a2at.message",
                LogSeverity.INFO,
                "diagnosis completed",
                task,
                span=span,
            )
            observe.metric("task.duration", 42_000, {"task.type": "diagnosis"})
            observe.metric("task.count", 1, {"task.type": "diagnosis"})
            span.ok()

    if not sdk.flush(timeout=10):
        raise RuntimeError("OTLP flush failed or timed out")
```

`ObservationSpan` is a context manager and ends the span on exit. Use `span.make_current()` when nested work should inherit the span through OpenTelemetry's context. `span.error(exception)` records an exception and marks the span as failed; exceptions leaving the span context manager also mark it as failed.

## Workflow and agent signals

```python
from openan_observability_sdk import LogSeverity, ObservabilitySdk, SignalKind

with ObservabilitySdk.start() as sdk:
    observe = sdk.observe()
    workflow = {
        "workflow.id": "wf-001",
        "workflow.type": "fault-diagnosis",
        "workflow.status": "running",
    }

    with observe.trace("a2at.workflow", SignalKind.INTERNAL, workflow) as span:
        observe.metric(
            "workflow.duration",
            42.0,
            {"workflow.type": "fault-diagnosis", "workflow.status": "completed"},
        )
        observe.log("gen_ai.agent.a2at.workflow", LogSeverity.INFO, "workflow completed", {
            **workflow,
            "workflow.status": "completed",
        })
        span.ok()

    agent = {
        "gen_ai.agent.name": "diagnosis-agent",
        "substep.name": "retrieve-alarms",
        "substep.status": "active",
    }
    with observe.trace("a2at.substep.retrieve-alarms", SignalKind.INTERNAL, agent) as span:
        observe.metric("substep.duration", 50.0, {
            "gen_ai.agent.name": "diagnosis-agent",
            "substep.name": "retrieve-alarms",
        })
        observe.metric("gen_ai.client.token.usage", 120, {
            "gen_ai.agent.name": "diagnosis-agent",
            "gen_ai.request.model": "model-1",
            "gen_ai.token.type": "input",
        })
        observe.log("gen_ai.agent.a2at.substep", LogSeverity.INFO, "substep completed", agent, span=span)
        span.ok()
```

## Trace context propagation

Use `TraceContextPropagation` with caller-owned HTTP headers or another mutable text carrier:

```python
from openan_observability_sdk import ObservabilitySdk, SignalKind, TraceContextPropagation

with ObservabilitySdk.start() as sdk:
    observe = sdk.observe()
    headers: dict[str, str] = {}

    with observe.trace("SendMessage", SignalKind.CLIENT, {"operation.name": "SendMessage"}) as span:
        with span.make_current():
            TraceContextPropagation.inject(headers)

    with TraceContextPropagation.extract_to_current(headers):
        with observe.trace("SendMessage", SignalKind.SERVER, {"operation.name": "SendMessage"}) as server:
            server.ok()
```

## OTLP configuration

Explicit `SdkConfig` values take precedence over OpenTelemetry environment variables.

| `SdkConfig` field | Environment variable | Default |
|---|---|---|
| `endpoint` | `OTEL_EXPORTER_OTLP_ENDPOINT` | gRPC: `http://localhost:4317`; HTTP/protobuf: `http://localhost:4318` |
| `protocol` | `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc`; also supports `http/protobuf` |
| `service_name` | `OTEL_SERVICE_NAME` | `unknown_service` |
| `metric_interval` (seconds) | `OTEL_METRIC_EXPORT_INTERVAL` (milliseconds) | `60` seconds |
| `headers` | `OTEL_EXPORTER_OTLP_HEADERS` | no request headers |
| `trusted_certificate` | `OTEL_EXPORTER_OTLP_CERTIFICATE` | system trust store |
| `export_timeout` (seconds) | `OTEL_EXPORTER_OTLP_TIMEOUT` (milliseconds) | `10` seconds |
| `enabled` | — | `True` |
| `span_batch` | — | queue `2048`, batch `512`, delay `5s` |
| `log_batch` | — | queue `2048`, batch `512`, delay `1s` |

For HTTP/protobuf, provide the Collector base URL; the SDK appends `/v1/traces`, `/v1/metrics`, and `/v1/logs`. For mutual TLS, set both `client_certificate` and `client_private_key`. A custom CA or client certificate requires an `https` endpoint. Environment header values must be percent-encoded; values passed through `SdkConfig.headers` are raw.

`SdkConfig` batch `export_timeout` values are accepted for API parity. The current OpenTelemetry Python batch processors do not pass that value to their exporters; use `SdkConfig.export_timeout` to set the OTLP exporter timeout.

Use `sdk.set_enabled(False)` to stop new SDK records at runtime. Existing spans can still be ended and exported. `sdk.update_exporters(config)` replaces OTLP exporters only when the service name, metric interval, and batch settings match the active providers.

## Development

Install the package in editable mode:

```powershell
python -m pip install -e .
```

Commit messages follow Conventional Commits, and every commit must include a DCO sign-off. Signal-definition changes must increment the applicable YAML version and update both language versions of the API reference and development guide.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
