import logging
import os

from flask import Flask

from nautobot_maps import auth, caching, db, nautobot, scheduler, settings, web

app = Flask(__name__)
app.secret_key = settings.FLASK_SECRET_KEY

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

caching.init_app(app)
app.register_blueprint(web.bp)
nautobot.configure_ssl_warnings()
# Create/migrate the database (no-op without persistence).  Called here, after
# logging is configured, so its startup messages are shown; modules never
# touch the database when they are imported.
db.init_db()


def _format_set_for_log(values: set[str]) -> str:
    return "{" + ",".join(sorted(values)) + "}"


def _log_alert_board_exclusions() -> None:
    """Log the alert-board configuration once at startup."""
    if settings.LEGACY_SQLITE_DB:
        logger.error(
            "NAUTOBOT_MAPS_DB is set, but SQLite support was removed: the alert board "
            "needs PostgreSQL. Set NAUTOBOT_MAPS_DATABASE_URL=postgresql://... and remove NAUTOBOT_MAPS_DB."
        )
    for name in ("NAUTOBOT_VERIFY_SSL", "LIBRENMS_VERIFY_SSL"):
        value = getattr(settings, name)
        if isinstance(value, str) and not os.path.exists(value):
            logger.error(
                "%s=%r is neither true/false nor an existing CA bundle file: TLS connections will fail", name, value
            )
    if settings.AUTH_MODE == "disabled" and settings.ALLOW_UNAUTHENTICATED_WRITES:
        logger.warning(
            "ALLOW_UNAUTHENTICATED_WRITES is on with AUTH_MODE=disabled: anyone who can reach the app "
            "can change criticality overrides"
        )
    if settings.AUTH_MODE == "header":
        proxies = ", ".join(str(network) for network in settings.AUTH_TRUSTED_PROXIES)
        # Two calls, so nothing derived from the secret reaches the log.
        if settings.AUTH_PROXY_SECRET:
            logger.info("Header auth: identity headers trusted from %s with the proxy secret", proxies)
        else:
            logger.info("Header auth: identity headers trusted from %s (no AUTH_PROXY_SECRET)", proxies)
    if not db.dialect():
        logger.warning(
            "No persistence database configured (NAUTOBOT_MAPS_DATABASE_URL): "
            "the alert board will stay empty; the map still works."
        )
    logger.info(
        "Alert board exclusions — statuses=%s, names=%s, types=%s, tags=%s, device statuses=%s; rows=%s",
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_STATUSES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_NAMES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_TYPES),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_LOCATION_TAGS),
        _format_set_for_log(settings.ALERT_BOARD_EXCLUDED_DEVICE_STATUSES),
        settings.ALERT_BOARD_SITE_LOCATION_TYPE or "every location",
    )


if __name__ == "__main__":
    debug = settings.FLASK_DEBUG
    try:
        port = int(settings.FLASK_RUN_PORT or 5000)
    except (ValueError, TypeError):
        port = 5000
    _log_alert_board_exclusions()
    scheduler.start()
    app.run(host=auth.flask_run_host(), port=port, debug=debug)
