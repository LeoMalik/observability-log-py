# Changelog

## [0.3.0] - 2026-03-10

### Fixed

- **TraceAccessLogMiddleware rewritten as pure ASGI middleware** — no longer inherits from
  Starlette `BaseHTTPMiddleware`. This fixes `RuntimeError: Unexpected message received:
  http.request` when stacking multiple `BaseHTTPMiddleware` layers (e.g. Langfuse + access log)
  with `StreamingResponse` under real ASGI servers (uvicorn / hypercorn).

- **LangfuseTraceMiddleware: removed `_restore_request_body` call** — Starlette's
  `_CachedRequest` already caches the body from `request.body()` and replays it to downstream
  middleware automatically; the manual restore caused `http.request` in the consumed state.

- **`_restore_request_body` helper: delegate to original receive after body consumed** — second
  call now awaits the original `receive()` (returns `http.disconnect`) instead of returning a
  second `http.request`.

- **TraceAccessLogMiddleware: cap streaming response body preview and keep span attrs intact** —
  response preview now samples only a bounded prefix (`max_bytes + 1`) while separately tracking
  full response size, avoiding unbounded memory growth on streaming responses. Also ensures
  `http.server_duration_ms` and body preview attributes are set on the active request span.

### Changed

- `TraceAccessLogMiddleware` now intercepts ASGI `receive`/`send` directly instead of using
  Starlette's `Request`/`Response` objects, enabling correct body buffering and header injection
  without `BaseHTTPMiddleware` side effects.
- Request body buffering uses a replay-receive pattern that faithfully replays the body once,
  then delegates to the original receive for `http.disconnect`.
