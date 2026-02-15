# observability-log-py

Python 二方观测日志库，提供：

- 统一结构化日志字段（`application_name/method_name/detail/time/level/trace_id/span_id`）
- 基于 FastAPI 的 access log + `X-Trace-Id` 响应头中间件
- 可选响应体预览（限长+脱敏）
- OTel 初始化与日志相关配置
- Span 常用 helper（批量 attributes、链式错误处理）

## 安装

```bash
pip install observability-log-py
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
)

logger = configure_logging("mail-mvp")
init_otel("mail-mvp", logger)

app = FastAPI()
add_fastapi_observability(app, logger)  # 默认读取 OBS_HTTP_BODY_PREVIEW_* 等环境变量

log_json(logger, "Email.Generate", "request accepted", fields={"user_id": 42})

def mark_error(span, err):
    SpanOps(span).error(err, error_code="DEMO_ERROR")
```

## 目标

- 跨 Python 服务统一日志格式
- 自动关联 OTel trace/span
- 在 SigNoz/ClickHouse 中按 `trace_id` 与 `method_name` 快速检索

