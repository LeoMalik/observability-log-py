# Changelog

## [0.3.0] - 2026-03-06

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

### Changed

- `TraceAccessLogMiddleware` now intercepts ASGI `receive`/`send` directly instead of using
  Starlette's `Request`/`Response` objects, enabling correct body buffering and header injection
  without `BaseHTTPMiddleware` side effects.
- Request body buffering uses a replay-receive pattern that faithfully replays the body once,
  then delegates to the original receive for `http.disconnect`.
