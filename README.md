# observability-log-py

Python 二方观测日志库，提供：

- 统一结构化日志字段（`application_name/method_name/detail/time/level/trace_id/span_id`）
- 基于 FastAPI 的 access log + `X-Trace-Id` 响应头中间件
- 可选响应体预览（限长+脱敏）
- Langfuse 追踪封装（HTTP span + LiteLLM generation + 统一 open span 入口）
- OTel 初始化与日志相关配置
- Span 常用 helper（批量 attributes、链式错误处理）
- 低侵入函数耗时埋点装饰器（`@traced_step`，支持 sync/async）

## 安装

```bash
pip install observability-log-py
```

启用 Langfuse 封装时：

```bash
pip install "observability-log-py[langfuse]"
```

## 使用示例

```python
from fastapi import FastAPI
from obslogpy import (
    SpanOps,
    add_fastapi_observability,
    configure_logging,
    init_otel,
    log_json,
    traced_step,
)

logger = configure_logging("mail-mvp")
init_otel("mail-mvp", logger)

app = FastAPI()
add_fastapi_observability(app, logger)  # 默认读取 OBS_HTTP_BODY_PREVIEW_* 等环境变量

log_json(logger, "Email.Generate", "request accepted", fields={"user_id": 42})

def mark_error(span, err):
    SpanOps(span).error(err, error_code="DEMO_ERROR")


@traced_step("step.send_email", attrs={"biz.module": "email"})
def send_email(recipient: str) -> str:
    return f"sent to {recipient}"
```

## 低侵入函数耗时埋点（traced_step）

```python
from obslogpy import traced_step


@traced_step("pipeline.parse_input", attrs={"biz.scene": "upload"})
def parse_input(payload: dict) -> dict:
    return payload


@traced_step("pipeline.score")
async def score(payload: dict) -> dict:
    return payload
```

说明：
- 每次调用会创建一个 span（span 名默认为 `模块名.函数名`，也可手动传 `name`）。
- 自动写入 `duration_ms` 到 span attributes。
- 正常返回自动标记 `StatusCode.OK`；异常自动 `record_exception` + `StatusCode.ERROR`。
- 建议只给关键业务步骤加 3-5 个装饰器，避免过度埋点。

## Langfuse 最小侵入接入

```python
from fastapi import FastAPI
from obslogpy.langfuse.fastapi import add_langfuse_tracing
from obslogpy.langfuse.litellm import (
    build_trace_headers,
    observed_instrumented_acompletion,
)

app = FastAPI()
add_langfuse_tracing(app)  # 从环境变量自动读取配置并决定是否启用

# 业务代码只调封装函数，不直接调 start_as_current_span/set_attribute 等 tracing 细节
headers = build_trace_headers(user_id="u-1", session_id="s-1")
resp = await observed_instrumented_acompletion(
    tracer_name="mail-mvp/llm/email-write",
    span_name="EmailWriteClient.custom_email_acompletion",
    generation_name="EmailWriteClient.generate_body_custom",
    model="litellm_proxy/google/gemini-2.5-pro",
    base_url="http://127.0.0.1",
    api_key="***",
    messages=[{"role": "user", "content": "hello"}],
    user_id="u-1",
    session_id="s-1",
    request_payload={"model": "litellm_proxy/google/gemini-2.5-pro"},
    extra_headers=headers,
)
```

环境变量（与现有项目兼容）：

```env
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-xxx
LANGFUSE_SECRET_KEY=sk-xxx
LANGFUSE_TRACING_ENABLED=true
LANGFUSE_FLUSH_AT_REQUEST_END=true
```

## Langfuse Open 手册

### 1) 本地自包含启动（推荐）

在业务仓库根目录执行：

```bash
docker compose -f docker-compose.langfuse.yml up -d
```

打开 UI：`http://localhost:3000`

### 2) 业务服务接入本地 Langfuse

- 容器内运行服务：`LANGFUSE_HOST=http://host.docker.internal:3000`
- 本机直接运行服务：`LANGFUSE_HOST=http://localhost:3000`

### 3) 117 profile

```bash
docker compose -f docker-compose.langfuse.117.yml up -d
```

UI 示例：`http://192.168.10.117:3001`

### 4) aws-staging profile

```bash
docker compose -f docker-compose.langfuse.aws-staging.yml up -d
```

通过 `NEXTAUTH_URL` 对应域名访问 UI。

## 目标

- 跨 Python 服务统一日志格式
- 自动关联 OTel trace/span
- 在 SigNoz/ClickHouse 中按 `trace_id` 与 `method_name` 快速检索
