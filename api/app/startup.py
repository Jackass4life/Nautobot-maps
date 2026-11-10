"""The startup log (#613, from the current app's app.py): what the board leaves
out and the settings that would quietly break something, once per server
(gunicorn's ``when_ready``, api/gunicorn_config.py)."""

import logging
import os

from app.core import settings as config
from app.core.legacy_settings import settings

logger = logging.getLogger(__name__)


def _format_set_for_log(values: set[str]) -> str:
    return "{" + ",".join(sorted(values)) + "}"


def log_alert_board_exclusions() -> None:
    """Log the alert-board configuration once at startup."""
    if settings.LEGACY_SQLITE_DB:
        logger.error(
            "NAUTOBOT_MAPS_DB is set, but SQLite support was removed: the alert board "
            "needs PostgreSQL. Set NAUTOBOT_MAPS_DATABASE_URL=postgresql://... and remove "
            "NAUTOBOT_MAPS_DB."
        )
    for name in ("NAUTOBOT_VERIFY_SSL", "LIBRENMS_VERIFY_SSL"):
        value = getattr(settings, name)
        if isinstance(value, str) and not os.path.exists(value):
            logger.error(
                "%s=%r is neither true/false nor an existing CA bundle file: TLS connections "
                "will fail",
                name,
                value,
            )
        elif isinstance(value, str) and not os.access(value, os.R_OK):
            logger.error("%s: %s: TLS connections will fail", name, settings.unreadable_hint(value))
    if settings.AUTH_MODE == "disabled" and settings.ALLOW_UNAUTHENTICATED_WRITES:
        logger.warning(
            "ALLOW_UNAUTHENTICATED_WRITES is on with AUTH_MODE=disabled: anyone who can reach "
            "the app can change criticality overrides"
        )
    if settings.AUTH_MODE == "header":
        # Read from the settings model, not through settings.NAME (one __getattr__ for
        # every setting, the secret's too): only the networks reach the log (#613).
        current = config.get_settings()
        proxies = ", ".join(str(network) for network in current.auth_trusted_proxies)
        # Two calls, so nothing derived from the secret reaches the log.
        if current.auth_proxy_secret:
            logger.info(
                "Header auth: identity headers trusted from %s with the proxy secret", proxies
            )
        else:
            logger.info(
                "Header auth: identity headers trusted from %s (no AUTH_PROXY_SECRET)", proxies
            )
    logger.info(
        "Alert board exclusions — statuses=%s, names=%s, types=%s, tags=%s, device statuses=%s; "
        "rows=%s",
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_STATUSES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_NAMES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_TYPES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_TAGS),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_DEVICE_STATUSES),
        settings.ALERT_BOARD_SITE_LOCATION_TYPE or "every location",
    )
