# Service Onboarding Checklist (Python)

1. 配置 OTel TracerProvider，并指定 `OTEL_SERVICE_NAME`。
2. 入站 FastAPI 中间件提取上游 trace 并创建 server span。
3. 在响应头返回 `X-Trace-Id`，方便调用方排查。
4. 出站 HTTP 客户端启用 OTel 注入（traceparent）。
5. 统一使用 `obslogpy.log_json(...)` 输出结构化日志字段。
6. 只在关键业务流程补 3-5 个语义 span，避免过度埋点。
7. 在 SigNoz 验证跨服务父子 span 与 trace_id 检索能力。

