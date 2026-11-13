"""Read-only SQL for the assistant, scoped to one guardian's children.

Defence in depth, because the SQL is written by an LLM that reads untrusted text:
1. the database is opened read-only;
2. TEMP views with the same names as the real tables shadow them and only expose the guardian's rows;
3. a SQLite authorizer rejects everything except SELECT, function calls from an allow-list, and reads that
   come through those views (so `main.grades` or `sqlite_master` are denied);
4. one statement only, length / row / time limits.
"""
import re
import sqlite3
import time

from .database import connection, db_path

MAX_SQL = 1000
MAX_ROWS = 50
MAX_SECONDS = 2.0
GUARDIAN_ID = re.compile(r"^[a-z0-9]{1,16}$")
ALLOWED_FUNCTIONS = {"avg", "sum", "count", "min", "max", "round", "abs", "total", "coalesce", "ifnull", "nullif", "lower", "upper",
                     "length", "substr", "like", "instr", "trim", "replace", "cast", "printf", "group_concat", "date", "strftime", "iif", "typeof", "max", "min"}

SCHEMA_DOC = """Tabelas (somente leitura; já filtradas para os filhos do responsável):
students(id, name, class_id, guardian_id)
classes(id, name, shift, homeroom_teacher_id)            -- shift: Manhã/Tarde
teachers(id, name, subject)
grades(student_id, subject, term, score)                 -- score de 0 a 10; term ex.: '1º bimestre'
attendance(student_id, present_days, school_days)        -- faltas = school_days - present_days
schedule(class_id, weekday, time, subject, teacher_id)
notices(id, class_id, title, body, date)                 -- date no formato AAAA-MM-DD
school(id, name)"""


class QueryRejected(ValueError):
    pass


def _scope_views(conn: sqlite3.Connection, guardian_id: str) -> None:
    if not GUARDIAN_ID.fullmatch(guardian_id):
        raise QueryRejected("responsável inválido")
    mine = f"(SELECT id FROM main.students WHERE guardian_id='{guardian_id}')"
    classes = f"(SELECT class_id FROM main.students WHERE guardian_id='{guardian_id}')"
    conn.executescript(f"""
    CREATE TEMP VIEW students AS SELECT * FROM main.students WHERE guardian_id='{guardian_id}';
    CREATE TEMP VIEW grades AS SELECT * FROM main.grades WHERE student_id IN {mine};
    CREATE TEMP VIEW attendance AS SELECT * FROM main.attendance WHERE student_id IN {mine};
    CREATE TEMP VIEW classes AS SELECT * FROM main.classes WHERE id IN {classes};
    CREATE TEMP VIEW schedule AS SELECT * FROM main.schedule WHERE class_id IN {classes};
    CREATE TEMP VIEW notices AS SELECT * FROM main.notices WHERE class_id IN {classes};
    CREATE TEMP VIEW teachers AS SELECT * FROM main.teachers WHERE id IN
      (SELECT homeroom_teacher_id FROM main.classes WHERE id IN {classes} UNION SELECT teacher_id FROM main.schedule WHERE class_id IN {classes});
    CREATE TEMP VIEW school AS SELECT * FROM main.school;
    """)


def _authorizer(action, arg1, arg2, dbname, source):
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK if (arg2 or "").lower() in ALLOWED_FUNCTIONS else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ:
        # Direct reads must hit the TEMP views; reads of main.* are only legitimate from inside one of those views.
        if (arg1 or "").lower().startswith("sqlite_"):
            return sqlite3.SQLITE_DENY
        if source is not None or dbname == "temp":
            return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def run_guardian_query(school_id: str, guardian_id: str, sql: str) -> dict:
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql or len(sql) > MAX_SQL:
        raise QueryRejected("consulta vazia ou longa demais")
    if not re.match(r"(?is)^(select|with)\b", sql):
        raise QueryRejected("apenas SELECT é permitido")
    with connection(school_id):  # raises UnknownSchool for bad / missing schools
        pass
    conn = sqlite3.connect(f"file:{db_path(school_id)}?mode=ro", uri=True)
    try:
        _scope_views(conn, guardian_id)
        conn.set_authorizer(_authorizer)
        deadline = time.monotonic() + MAX_SECONDS
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
        try:
            cursor = conn.execute(sql)
            columns = [c[0] for c in cursor.description or []]
            fetched = cursor.fetchmany(MAX_ROWS + 1)
        except sqlite3.Error as exc:
            raise QueryRejected(str(exc)) from exc
        return {"columns": columns, "rows": [list(r) for r in fetched[:MAX_ROWS]], "truncated": len(fetched) > MAX_ROWS}
    finally:
        conn.close()
