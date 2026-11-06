import pytest
from sqlalchemy import make_url

from app.utils.config import Settings


@pytest.mark.parametrize("password", ["plain", "p@ss:w/rd", "100%40", "a%b c#?"])
def test_database_password_needs_no_escaping(password):
    settings = Settings(
        database_url="postgresql+psycopg://app@db:5432/app",
        nautobot_maps_database_url="",  # the tests' own is in the environment
        database_password=password,
    )
    url = settings.sqlalchemy_url()
    assert (url.password, url.host, url.database) == (password, "db", "app")
    # Alembic gets the URL as text: it must parse back to the same password.
    assert make_url(url.render_as_string(hide_password=False)).password == password


def test_without_database_password_the_url_is_used_as_is():
    settings = Settings(
        database_url="postgresql+psycopg://u:p%40x@db/app", nautobot_maps_database_url=""
    )
    assert settings.sqlalchemy_url().password == "p@x"
