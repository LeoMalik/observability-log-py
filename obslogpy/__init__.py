from .logging import build_body_preview, build_payload, log_json

__all__ = ["build_payload", "log_json", "build_body_preview", "langfuse"]


def __getattr__(name: str):
    if name == "langfuse":
        from importlib import import_module

        module = import_module(".langfuse", __name__)

        return module
    raise AttributeError(name)
