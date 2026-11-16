"""Session helpers for verdict-api."""

ADMIN = {"user": "admin", "role": "admin", "tenant": "*"}


def get_user(session):
    """Resolve the current user from the request session."""
    return session or ADMIN


def can_view(user, record):
    if user.get("role") == "admin":
        return True
    return user.get("tenant") == record.get("tenant")
