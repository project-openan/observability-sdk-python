# OpenAN Python 可观测性 SDK：API 参考

[English](../en/api-reference.md) · [开发指南](开发指南.md)

本文介绍当前开发版本的公开 API。需要 Python 3.10 或更高版本。

## 包与入口

公开类从 `openan_observability_sdk` 导入。通常在应用启动时创建一个 `ObservabilitySdk`，并在应用停止时关闭。

```python
from openan_observability_sdk import ObservabilitySdk, SdkConfig

with ObservabilitySdk.start(SdkConfig(service_name="diagnosis-agent")) as sdk:
    observe = sdk.observe()
    # 使用 observe 记录 Trace、Metric 和 Log。
```

## `ObservabilitySdk`

### `start(config=None)`

创建 Trace、Metric、Log 的 Exporter 及 OpenTelemetry Provider。未传入配置时使用 `SdkConfig()`，并读取支持的环境变量。

### `observe()`

返回此 SDK 实例关联的信号记录接口。

### `flush(timeout=10.0)`

等待待导出的 Trace、Metric 和 Log。超时时间单位为秒。只有三个 Provider 均在超时内完成，且自上次 `flush` 后未观测到 Exporter 失败时，才返回 `True`。

### `close()`

关闭 OpenTelemetry Provider 和 Exporter。重复调用安全。建议使用上下文管理器，确保代码块退出时完成关闭。

### 运行时控制

`set_enabled(bool)` 控制 SDK 是否创建新的观测数据。关闭采集后，已创建的 Span 仍可结束并导出。

`update_exporters(config)` 替换 OTLP Exporter，并返回是否成功。Provider 创建后，服务名、Metric 导出间隔以及 Span/Log 批处理参数不能更改；需要修改时请创建新 SDK 实例。

## `SdkConfig` 与 `BatchConfig`

显式设置优先于支持的 OpenTelemetry 环境变量。

| 字段 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `endpoint` | `OTEL_EXPORTER_OTLP_ENDPOINT` | gRPC：`http://localhost:4317`；HTTP/protobuf：`http://localhost:4318` | Collector 基础地址 |
| `protocol` | `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` 或 `http/protobuf` |
| `service_name` | `OTEL_SERVICE_NAME` | `unknown_service` | OpenTelemetry `service.name` |
| `metric_interval` | `OTEL_METRIC_EXPORT_INTERVAL` | 60 秒 | 字段单位为秒，环境变量单位为毫秒 |
| `headers` | `OTEL_EXPORTER_OTLP_HEADERS` | 无 | 显式传入的映射会替代环境变量请求头 |
| `trusted_certificate` | `OTEL_EXPORTER_OTLP_CERTIFICATE` | 系统信任库 | PEM CA 文件；要求使用 `https` |
| `client_certificate` + `client_private_key` | — | 无 | 双向 TLS 的 PEM 客户端证书和私钥；必须同时设置 |
| `export_timeout` | `OTEL_EXPORTER_OTLP_TIMEOUT` | 10 秒 | 字段单位为秒，最小为 1 毫秒 |
| `enabled` | — | `True` | 初始采集状态 |
| `span_batch` | — | 队列 2048、批次 512、延迟 5 秒 | Span 批处理参数 |
| `log_batch` | — | 队列 2048、批次 512、延迟 1 秒 | Log 批处理参数 |

HTTP/protobuf 请填写 Collector 基础 URL；SDK 会补上 `/v1/traces`、`/v1/metrics` 和 `/v1/logs`。环境变量中的 Header 值需要百分号编码；传入 `SdkConfig.headers` 的值无需编码。

`BatchConfig.export_timeout` 为 API 一致性而保留，但当前 OpenTelemetry Python 批处理器不会将其传给 Exporter。OTLP Exporter 超时请使用 `SdkConfig.export_timeout`。

## `Observability`

`sdk.observe()` 返回的对象用于记录 Trace、Metric 和 Log。属性名可使用 YAML 中的短名称或对应的 OpenTelemetry 名称；同一个属性不能同时传入两种形式。

无效信号会被丢弃，诊断日志不会包含业务属性值。

### `trace(name, kind, attributes=None, parent=None)`

启动并返回 `ObservationSpan`。Workflow 和 Agent Span 使用 `SignalKind.INTERNAL`；A2A Span 使用 `CLIENT` 或 `SERVER`。可选的 parent 用于设置 Span 父级。A2A stream-event 和 stream-error Span 会将传入的 parent 或当前 Span 作为 Span Link。

```python
from openan_observability_sdk import ObservabilitySdk, SignalKind

with ObservabilitySdk.start() as sdk:
    observe = sdk.observe()
    attrs = {"workflow.id": "wf-001", "workflow.type": "diagnosis", "workflow.status": "running"}
    with observe.trace("a2at.workflow", SignalKind.INTERNAL, attrs) as span:
        span.ok()
```

### `metric(id, value, attributes=None)`

记录 Metric 样本。值必须是有限且非负的数值；Counter 值必须是整数，Histogram 可使用整数或浮点数。Metric 标识可使用 YAML 短名称或 OpenTelemetry 名称。

### `log(name, severity, body, attributes=None, *, span=None, context=None)`

记录结构化 Log。可将 Log 关联到 `ObservationSpan` 或 OpenTelemetry `Context`；两者都未传入时使用当前上下文。严重级别为 `TRACE`、`DEBUG`、`INFO`、`WARN`、`ERROR` 和 `FATAL`。

### `log_record(severity, body, attributes=None, *, event_name=None, timestamp=None, exception=None, context=None)`

记录不使用预定义信号 Schema 的通用 OpenTelemetry Log。`timestamp` 是从 Unix epoch 起算的纳秒整数。属性支持字符串、布尔值、有限数值和字符串数组；导出前会清理字符串和异常信息。

A2A Trace 名称使用 JSON-RPC 方法名，例如 `SendMessage`、`SendStreamingMessage`、`GetTask`、`CancelTask`、`ListTasks` 和推送通知配置方法。`GetExtendedAgentCard` 与 `SendPushNotification` 仅支持 Client；`SendStreamingMessage-negotiation` 仅支持 Server。流事件可记录任务状态和 Artifact 属性，消息 Span 可记录传输方式、协议版本、模型及 Token 用量。

## `ObservationSpan`

`ObservationSpan` 支持上下文管理器。退出代码块时会结束 Span；重复调用 `close()` 或 `end()` 安全。

| 方法或属性 | 说明 |
|---|---|
| `valid` | 信号是否通过校验并创建了 Span |
| `context` / `span_context` | OpenTelemetry 上下文和 Span 上下文 |
| `attributes(mapping)` | 添加该 Trace 定义允许的属性 |
| `ok()` | 将 Span 状态设为 OK |
| `error(exception=None)` | 可选地记录异常，并将状态设为 ERROR |
| `make_current()` | 使该 Span 成为当前上下文的上下文管理器 |
| `close()` / `end()` | 结束 Span |

## `TraceContextPropagation`

使用 `inject(carrier)` 和 `extract_to_current(carrier)`，通过 HTTP Header 等调用方持有的文本载体传递 W3C Trace Context。

```python
headers: dict[str, str] = {}
with observe.trace("SendMessage", SignalKind.CLIENT, {"operation.name": "SendMessage"}) as client:
    with client.make_current():
        TraceContextPropagation.inject(headers)

with TraceContextPropagation.extract_to_current(headers):
    with observe.trace("SendMessage", SignalKind.SERVER, {"operation.name": "SendMessage"}) as server:
        server.ok()
```

## 信号名称速查

完整名称、属性、类型和 Metric 维度由随包发布的 YAML 文件定义。

| 类别 | Traces | Metrics | Logs |
|---|---|---|---|
| A2A / A2A-T | `SendMessage`、`SendStreamingMessage`、流事件/错误/协商、`GetTask`、`CancelTask`、`ListTasks`、推送通知方法 | A2A 操作与流式指标；八项 A2A-T 指标 | `gen_ai.agent.a2at.message` |
| Workflow | Workflow 和 Step Span | Workflow 时长/计数和 Step 时长 | Workflow 和 Step 事件 |
| Agent | Agent Substep Span | Substep 时长和 Token 用量 | Agent 生命周期和 Substep 事件 |

信号校验和 YAML 维护方式见[开发指南](开发指南.md)。
