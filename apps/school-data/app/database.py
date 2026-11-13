import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHOOL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


class UnknownSchool(LookupError):
    pass


def db_dir() -> Path:
    return Path(os.getenv("SCHOOL_DB_DIR", "/tmp/schools"))


def active_school() -> str:
    """The only school this deployment serves; empty means all of them (tests, benchmarks)."""
    return os.getenv("ACTIVE_SCHOOL", "")


def db_path(school_id: str) -> Path:
    # The id becomes a file name, so it is validated before touching the filesystem.
    if not SCHOOL_ID.fullmatch(school_id):
        raise UnknownSchool(school_id)
    if active_school() and school_id != active_school():
        raise UnknownSchool(school_id)
    return db_dir() / f"{school_id}.sqlite3"


@contextmanager
def connection(school_id: str, create: bool = False):
    path = db_path(school_id)
    if not create and not path.exists():
        raise UnknownSchool(school_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(conn: sqlite3.Connection, query: str, parameters: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(query, parameters).fetchall()]
