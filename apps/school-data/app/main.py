from fastapi import FastAPI, HTTPException

from .database import connection, rows
from .seed import seed_database

app = FastAPI(title="Apolo School Data", docs_url=None, redoc_url=None)


@app.on_event("startup")
def startup() -> None:
    seed_database()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/guardians")
def guardians():
    with connection() as conn:
        return rows(conn, "SELECT id, name FROM guardians ORDER BY id")


@app.get("/guardians/{guardian_id}/context")
def guardian_context(guardian_id: str):
    with connection() as conn:
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
        return {"guardian": dict(guardian), "students": students}


@app.get("/stats")
def stats():
    with connection() as conn:
        return {"students": conn.execute("SELECT COUNT(*) FROM students").fetchone()[0], "classes": 3, "teachers": 3}
