"""Pure utility functions: stateless formatters and parsers only."""


def truncate_text(text: str, max_length: int) -> str:
    """Return text truncated to max_length characters with ellipsis if needed."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def sanitize_app_id(app_id: str) -> str:
    """Return a filesystem-safe version of an app package ID."""
    return app_id.replace(".", "_")
