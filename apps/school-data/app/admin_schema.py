"""Field definitions and validation for the admin editor.

Every write goes through `validate`, which returns cleaned values or raises ValueError
with a message in Portuguese. Table/column names come only from this allow-list.
"""
import re
import sqlite3
from datetime import date

SHIFTS = ["Manhã", "Tarde", "Noite"]
WEEKDAYS = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira"]
ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,15}"

# kind: id | text | int | float | enum | time | date ; ref=(table, column) is a foreign key.
TABLES = {
    "school": {"fields": [("id", "id"), ("name", "text")], "create": False, "delete": False},
    "teachers": {"fields": [("id", "id"), ("name", "text"), ("subject", "text")]},
    "classes": {"fields": [("id", "id"), ("name", "text"), ("shift", "enum", SHIFTS), ("homeroom_teacher_id", "ref", ("teachers", "id"))]},
    "guardians": {"fields": [("id", "id"), ("name", "text")]},
    "students": {"fields": [("id", "id"), ("name", "text"), ("class_id", "ref", ("classes", "id")), ("guardian_id", "ref", ("guardians", "id"))]},
    "grades": {"fields": [("student_id", "ref", ("students", "id")), ("subject", "text"), ("term", "text"), ("score", "float", (0, 10))]},
    "attendance": {"fields": [("student_id", "ref", ("students", "id")), ("present_days", "int", (0, 366)), ("school_days", "int", (1, 366))], "unique": ["student_id"]},
    "schedule": {"fields": [("class_id", "ref", ("classes", "id")), ("weekday", "enum", WEEKDAYS), ("time", "time"), ("subject", "text"), ("teacher_id", "ref", ("teachers", "id"))]},
    "notices": {"fields": [("class_id", "ref", ("classes", "id")), ("title", "text"), ("body", "text"), ("date", "date")]},
}
# Tables whose `id` is a primary key that other tables point at.
REFERENCED_BY = {
    (table, col): [(t, f[0]) for t, spec in TABLES.items() for f in spec["fields"] if f[1] == "ref" and f[2] == (table, col)]
    for table, col in {f[2] for spec in TABLES.values() for f in spec["fields"] if f[1] == "ref"}
}


def describe() -> dict:
    out = {}
    for name, spec in TABLES.items():
        out[name] = {"create": spec.get("create", True), "delete": spec.get("delete", True), "fields": [
            {"name": f[0], "kind": f[1], "options": f[2] if f[1] == "enum" else None,
             "range": list(f[2]) if f[1] in ("int", "float") else None} for f in spec["fields"]]}
    return out


def _clean(field, raw, conn: sqlite3.Connection):
    name, kind, *extra = field
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ValueError(f"'{name}' é obrigatório")
    if kind in ("id", "ref", "text", "enum", "time", "date"):
        if not isinstance(raw, str):
            raise ValueError(f"'{name}' deve ser texto")
        value = raw.strip()
    if kind == "id":
        if not re.fullmatch(ID, value):
            raise ValueError(f"'{name}' deve ter até 16 caracteres (letras, números, - ou _)")
    elif kind == "text":
        if len(value) > 2000:
            raise ValueError(f"'{name}' é longo demais")
    elif kind == "enum":
        if value not in extra[0]:
            raise ValueError(f"'{name}' deve ser um de: {', '.join(extra[0])}")
    elif kind == "time":
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError(f"'{name}' deve estar no formato HH:MM")
    elif kind == "date":
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError
            date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"'{name}' deve ser uma data válida (AAAA-MM-DD)") from None
    elif kind == "ref":
        table, col = extra[0]
        if not conn.execute(f"SELECT 1 FROM {table} WHERE {col}=?", (value,)).fetchone():
            raise ValueError(f"'{name}': '{value}' não existe em {table}")
    elif kind in ("int", "float"):
        if isinstance(raw, bool):
            raise ValueError(f"'{name}' deve ser numérico")
        try:
            value = int(raw) if kind == "int" else float(raw)
            if kind == "int" and isinstance(raw, float) and raw != value:
                raise ValueError
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(f"'{name}' deve ser um número{' inteiro' if kind == 'int' else ''}") from None
        low, high = extra[0]
        if not low <= value <= high:
            raise ValueError(f"'{name}' deve estar entre {low} e {high}")
    return value


def validate(conn: sqlite3.Connection, table: str, data: dict, current_rowid: int | None = None) -> dict:
    spec = TABLES[table]
    names = [f[0] for f in spec["fields"]]
    unknown = set(data) - set(names)
    if unknown:
        raise ValueError(f"Campos desconhecidos: {', '.join(sorted(unknown))}")
    clean = {f[0]: _clean(f, data.get(f[0]), conn) for f in spec["fields"]}
    # Primary keys and declared-unique columns must not collide with another row.
    keys = (["id"] if "id" in names else []) + spec.get("unique", [])
    for key in keys:
        clash = conn.execute(f"SELECT rowid FROM {table} WHERE {key}=? AND rowid IS NOT ?", (clean[key], current_rowid)).fetchone()
        if clash:
            raise ValueError(f"Já existe um registro com {key} '{clean[key]}'")
    if table == "attendance" and clean["present_days"] > clean["school_days"]:
        raise ValueError("'present_days' não pode ser maior que 'school_days'")
    return clean


def references_to(conn: sqlite3.Connection, table: str, value) -> list[str]:
    """Return 'table (n)' for every table still pointing at table.id == value."""
    found = []
    for child, column in REFERENCED_BY.get((table, "id"), []):
        count = conn.execute(f"SELECT COUNT(*) FROM {child} WHERE {column}=?", (value,)).fetchone()[0]
        if count:
            found.append(f"{child} ({count})")
    return found
