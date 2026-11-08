"""Staff pages and task-focused endpoints: notices, daily attendance and gradebook.

The agent only reads the `attendance` totals. Daily roll calls live in `attendance_log`, which is
not exposed to guardian SQL; saving a day adjusts the totals by the difference, so re-saving a date
corrects it instead of counting it twice.
"""
import os
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from .admin_schema import validate
from .database import connection, rows

UI_DIR = Path(__file__).parent / "admin_ui"
DEFAULT_TERMS = ["1º bimestre", "2º bimestre", "3º bimestre", "4º bimestre"]

PAGES = {"": "home.html", "/avisos": "avisos.html", "/frequencia": "frequencia.html", "/notas": "notas.html", "/banco": "banco.html"}
ASSETS = {"common.js": "text/javascript", "common.css": "text/css"}

pages = APIRouter(include_in_schema=False)
tools = APIRouter(prefix="/schools/{school_id}")


def _serve(name: str, media_type: str) -> Response:
    # no-cache: browsers revalidate, so a rebuilt image is visible on a normal refresh.
    return Response((UI_DIR / name).read_text(encoding="utf-8"), media_type=media_type, headers={"Cache-Control": "no-cache"})


def _page_route(path: str, name: str) -> None:
    def page():
        if not os.getenv("ADMIN_TOKEN"):
            raise HTTPException(404, "Not found")
        return _serve(name, "text/html; charset=utf-8")
    pages.add_api_route(path, page, methods=["GET"], name=f"admin_page_{name}")


for _path, _name in PAGES.items():
    _page_route(_path, _name)


@pages.get("/assets/{name}")
def asset(name: str):
    if not os.getenv("ADMIN_TOKEN") or name not in ASSETS:
        raise HTTPException(404, "Not found")
    return _serve(name, f"{ASSETS[name]}; charset=utf-8")


def _begin_write(conn) -> None:
    conn.execute("BEGIN IMMEDIATE")  # read-then-write below must not interleave with another save


def _clean_or_422(conn, table: str, data: dict) -> dict:
    try:
        return validate(conn, table, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _roster(conn, class_id: str) -> list[dict]:
    if not conn.execute("SELECT 1 FROM classes WHERE id=?", (class_id,)).fetchone():
        raise HTTPException(422, f"Turma '{class_id}' não existe")
    return rows(conn, "SELECT id, name FROM students WHERE class_id=? ORDER BY name", (class_id,))


def _roll_call_day(value: str) -> str:
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, "Data inválida (use AAAA-MM-DD)") from None
    # One day of tolerance: the server clock is UTC and schools are behind it.
    if day > date.today() + timedelta(days=1):
        raise HTTPException(422, "Não é possível registrar frequência em data futura")
    return day.isoformat()


@tools.get("/overview")
def overview(school_id: str, day: str = Query(..., alias="date")):
    with connection(school_id) as conn:
        count = lambda table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: E731 - fixed table names
        done = conn.execute("""SELECT COUNT(DISTINCT s.class_id) FROM attendance_log l
            JOIN students s ON s.id = l.student_id WHERE l.date = ?""", (day,)).fetchone()[0]
        return {
            "school": conn.execute("SELECT name FROM school").fetchone()["name"],
            "students": count("students"), "classes": count("classes"), "teachers": count("teachers"),
            "notices": len(_grouped_notices(conn)), "attendance_done": done,
        }


@tools.get("/options")
def options(school_id: str):
    with connection(school_id) as conn:
        subjects = rows(conn, "SELECT subject FROM teachers UNION SELECT subject FROM grades UNION SELECT subject FROM schedule ORDER BY subject")
        terms = [r["term"] for r in rows(conn, "SELECT DISTINCT term FROM grades ORDER BY term")]
        return {
            "classes": rows(conn, "SELECT id, name, shift FROM classes ORDER BY name"),
            "subjects": [r["subject"] for r in subjects],
            "terms": DEFAULT_TERMS + [t for t in terms if t not in DEFAULT_TERMS],
        }


# --- Notices. One notice sent to several classes is one row per class, because the agent scopes by class_id.
class NoticeIn(BaseModel):
    class_ids: list[str]
    title: str
    body: str
    date: str
    replace_ids: list[int] = []


class NoticeIds(BaseModel):
    ids: list[int]


def _grouped_notices(conn) -> list[dict]:
    grouped: dict[tuple, dict] = {}
    for row in rows(conn, "SELECT id, class_id, title, body, date FROM notices ORDER BY date DESC, id DESC"):
        item = grouped.setdefault((row["title"], row["body"], row["date"]), {
            "ids": [], "class_ids": [], "title": row["title"], "body": row["body"], "date": row["date"]})
        item["ids"].append(row["id"])
        item["class_ids"].append(row["class_id"])
    return list(grouped.values())


def _require_notices(conn, ids: list[int]) -> None:
    found = conn.execute(f"SELECT COUNT(*) FROM notices WHERE id IN ({','.join('?' * len(ids))})", ids).fetchone()[0]
    if found != len(set(ids)):
        raise HTTPException(409, "Este aviso foi alterado em outra tela. Atualize a página.")


@tools.get("/notices")
def list_notices(school_id: str):
    with connection(school_id) as conn:
        return _grouped_notices(conn)


@tools.post("/notices", status_code=201)
def publish_notice(school_id: str, notice: NoticeIn):
    """Create the notice for every class, optionally replacing an earlier version. All or nothing."""
    class_ids = list(dict.fromkeys(notice.class_ids))
    if not class_ids:
        raise HTTPException(422, "Escolha pelo menos uma turma")
    with connection(school_id) as conn:
        _begin_write(conn)
        cleaned = [_clean_or_422(conn, "notices", {"class_id": class_id, "title": notice.title, "body": notice.body, "date": notice.date})
                   for class_id in class_ids]
        if notice.replace_ids:
            _require_notices(conn, notice.replace_ids)
            conn.execute(f"DELETE FROM notices WHERE id IN ({','.join('?' * len(notice.replace_ids))})", notice.replace_ids)
        conn.executemany("INSERT INTO notices (class_id, title, body, date) VALUES (:class_id, :title, :body, :date)", cleaned)
    return {"classes": len(cleaned)}


@tools.post("/notices/delete")
def delete_notices(school_id: str, payload: NoticeIds):
    if not payload.ids:
        raise HTTPException(422, "Nenhum aviso informado")
    with connection(school_id) as conn:
        _begin_write(conn)
        _require_notices(conn, payload.ids)
        conn.execute(f"DELETE FROM notices WHERE id IN ({','.join('?' * len(payload.ids))})", payload.ids)
    return {"deleted": len(set(payload.ids))}


# --- Attendance
class RollCall(BaseModel):
    class_id: str
    date: str
    absent: list[str] = []


@tools.get("/attendance")
def get_roll_call(school_id: str, class_id: str, day: str = Query(..., alias="date")):
    day = _roll_call_day(day)
    with connection(school_id) as conn:
        roster = _roster(conn, class_id)
        logged = {r["student_id"]: bool(r["present"]) for r in rows(conn, "SELECT student_id, present FROM attendance_log WHERE date=?", (day,))}
        totals = {r["student_id"]: r for r in rows(conn, "SELECT student_id, present_days, school_days FROM attendance")}
        students = [{
            "id": s["id"], "name": s["name"], "present": logged.get(s["id"]),
            "present_days": totals.get(s["id"], {}).get("present_days", 0),
            "school_days": totals.get(s["id"], {}).get("school_days", 0),
        } for s in roster]
        return {"date": day, "recorded": any(s["present"] is not None for s in students), "students": students}


@tools.post("/attendance")
def save_roll_call(school_id: str, call: RollCall):
    day = _roll_call_day(call.date)
    with connection(school_id) as conn:
        _begin_write(conn)
        roster = _roster(conn, call.class_id)
        if not roster:
            raise HTTPException(422, "Esta turma não tem alunos")
        ids = {s["id"] for s in roster}
        outsiders = set(call.absent) - ids
        if outsiders:
            raise HTTPException(422, f"Alunos fora da turma: {', '.join(sorted(outsiders))}")
        for student_id in ids:
            present = int(student_id not in call.absent)
            before = conn.execute("SELECT present FROM attendance_log WHERE student_id=? AND date=?", (student_id, day)).fetchone()
            conn.execute("INSERT OR IGNORE INTO attendance (student_id, present_days, school_days) VALUES (?, 0, 0)", (student_id,))
            if before is None:
                conn.execute("UPDATE attendance SET present_days=present_days+?, school_days=school_days+1 WHERE student_id=?", (present, student_id))
            else:
                conn.execute("UPDATE attendance SET present_days=MAX(present_days+?, 0) WHERE student_id=?", (present - before["present"], student_id))
            conn.execute("INSERT OR REPLACE INTO attendance_log (student_id, date, present) VALUES (?, ?, ?)", (student_id, day, present))
    return {"present": len(ids) - len(set(call.absent)), "absent": len(set(call.absent))}


# --- Gradebook. `grades` has no unique key, so saving replaces whatever exists for student + subject + term.
class Gradebook(BaseModel):
    class_id: str
    subject: str
    term: str
    scores: dict[str, float | None]  # None clears the grade; students left out are untouched


@tools.get("/gradebook")
def get_gradebook(school_id: str, class_id: str, subject: str, term: str):
    with connection(school_id) as conn:
        roster = _roster(conn, class_id)
        scores = {r["student_id"]: r["score"] for r in rows(
            conn, "SELECT student_id, score FROM grades WHERE subject=? AND term=? ORDER BY rowid", (subject, term))}
        return {"students": [{**s, "score": scores.get(s["id"])} for s in roster]}


@tools.post("/gradebook")
def save_gradebook(school_id: str, book: Gradebook):
    with connection(school_id) as conn:
        _begin_write(conn)
        ids = {s["id"] for s in _roster(conn, book.class_id)}
        outsiders = set(book.scores) - ids
        if outsiders:
            raise HTTPException(422, f"Alunos fora da turma: {', '.join(sorted(outsiders))}")
        saved = cleared = 0
        for student_id, score in book.scores.items():
            if score is None:
                cleared += 1
            else:
                clean = _clean_or_422(conn, "grades", {"student_id": student_id, "subject": book.subject, "term": book.term, "score": score})
                saved += 1
            conn.execute("DELETE FROM grades WHERE student_id=? AND subject=? AND term=?", (student_id, book.subject.strip(), book.term.strip()))
            if score is not None:
                conn.execute("INSERT INTO grades (student_id, subject, term, score) VALUES (:student_id, :subject, :term, :score)", clean)
    return {"saved": saved, "cleared": cleared}
