# observability-log-py

Python 二方观测日志库，提供：

- 统一结构化日志字段（`application_name/method_name/detail/time/level/trace_id/span_id`）
- 基于 FastAPI 的 access log + `X-Trace-Id` 响应头中间件
- 可选响应体预览（限长+脱敏）

## 安装

```bash
pip install observability-log-py
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

## 目标

- 跨 Python 服务统一日志格式
- 自动关联 OTel trace/span
- 在 SigNoz/ClickHouse 中按 `trace_id` 与 `method_name` 快速检索

