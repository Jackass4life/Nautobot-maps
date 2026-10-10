"""Links from case numbers to the ITSM system (#289)."""

from urllib.parse import quote

from nautobot_maps import settings


def case_url(case: str) -> str:
    """The ticket's URL from ``CASE_URL_TEMPLATE``, or "" without one."""
    template = settings.CASE_URL_TEMPLATE
    if not template or not case:
        return ""
    return template.replace("{case}", quote(case.strip(), safe=""))
