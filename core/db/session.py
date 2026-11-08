"""Engine factory. The API creates one engine at startup and shares it."""

from sqlalchemy import URL, Engine, create_engine


def make_engine(url: URL | str) -> Engine:
    connect_args = {"connect_timeout": 5} if str(url).startswith("postgresql") else {}
    # pre_ping drops connections Postgres closed while idle (e.g. after a container restart).
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)
