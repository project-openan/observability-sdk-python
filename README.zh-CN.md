# OpenAN Python 可观测性 SDK

[English](README.md) | 简体中文

用于记录 A2A / A2A-T、Workflow 和 Agent 可观测性信号的 Python SDK。SDK 会校验信号名称和属性，将其转换为 OpenTelemetry 数据，并通过 OTLP 导出 Traces、Metrics 和 Logs。

```text
应用代码 ──> OpenAN SDK ──> OpenTelemetry SDK ──OTLP──> Collector
                       ├── A2A / A2A-T
                       ├── Workflow
                       └── Agent / Substep
```

## 信号范围

| 类别 | Traces | Metrics | Logs |
|---|---|---|---|
| A2A / A2A-T | A2A 消息、任务和流式调用；Task-T、Negotiation-T、Notification-T、Authorization-T | A2A 操作及流式指标；八项 A2A-T 指标 | A2A-T 消息事件 |
| Workflow | Workflow 和 Step Span | Workflow 时长/计数和 Step 时长 | Workflow 和 Step 事件 |
| Agent | Agent Substep Span | Substep 时长和 Token 使用量 | Agent 生命周期和 Substep 事件 |

信号定义随包发布在 [`src/openan_observability_sdk/config/`](src/openan_observability_sdk/config/) 中。当前 A2A 配置版本为 `1.0.0`，与本次移植参考的 Java SDK 一致。

完整 API 说明和信号维护流程见[API 参考](doc/zh/API参考.md)和[开发指南](doc/zh/开发指南.md)。

## 安装

需要 Python 3.10 或更高版本：

```powershell
python -m pip install .
```

支持 OTLP/gRPC 和 OTLP/HTTP protobuf。导出前请启动 Collector：gRPC 默认端口为 `4317`，HTTP/protobuf 默认端口为 `4318`。

## 快速开始

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
                "诊断完成",
                task,
                span=span,
            )
            observe.metric("task.duration", 42_000, {"task.type": "diagnosis"})
            observe.metric("task.count", 1, {"task.type": "diagnosis"})
            span.ok()

    if not sdk.flush(timeout=10):
        raise RuntimeError("OTLP flush failed or timed out")
```

`ObservationSpan` 支持上下文管理器，退出时会结束 Span。需要子任务继承当前 Span 时，用 `with span.make_current():` 建立 OpenTelemetry 上下文。调用 `span.error(exception)` 会记录异常并标记失败；异常离开 Span 上下文管理器时也会自动标记失败。

## Workflow 与 Agent

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
        observe.metric("workflow.duration", 42.0, {
            "workflow.type": "fault-diagnosis",
            "workflow.status": "completed",
        })
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

## Trace Context 传播

`TraceContextPropagation` 可用于 HTTP Header 等调用方持有的文本载体：

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

## OTLP 配置

`SdkConfig` 中显式设置的值优先于 OpenTelemetry 环境变量。

| `SdkConfig` 字段 | 环境变量 | 默认值 |
|---|---|---|
| `endpoint` | `OTEL_EXPORTER_OTLP_ENDPOINT` | gRPC：`http://localhost:4317`；HTTP/protobuf：`http://localhost:4318` |
| `protocol` | `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc`，也支持 `http/protobuf` |
| `service_name` | `OTEL_SERVICE_NAME` | `unknown_service` |
| `metric_interval`（秒） | `OTEL_METRIC_EXPORT_INTERVAL`（毫秒） | 60 秒 |
| `headers` | `OTEL_EXPORTER_OTLP_HEADERS` | 无请求 Header |
| `trusted_certificate` | `OTEL_EXPORTER_OTLP_CERTIFICATE` | 系统信任库 |
| `export_timeout`（秒） | `OTEL_EXPORTER_OTLP_TIMEOUT`（毫秒） | 10 秒 |
| `enabled` | — | `True` |
| `span_batch` | — | 队列 `2048`、批次 `512`、延迟 `5s` |
| `log_batch` | — | 队列 `2048`、批次 `512`、延迟 `1s` |

HTTP/protobuf 请填写 Collector 基础 URL；SDK 会补上 `/v1/traces`、`/v1/metrics` 和 `/v1/logs`。配置双向 TLS 时，需要同时设置 `client_certificate` 和 `client_private_key`。自定义 CA 或客户端证书要求使用 `https`。环境变量中的 Header 值需要进行百分号编码；直接传给 `SdkConfig.headers` 的值无需编码。

为兼容 Java API，`span_batch` 和 `log_batch` 接受 `export_timeout`。当前 OpenTelemetry Python 批处理器不会把该值传给 Exporter；请用 `SdkConfig.export_timeout` 设置 OTLP Exporter 超时。

运行中调用 `sdk.set_enabled(False)` 可以暂停 SDK 新建记录；已有 Span 仍可结束并导出。`sdk.update_exporters(config)` 可替换 OTLP Exporter，但服务名、Metric 导出间隔和批处理参数必须与当前 Provider 一致。

## 开发

以可编辑模式安装：

```powershell
python -m pip install -e .
```

Commit 使用 Conventional Commits 格式，并包含 DCO sign-off。更新信号定义时，需要递增对应 YAML 版本，并同步更新中英文 API 参考和开发指南。

## 许可证

Apache License 2.0。详见 [LICENSE](LICENSE) 和 [NOTICE](NOTICE)。
