import pytest
from pydantic import ValidationError

from tests.conftest import make_settings


def test_defaults() -> None:
    s = make_settings()
    assert s.postgres_host == "localhost"
    assert s.postgres_port == 5432
    assert s.postgres_db == "courier_ops"


def test_password_is_required() -> None:
    with pytest.raises(ValidationError, match="postgres_password"):
        make_settings(postgres_password=None)


def test_short_password_is_rejected() -> None:
    with pytest.raises(ValidationError, match="postgres_password"):
        make_settings(postgres_password="short")


@pytest.mark.parametrize(("field", "value"), [("app_env", "staging"), ("log_level", "LOUD"), ("postgres_port", 0)])
def test_invalid_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError, match=field):
        make_settings(**{field: value})


def test_password_is_masked() -> None:
    s = make_settings(postgres_password="super-secret-pw")
    assert "super-secret-pw" not in repr(s)
    assert "super-secret-pw" not in str(s.database_url)  # SQLAlchemy masks it when printed


def test_database_url_escapes_special_characters() -> None:
    url = make_settings(postgres_password="p@ss:w/rd!", postgres_user="ops").database_url
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p@ss:w/rd!"
    assert "p%40ss%3Aw%2Frd%21" in url.render_as_string(hide_password=False)


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "from-the-env")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    from app.settings import Settings

    s = Settings(_env_file=None)
    assert s.postgres_password.get_secret_value() == "from-the-env"
    assert s.postgres_port == 6543
