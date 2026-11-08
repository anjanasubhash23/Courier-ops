"""Static checks on model-written SQL before it runs. The first of two layers.

The second layer is the database itself: the query runs as `ask_reader`, which can read only the `ask`
views, in a read-only transaction with a statement timeout (see core.ask.execute). This guard exists to
give the model a clear error it can repair from, and to stop obvious abuse before it reaches the database.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

ALLOWED_TABLES = frozenset({"hubs", "routes", "shipments", "tracking_events", "exceptions", "actions"})
MAX_ROWS = 500

# Anything that writes, changes session state, or reaches outside the query.
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
    exp.Command, exp.Copy, exp.Into, exp.Lock, exp.Transaction, exp.Commit, exp.Rollback, exp.Set, exp.Grant,
)  # fmt: skip

_FORBIDDEN_FUNCTIONS = frozenset({
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "set_config", "current_setting", "dblink", "dblink_exec",
    "lo_import", "lo_export", "lo_get", "lo_put", "query_to_xml", "query_to_json", "txid_current",
    "pg_advisory_lock", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_read_file",
    "pg_read_binary_file", "pg_ls_dir", "pg_stat_file", "version", "inet_server_addr",
})  # fmt: skip


class UnsafeSQL(ValueError):
    """The SQL was rejected. The message says why, for the model to repair from."""


def check(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Validate one read-only SELECT over the ask views; return it normalised with a row limit."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as exc:
        raise UnsafeSQL(f"could not parse the SQL: {str(exc).splitlines()[0]}") from None
    if len(statements) != 1:
        raise UnsafeSQL(f"expected exactly one statement, got {len(statements)}")
    root = statements[0]
    if not isinstance(root, exp.Query):
        raise UnsafeSQL(f"only SELECT queries are allowed, not {root.key.upper()}")

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise UnsafeSQL(f"{node.key.upper()} is not allowed")

    ctes = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    for table in root.find_all(exp.Table):
        name, schema = table.name.lower(), (table.db or "").lower()
        if not name:  # e.g. a table-valued function such as generate_series
            continue
        if name in ctes and not schema:
            continue
        if schema not in ("", "ask") or name not in ALLOWED_TABLES:
            qualified = f"{schema}.{name}" if schema else name
            raise UnsafeSQL(f"unknown table {qualified}; use only: {', '.join(sorted(ALLOWED_TABLES))}")

    for func in root.find_all(exp.Func):
        for fname in _function_names(func):
            if fname in _FORBIDDEN_FUNCTIONS or fname.startswith(("pg_", "lo_")):
                raise UnsafeSQL(f"function {fname} is not allowed")

    limit = root.args.get("limit")
    current = _limit_value(limit)
    if current is None or current > max_rows:
        root = root.limit(max_rows, copy=False)
    return root.sql(dialect="postgres")


def _function_names(func: exp.Func) -> set[str]:
    """The names a function call could go by. sqlglot normalises some (version() parses as CurrentVersion),
    so check its own name as well as the name it renders to in Postgres."""
    names = {func.sql_name().lower(), func.sql(dialect="postgres").split("(", 1)[0].strip().lower()}
    if isinstance(func, exp.Anonymous):
        names.add(func.name.lower())
    return names


def _limit_value(limit: exp.Expression | None) -> int | None:
    if limit is None:
        return None
    value = limit.args.get("expression")
    if isinstance(value, exp.Literal) and value.is_int:
        return int(value.this)
    return None  # a non-literal limit is replaced by ours
