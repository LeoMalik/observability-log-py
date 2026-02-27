from __future__ import annotations

import os
from dataclasses import dataclass


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw == "":
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class LangfuseSettings:
    host: str = ""
    public_key: str = ""
    secret_key: str = ""
    tracing_enabled: bool = False
    flush_at_request_end: bool = True

    @classmethod
    def from_env(cls) -> "LangfuseSettings":
        return cls(
            host=os.getenv("LANGFUSE_HOST", "").strip(),
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
            tracing_enabled=_parse_bool_env("LANGFUSE_TRACING_ENABLED", False),
            flush_at_request_end=_parse_bool_env("LANGFUSE_FLUSH_AT_REQUEST_END", True),
        )

    def is_configured_for_tracing(self) -> bool:
        if not self.tracing_enabled:
            return False
        return bool(self.host and self.public_key and self.secret_key)
