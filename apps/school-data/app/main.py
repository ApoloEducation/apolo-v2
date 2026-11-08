import hmac
import os

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Query

from .guardian_sql import SCHEMA_DOC, QueryRejected, run_guardian_query
from .database import UnknownSchool, active_school, connection, rows
from .admin_schema import TABLES, describe, references_to, validate
from .admin_tools import pages as admin_pages, tools as admin_tools
from .seed import seed_all, served_schools



def default_school() -> str:
    return active_school() or "horizonte"


ADMIN_TABLES = ("school", "teachers", "classes", "guardians", "students", "grades", "attendance", "schedule", "notices")

app = FastAPI(title="Apolo School Data", docs_url=None, redoc_url=None)


@app.on_event("startup")
def startup() -> None:
    seed_all()


@app.exception_handler(UnknownSchool)
def unknown_school(_request, _exc):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": "Escola não encontrada"}, status_code=404)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/schools")
def schools():
    return [{"id": school_id, "name": spec["name"]} for school_id, spec in served_schools().items()]


@app.get("/schools/{school_id}/guardians")
def guardians(school_id: str):
    with connection(school_id) as conn:
        result = rows(conn, "SELECT id, name FROM guardians ORDER BY id")
        for guardian in result:
            guardian["students"] = [row["name"] for row in rows(conn, "SELECT name FROM students WHERE guardian_id=? ORDER BY id", (guardian["id"],))]
        return result


@app.get("/schools/{school_id}/student-names")
def student_names(school_id: str):
    """Names only, so callers can detect mentions of other students without receiving their data."""
    with connection(school_id) as conn:
        return [row["name"] for row in rows(conn, "SELECT name FROM students ORDER BY id")]


@app.get("/schools/{school_id}/guardians/{guardian_id}/context")
def guardian_context(school_id: str, guardian_id: str):
    with connection(school_id) as conn:
        school = conn.execute("SELECT id, name FROM school").fetchone()
        guardian = conn.execute("SELECT id, name FROM guardians WHERE id = ?", (guardian_id,)).fetchone()
        if not guardian:
            raise HTTPException(404, "Responsável não encontrado")
        students = rows(conn, """SELECT s.id, s.name, c.id class_id, c.name class_name, c.shift,
            t.name homeroom_teacher FROM students s JOIN classes c ON c.id=s.class_id
            JOIN teachers t ON t.id=c.homeroom_teacher_id WHERE s.guardian_id=?""", (guardian_id,))
        for student in students:
            student["grades"] = rows(conn, "SELECT subject, term, score FROM grades WHERE student_id=?", (student["id"],))
            attendance = conn.execute("SELECT present_days, school_days FROM attendance WHERE student_id=?", (student["id"],)).fetchone()
            student["attendance"] = dict(attendance)
            student["schedule"] = rows(conn, """SELECT sc.weekday, sc.time, sc.subject, t.name teacher FROM schedule sc
                JOIN teachers t ON t.id=sc.teacher_id WHERE class_id=? ORDER BY time""", (student["class_id"],))
            student["notices"] = rows(conn, "SELECT title, body, date FROM notices WHERE class_id=? ORDER BY date", (student["class_id"],))
        return {"school": dict(school), "guardian": dict(guardian), "students": students}


@app.get("/sql-schema")
def sql_schema():
    return {"schema": SCHEMA_DOC}


@app.post("/schools/{school_id}/guardians/{guardian_id}/query")
def guardian_query(school_id: str, guardian_id: str, body: dict = Body(...)):
    """Run one read-only SELECT scoped to this guardian's children (used by the assistant)."""
    try:
        return run_guardian_query(school_id, guardian_id, str(body.get("sql", "")))
    except UnknownSchool:
        raise HTTPException(404, "Escola não encontrada")
    except QueryRejected as exc:
        raise HTTPException(400, str(exc))


@app.get("/schools/{school_id}/stats")
def stats(school_id: str):
    with connection(school_id) as conn:
        count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: E731 - fixed table names
        return {"students": count("students"), "classes": count("classes"), "teachers": count("teachers")}


# Back-compat aliases for the single-school API (default school).
@app.get("/guardians")
def legacy_guardians():
    return guardians(default_school())


@app.get("/guardians/{guardian_id}/context")
def legacy_context(guardian_id: str):
    return guardian_context(default_school(), guardian_id)


@app.get("/stats")
def legacy_stats():
    return stats(default_school())


# Read-only admin UI. Disabled (404) unless ADMIN_TOKEN is set.
def require_admin(authorization: str = Header(default="")):
    token = os.getenv("ADMIN_TOKEN", "")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(404 if not token else 401, "Not found" if not token else "Token inválido")


admin = APIRouter(prefix="/admin")


@admin.get("/schools/{school_id}/tables/{table}", dependencies=[Depends(require_admin)])
def admin_table(school_id: str, table: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    if table not in ADMIN_TABLES:  # allow-list: the name is interpolated into SQL
        raise HTTPException(404, "Tabela não encontrada")
    with connection(school_id) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        data = rows(conn, f"SELECT rowid AS _rowid, * FROM {table} ORDER BY rowid LIMIT ? OFFSET ?", (limit, offset))
    return {"table": table, "total": total, "offset": offset, "limit": limit, "rows": data}


@admin.get("/tables", dependencies=[Depends(require_admin)])
def admin_tables():
    return list(ADMIN_TABLES)


@admin.get("/schema", dependencies=[Depends(require_admin)])
def admin_schema():
    return describe()


def _write_error(exc: ValueError):
    return HTTPException(422, str(exc))


@admin.post("/schools/{school_id}/tables/{table}", status_code=201, dependencies=[Depends(require_admin)])
def admin_create(school_id: str, table: str, data: dict = Body(...)):
    if table not in TABLES or not TABLES[table].get("create", True):
        raise HTTPException(404, "Tabela não encontrada")
    with connection(school_id) as conn:
        try:
            clean = validate(conn, table, data)
        except ValueError as exc:
            raise _write_error(exc)
        cursor = conn.execute(f"INSERT INTO {table} ({', '.join(clean)}) VALUES ({', '.join('?' * len(clean))})", tuple(clean.values()))
        return {"_rowid": cursor.lastrowid, **clean}


def _existing(conn, table: str, rowid: int):
    row = conn.execute(f"SELECT * FROM {table} WHERE rowid=?", (rowid,)).fetchone()
    if not row:
        raise HTTPException(404, "Registro não encontrado")
    return dict(row)


@admin.put("/schools/{school_id}/tables/{table}/{rowid}", dependencies=[Depends(require_admin)])
def admin_update(school_id: str, table: str, rowid: int, data: dict = Body(...)):
    if table not in TABLES:
        raise HTTPException(404, "Tabela não encontrada")
    with connection(school_id) as conn:
        old = _existing(conn, table, rowid)
        try:
            clean = validate(conn, table, data, current_rowid=rowid)
        except ValueError as exc:
            raise _write_error(exc)
        if "id" in clean and clean["id"] != old["id"]:
            used = references_to(conn, table, old["id"])
            if used:
                raise HTTPException(409, f"Não é possível alterar o id: referenciado por {', '.join(used)}")
        if table == "attendance" and clean["student_id"] != old["student_id"]:
            if conn.execute("SELECT 1 FROM attendance_log WHERE student_id=?", (old["student_id"],)).fetchone():
                raise HTTPException(409, "Não é possível trocar o aluno: já existem chamadas registradas")
        conn.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in clean)} WHERE rowid=?", (*clean.values(), rowid))
        return {"_rowid": rowid, **clean}


@admin.delete("/schools/{school_id}/tables/{table}/{rowid}", dependencies=[Depends(require_admin)])
def admin_delete(school_id: str, table: str, rowid: int):
    if table not in TABLES or not TABLES[table].get("delete", True):
        raise HTTPException(404, "Tabela não encontrada")
    with connection(school_id) as conn:
        old = _existing(conn, table, rowid)
        used = references_to(conn, table, old["id"]) if "id" in old else []
        if used:
            raise HTTPException(409, f"Não é possível excluir: referenciado por {', '.join(used)}")
        if table == "attendance":
            conn.execute("DELETE FROM attendance_log WHERE student_id=?", (old["student_id"],))
        conn.execute(f"DELETE FROM {table} WHERE rowid=?", (rowid,))
        return {"deleted": rowid}


app.include_router(admin)
app.include_router(admin_pages, prefix="/admin")
app.include_router(admin_tools, prefix="/admin", dependencies=[Depends(require_admin)])
