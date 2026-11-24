# OpenAN Observability SDK for Python: Development Guide

[简体中文](../zh/开发指南.md) · [API Reference](api-reference.md)

This guide covers package development, signal definitions, and the expected contribution format.

## 1. Prerequisites and installation

- Python 3.10 or later
- Git
- A reachable OpenTelemetry Collector when exporting telemetry

Install the package in editable mode from the repository root:

```powershell
python -m pip install -e .
```

The package uses OpenTelemetry Python for signal APIs and OTLP exporters. It supports OTLP/gRPC and OTLP/HTTP with protobuf.

## 2. Package layout

| Path | Purpose |
|---|---|
| `src/openan_observability_sdk/config.py` | Public settings and environment resolution |
| `src/openan_observability_sdk/definitions.py` | Loads and indexes signal YAML files |
| `src/openan_observability_sdk/observability.py` | Validates and records traces, metrics, and logs |
| `src/openan_observability_sdk/sdk.py` | Creates and manages OpenTelemetry providers and OTLP exporters |
| `src/openan_observability_sdk/config/` | Packaged A2A-T, workflow, and agent definitions |
| `examples/` | Runnable workflow/agent and HTTP propagation examples |
| `doc/` | English and Chinese API and development documentation |

The supported public API is exported from `openan_observability_sdk`. Names beginning with an underscore are implementation details.

## 3. Initialize and close the SDK

Create the SDK once for the application process. Close it during application shutdown. Use `flush()` before a short-lived process exits so queued telemetry has a chance to export.

```python
from openan_observability_sdk import ObservabilitySdk, SdkConfig

with ObservabilitySdk.start(SdkConfig(service_name="diagnosis-agent")) as sdk:
    observe = sdk.observe()
    # Record application signals.
    if not sdk.flush(timeout=10):
        raise RuntimeError("OTLP flush failed or timed out")
```

Call `set_enabled(False)` to stop creating new SDK signals at runtime. Spans created before the switch can still be ended and exported.

## 4. Record signal categories

### 4.1 A2A / A2A-T

A2A request spans use `CLIENT` or `SERVER`. A2A-T message spans and message logs require `extension.name`, which selects the extension attributes:

A2A trace names follow the Java SDK's JSON-RPC method names, including `SendMessage`, `SendStreamingMessage`, and `GetTask`. `GetExtendedAgentCard` and `SendPushNotification` are client-only; `SendStreamingMessage-negotiation` is server-only. Stream-event and stream-error spans retain the supplied parent, or the current span, as a Span Link.

| `extension.name` | Example attributes |
|---|---|
| `task-t` | `task.type`, `task.object` |
| `negotiation-t` | `negotiation.id`, `negotiation.round`, `negotiation.result`, `negotiation.performative` |
| `notification-t` | `notification.topic`, `push.notification.url` |
| `authorization-t` | `authorization.stage`, `authorization.policy.operation.type` |

Task-T spans, message logs, `task.duration`, and `task.count` require a non-blank `task.type`.

### 4.2 Workflow

Workflow spans use `a2at.workflow`; step spans use the names declared by `workflow.yaml`. Required attributes and metric dimensions are defined in that YAML file.

### 4.3 Agent

Agent substep spans use names declared by `agent.yaml`. The `agent.skills` attribute is a list of non-blank strings. Token usage metrics require the agent name, model, and token type dimensions.

## 5. Trace context, errors, and runtime updates

Use `TraceContextPropagation.inject` before an outbound call and `extract_to_current` before creating a receiving span. Use `with span.make_current():` when nested work needs to inherit the current span.

Calling `span.error(exception)` records the exception and sets the span status to error. An exception leaving the `ObservationSpan` context manager also marks the span as failed.

`update_exporters(config)` replaces the exporters after flushing existing data. Service name, metric interval, and span/log batch settings cannot change on an existing provider; create a new SDK instance to change them.

The `BatchConfig.export_timeout` option is accepted, but OpenTelemetry Python's batch processors currently do not forward it to exporters. The exporter timeout is configured with `SdkConfig.export_timeout`.

## 6. Signal definitions and compatibility

The SDK packages three YAML definitions:

| File | Coverage |
|---|---|
| `src/openan_observability_sdk/config/a2at.yaml` | A2A and the four A2A-T extensions |
| `src/openan_observability_sdk/config/workflow.yaml` | Workflow and step |
| `src/openan_observability_sdk/config/agent.yaml` | Agent, substep, and token usage |

Each file defines its version, attributes and OpenTelemetry names, trace/log allowlists, required fields, metrics, and metric dimensions. When a signal definition changes:

1. Keep the definition aligned with the Java SDK.
2. Increment the affected YAML version.
3. Update relevant examples and both language versions of the API reference and development guide.
4. Treat removal or tightening of a published signal, attribute, type, required field, or metric dimension as a breaking change.

The current A2A definition version is `1.0.0`.

## 7. Commits and pull requests

- Use Conventional Commit messages, such as `feat(sdk): add exporter option` or `fix(validation): reject invalid metric dimension`.
- Sign every commit off with DCO using `git commit -s`.
- Open pull requests from a personal fork and target `project-openan/observability-sdk-python`.
- Describe the main changes, compatibility impact, and verification results in the pull request.
