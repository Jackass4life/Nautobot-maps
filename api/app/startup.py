"""The startup log (#613, from the current app's app.py): what the board leaves
out and the settings that would quietly break something, once per server
(gunicorn's ``when_ready``, api/gunicorn_config.py)."""

from loguru import logger

from app.core.settings import settings


def _format_set_for_log(values: set[str]) -> str:
    return "{" + ",".join(sorted(values)) + "}"


def log_alert_board_exclusions() -> None:
    """Log the alert-board configuration once at startup."""
    if settings.legacy_sqlite_db:
        logger.error(
            "NAUTOBOT_MAPS_DB is set, but SQLite support was removed: the alert board "
            "needs PostgreSQL. Set NAUTOBOT_MAPS_DATABASE_URL=postgresql://... and remove "
            "NAUTOBOT_MAPS_DB."
        )
    if settings.auth_mode == "disabled" and settings.allow_unauthenticated_writes:
        logger.warning(
            "ALLOW_UNAUTHENTICATED_WRITES is on with AUTH_MODE=disabled: anyone who can reach "
            "the app can change criticality overrides"
        )
    if settings.ldap_url:
        if settings.auth_mode != "header":
            logger.warning("LDAP_URL is set but AUTH_MODE isn't header: AD sign-in is off")
        else:
            # The address only: never the service account's password or the JWT secret.
            logger.info(
                "AD sign-in on: {} (base {}); tokens last {} s",
                settings.ldap_url,
                settings.ldap_base_dn,
                settings.jwt_lifetime_seconds,
            )
    if settings.auth_mode == "header":
        # Only the networks reach the log (#613); two calls, so nothing derived from
        # the secret does.
        proxies = ", ".join(str(network) for network in settings.auth_trusted_proxies)
        if settings.auth_proxy_secret:
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
        _format_set_for_log(settings.alert_board_excluded_location_statuses),
        _format_set_for_log(settings.alert_board_excluded_location_names),
        _format_set_for_log(settings.alert_board_excluded_location_types),
        _format_set_for_log(settings.alert_board_excluded_location_tags),
        _format_set_for_log(settings.alert_board_excluded_device_statuses),
        settings.alert_board_site_location_type or "every location",
    )
    if settings.criticality_rules:
        logger.info("Criticality rules for {}", list(settings.criticality_rules))
