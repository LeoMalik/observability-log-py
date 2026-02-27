# observability-log-py

Python 二方观测日志库，提供：

- 统一结构化日志字段（`application_name/method_name/detail/time/level/trace_id/span_id`）
- 基于 FastAPI 的 access log + `X-Trace-Id` 响应头中间件
- 可选响应体预览（限长+脱敏）
- Langfuse 追踪封装（HTTP span + LiteLLM generation + 统一 open span 入口）

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
import logging
from fastapi import FastAPI
from obslogpy.fastapi import TraceAccessLogMiddleware
from obslogpy import log_json

logger = logging.getLogger("mail-mvp")
app = FastAPI()
app.add_middleware(
    TraceAccessLogMiddleware,
    logger=logger,
    enable_response_body_preview=True,
    response_body_preview_max_bytes=2048,
    response_body_preview_paths=["/api/generate-company-summary"],
)

log_json(logger, "Email.Generate", "request accepted", fields={"user_id": 42})
```

## Langfuse 最小侵入接入

```python
from fastapi import FastAPI
from obslogpy.langfuse.fastapi import add_langfuse_tracing
from obslogpy.langfuse.litellm import instrumented_acompletion

app = FastAPI()
add_langfuse_tracing(app)  # 从环境变量自动读取配置并决定是否启用

# 业务代码只调封装函数，不直接调 start_as_current_span/start_as_current_observation
resp = await instrumented_acompletion(
    name="EmailWriteClient.generate_body_custom",
    model="litellm_proxy/google/gemini-2.5-pro",
    messages=[{"role": "user", "content": "hello"}],
    base_url="http://127.0.0.1",
    api_key="***",
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
