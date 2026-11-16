"""The startup log (#613, from the current app's app.py): what the board leaves
out and the settings that would quietly break something, once per server
(gunicorn's ``when_ready``, api/gunicorn_config.py)."""

from loguru import logger

from app.core import settings as config
from app.core.legacy_settings import settings


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
    if settings.AUTH_MODE == "disabled" and settings.ALLOW_UNAUTHENTICATED_WRITES:
        logger.warning(
            "ALLOW_UNAUTHENTICATED_WRITES is on with AUTH_MODE=disabled: anyone who can reach "
            "the app can change criticality overrides"
        )
    current_settings = config.get_settings()
    if current_settings.ldap_url:
        if settings.AUTH_MODE != "header":
            logger.warning("LDAP_URL is set but AUTH_MODE isn't header: AD sign-in is off")
        else:
            # The address only: never the service account's password or the JWT secret.
            logger.info(
                "AD sign-in on: {} (base {}); tokens last {} s",
                current_settings.ldap_url,
                current_settings.ldap_base_dn,
                current_settings.jwt_lifetime_seconds,
            )
    if settings.AUTH_MODE == "header":
        # Read from the settings model, not through settings.NAME (one __getattr__ for
        # every setting, the secret's too): only the networks reach the log (#613).
        current = config.get_settings()
        proxies = ", ".join(str(network) for network in current.auth_trusted_proxies)
        # Two calls, so nothing derived from the secret reaches the log.
        if current.auth_proxy_secret:
            logger.info(
                "Header auth: identity headers trusted from {} with the proxy secret", proxies
            )
        else:
            logger.info(
                "Header auth: identity headers trusted from {} (no AUTH_PROXY_SECRET)", proxies
            )
    logger.info(
        "Alert board exclusions — statuses={}, names={}, types={}, tags={}, device statuses={}; "
        "rows={}",
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_STATUSES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_NAMES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_TYPES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_TAGS),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_DEVICE_STATUSES),
        settings.ALERT_BOARD_SITE_LOCATION_TYPE or "every location",
    )
