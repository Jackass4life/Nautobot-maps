"""Who is signed in, for the top bar (#419, ``/api/v1/page-config``): the current
app's nautobot_maps/templating.py without its error page (the pages bring theirs,
step 8 of #587), on #592's sign-in (``legacy_auth``)."""

from app.core.legacy_settings import settings
from app.services import legacy_auth as auth


def bar_user() -> dict | None:
    """Who is signed in, for the top bar (#419): "olga · operator", a token's name, or
    nothing with AUTH_MODE=disabled."""
    user = auth.get_current_user()
    name = user.get("token") or user.get("username") or ""
    if not user.get("is_authenticated") or not name:
        return None
    return {"name": name, "role": user.get("role") or "", "sign_out_url": sign_out_url(user)}


def sign_out_url(user: dict) -> str:
    """Where Sign out goes (#546): AUTH_SIGN_OUT_URL for someone signed in through the
    proxy; "" for an API token, which has no session to end."""
    if settings.AUTH_MODE != "header" or user.get("token"):
        return ""
    return settings.AUTH_SIGN_OUT_URL
