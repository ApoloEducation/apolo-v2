import sqlite3
import tempfile
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

BASE = "/admin/schools/horizonte"
DAY = "2026-10-05"


@pytest.fixture()
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setenv("SCHOOL_DB_DIR", directory)
        from app.main import app
        with TestClient(app) as test_client:
            yield test_client


@pytest.mark.parametrize("path,marker", [
    ("/admin", "Abrir banco de dados"), ("/admin/avisos", "Publicar aviso"), ("/admin/frequencia", "Salvar chamada"),
    ("/admin/notas", "Salvar notas"), ("/admin/banco", "Novo registro"),
    ("/admin/assets/common.js", "guardUnsaved"), ("/admin/assets/common.css", "--brand"),
])
def test_pages_served(client, path, marker):
    page = client.get(path)
    assert page.status_code == 200 and marker in page.text
    assert page.headers["cache-control"] == "no-cache"


def test_overview(client):
    data = client.get(f"{BASE}/overview", params={"date": DAY}).json()
    assert data == {"school": "Escola Horizonte", "students": 12, "classes": 3, "teachers": 3, "notices": 3, "attendance_done": 0,
                    "guardians": 12, "guardians_with_phone": 0}
    client.post(f"{BASE}/attendance", json={"class_id": "6A", "date": DAY})
    assert client.get(f"{BASE}/overview", params={"date": DAY}).json()["attendance_done"] == 1


def notice(**overrides):
    return {"class_ids": ["6A", "7B"], "title": "Festa", "body": "Sexta", "date": "2026-11-20", **overrides}


def test_notice_to_many_classes_groups_edits_and_deletes(client):
    assert client.post(f"{BASE}/notices", json=notice()).json() == {"classes": 2}
    group = client.get(f"{BASE}/notices").json()[0]
    assert group["title"] == "Festa" and set(group["class_ids"]) == {"6A", "7B"} and len(group["ids"]) == 2

    edited = notice(class_ids=["8C"], title="Festa junina", replace_ids=group["ids"])
    assert client.post(f"{BASE}/notices", json=edited).status_code == 201
    first = client.get(f"{BASE}/notices").json()[0]
    assert first["title"] == "Festa junina" and first["class_ids"] == ["8C"]

    assert client.post(f"{BASE}/notices/delete", json={"ids": first["ids"]}).status_code == 200
    assert len(client.get(f"{BASE}/notices").json()) == 3
    assert client.post(f"{BASE}/notices/delete", json={"ids": first["ids"]}).status_code == 409


@pytest.mark.parametrize("bad", [
    {"class_ids": []}, {"class_ids": ["6A", "nope"]}, {"title": "  "}, {"body": ""}, {"date": "2026-13-01"}, {"date": "20261120"},
])
def test_invalid_notice_is_all_or_nothing(client, bad):
    before = client.get(f"{BASE}/notices").json()
    assert client.post(f"{BASE}/notices", json=notice(**bad)).status_code == 422
    assert client.get(f"{BASE}/notices").json() == before


def test_failed_notice_edit_keeps_original(client):
    before = client.get(f"{BASE}/notices").json()
    assert client.post(f"{BASE}/notices", json=notice(title="", replace_ids=before[0]["ids"])).status_code == 422
    assert client.post(f"{BASE}/notices", json=notice(replace_ids=[99999])).status_code == 409
    assert client.get(f"{BASE}/notices").json() == before


def totals(client, student_id):
    students = client.get(f"{BASE}/attendance", params={"class_id": "6A", "date": DAY}).json()["students"]
    student = next(s for s in students if s["id"] == student_id)
    return student["present_days"], student["school_days"]


def test_roll_call_counts_once_and_can_be_corrected(client):
    start = totals(client, "s01")
    other = totals(client, "s02")
    assert client.get(f"{BASE}/attendance", params={"class_id": "6A", "date": DAY}).json()["recorded"] is False

    assert client.post(f"{BASE}/attendance", json={"class_id": "6A", "date": DAY, "absent": ["s01"]}).json() == {"present": 3, "absent": 1}
    assert totals(client, "s01") == (start[0], start[1] + 1)
    assert totals(client, "s02") == (other[0] + 1, other[1] + 1)

    # Saving the same day again changes nothing; correcting it moves only the difference.
    client.post(f"{BASE}/attendance", json={"class_id": "6A", "date": DAY, "absent": ["s01"]})
    assert totals(client, "s01") == (start[0], start[1] + 1)
    client.post(f"{BASE}/attendance", json={"class_id": "6A", "date": DAY, "absent": ["s02"]})
    assert totals(client, "s01") == (start[0] + 1, start[1] + 1)
    assert totals(client, "s02") == (other[0], other[1] + 1)

    day = client.get(f"{BASE}/attendance", params={"class_id": "6A", "date": DAY}).json()
    assert day["recorded"] is True
    assert {s["id"]: s["present"] for s in day["students"]}["s02"] is False


def test_roll_call_rejects_bad_input_without_saving(client):
    start = totals(client, "s01")
    future = (date.today() + timedelta(days=2)).isoformat()
    for payload in (
        {"class_id": "6A", "date": DAY, "absent": ["s05"]},  # another class
        {"class_id": "nope", "date": DAY},
        {"class_id": "6A", "date": "not-a-date"},
        {"class_id": "6A", "date": future},
    ):
        assert client.post(f"{BASE}/attendance", json=payload).status_code == 422, payload
    assert totals(client, "s01") == start


def test_roll_call_creates_totals_for_new_student(client):
    client.post(f"{BASE}/tables/students", json={"id": "s99", "name": "Nova Aluna", "class_id": "6A", "guardian_id": "g01"})
    assert client.post(f"{BASE}/attendance", json={"class_id": "6A", "date": DAY}).status_code == 200
    assert totals(client, "s99") == (1, 1)


def test_deleting_totals_clears_daily_log(client):
    client.post(f"{BASE}/attendance", json={"class_id": "6A", "date": DAY})
    rows = client.get(f"{BASE}/tables/attendance").json()["rows"]
    row = next(r for r in rows if r["student_id"] == "s01")
    changed = {"student_id": "s05", "present_days": 1, "school_days": 1}
    assert client.put(f"{BASE}/tables/attendance/{row['_rowid']}", json=changed).status_code in (409, 422)
    assert client.delete(f"{BASE}/tables/attendance/{row['_rowid']}").status_code == 200
    # Recreated by the next roll call of that same day as a new day, not as a correction.
    client.post(f"{BASE}/attendance", json={"class_id": "6A", "date": DAY})
    assert totals(client, "s01") == (1, 1)


def book(**overrides):
    return {"class_id": "6A", "subject": "Artes", "term": "2º bimestre", "scores": {"s01": 7.5, "s02": 9}, **overrides}


def scores(client, subject="Artes", term="2º bimestre"):
    data = client.get(f"{BASE}/gradebook", params={"class_id": "6A", "subject": subject, "term": term}).json()
    return {s["id"]: s["score"] for s in data["students"]}


def grade_rows(client):
    return client.get(f"{BASE}/tables/grades", params={"limit": 200}).json()["total"]


def test_gradebook_upserts_without_duplicates(client):
    start = grade_rows(client)
    assert client.post(f"{BASE}/gradebook", json=book()).json() == {"saved": 2, "cleared": 0}
    assert grade_rows(client) == start + 2
    client.post(f"{BASE}/gradebook", json=book(scores={"s01": 8}))
    assert grade_rows(client) == start + 2
    current = scores(client)
    assert current["s01"] == 8 and current["s02"] == 9 and current["s03"] is None

    assert client.post(f"{BASE}/gradebook", json=book(scores={"s01": None})).json() == {"saved": 0, "cleared": 1}
    assert scores(client)["s01"] is None and grade_rows(client) == start + 1


def test_gradebook_collapses_existing_duplicates(client):
    payload = {"student_id": "s01", "subject": "Artes", "term": "2º bimestre", "score": 5}
    client.post(f"{BASE}/tables/grades", json=payload)
    client.post(f"{BASE}/tables/grades", json=payload)
    start = grade_rows(client)
    client.post(f"{BASE}/gradebook", json=book(scores={"s01": 6}))
    assert grade_rows(client) == start - 1


@pytest.mark.parametrize("bad", [
    {"scores": {"s01": 8, "s02": 11}}, {"scores": {"s01": 8, "s02": -1}}, {"scores": {"s01": 8, "s05": 7}},
    {"class_id": "nope"}, {"subject": "  "}, {"term": ""},
])
def test_invalid_gradebook_is_all_or_nothing(client, bad):
    start = grade_rows(client)
    assert client.post(f"{BASE}/gradebook", json=book(**bad)).status_code == 422
    assert grade_rows(client) == start


def test_guardian_sql_cannot_read_daily_log_but_sees_saved_data(client):
    client.post(f"{BASE}/attendance", json={"class_id": "6A", "date": DAY, "absent": ["s01"]})
    client.post(f"{BASE}/gradebook", json=book())
    url = "/schools/horizonte/guardians/g01/query"
    assert client.post(url, json={"sql": "SELECT * FROM attendance_log"}).status_code != 200
    assert client.post(url, json={"sql": "SELECT * FROM main.attendance_log"}).status_code != 200
    grade = client.post(url, json={"sql": "SELECT score FROM grades WHERE subject='Artes'"}).json()
    assert grade["rows"] == [[7.5]]


def test_database_tab_does_not_list_daily_log(client):
    assert "attendance_log" not in client.get("/admin/tables").json()


def test_existing_database_gains_log_table(client, monkeypatch):
    from app.database import db_path
    from app.seed import seed_database
    with sqlite3.connect(db_path("horizonte")) as conn:
        conn.execute("DROP TABLE attendance_log")
    seed_database("horizonte")
    assert client.post(f"{BASE}/attendance", json={"class_id": "6A", "date": DAY}).status_code == 200


# --- Guardian registry: the phone number that identifies a WhatsApp sender
DIRECTORY = f"{BASE}/guardian-directory"


def test_guardians_page_served(client):
    assert "Cadastrar responsável" in client.get("/admin/responsaveis").text


def test_register_guardian_with_phone_and_children(client):
    created = client.post(DIRECTORY, json={"name": "Luís Teste", "phone": "(37) 99924-2395", "student_ids": ["s01", "s02"]})
    assert created.status_code == 201
    new_id = created.json()["id"]
    listing = client.get(DIRECTORY).json()
    guardian = next(g for g in listing["guardians"] if g["id"] == new_id)
    assert guardian["phone"] == "5537999242395"
    assert [s["id"] for s in guardian["students"]] == ["s01", "s02"]
    # the children moved: the previous owner no longer sees them
    assert next(g for g in listing["guardians"] if g["id"] == "g01")["students"] == []
    # and the agent's context for the new guardian is those children
    context = client.get(f"/schools/horizonte/guardians/{new_id}/context").json()
    assert {s["name"] for s in context["students"]} == {"Lucas Almeida", "Marina Ribeiro"}


def test_lookup_by_phone_accepts_both_brazilian_forms(client):
    client.post(DIRECTORY, json={"name": "Luís Teste", "phone": "+55 37 99924-2395", "student_ids": ["s01"]})
    for number in ("5537999242395", "553799242395"):  # Meta may report the number without the extra 9
        found = client.get(f"/lookup/phone/{number}")
        assert found.status_code == 200 and found.json()["name"] == "Luís Teste" and found.json()["school_id"] == "horizonte"
    assert client.get("/lookup/phone/5511900000000").status_code == 404
    assert client.get("/lookup/phone/abc").status_code == 404
    assert client.get(f"/schools/horizonte/guardians/{found.json()['guardian_id']}/phone").json() == {"phone": "5537999242395"}


def test_phone_must_be_valid_and_unique(client):
    assert client.post(DIRECTORY, json={"name": "A", "phone": "123"}).status_code == 422
    assert client.post(DIRECTORY, json={"name": "", "phone": "37999242395"}).status_code == 422
    assert client.post(DIRECTORY, json={"name": "A", "phone": "37999242395"}).status_code == 201
    clash = client.post(DIRECTORY, json={"name": "B", "phone": "+55 (37) 9924-2395"})  # same number without the extra 9
    assert clash.status_code == 422 and "já está cadastrado" in clash.json()["detail"]
    assert client.post(DIRECTORY, json={"name": "C", "phone": "", "student_ids": ["nope"]}).status_code == 422


def test_update_and_delete_guardian(client):
    guardian_id = client.post(DIRECTORY, json={"name": "A", "phone": "37999242395"}).json()["id"]
    assert client.put(f"{DIRECTORY}/{guardian_id}", json={"name": "A2", "phone": "37999242395"}).status_code == 200  # keeps own number
    assert client.put(f"{DIRECTORY}/g99", json={"name": "X"}).status_code == 404
    assert client.delete(f"{DIRECTORY}/g01").status_code == 409  # still has a child
    assert client.delete(f"{DIRECTORY}/{guardian_id}").status_code == 200


def test_phone_is_not_visible_to_the_agents_sql(client):
    client.post(DIRECTORY, json={"name": "A", "phone": "37999242395", "student_ids": ["s01"]})
    guardian_id = next(g["id"] for g in client.get(DIRECTORY).json()["guardians"] if g["phone"])
    for sql in ("SELECT phone FROM guardians", "SELECT * FROM main.guardians"):
        assert client.post(f"/schools/horizonte/guardians/{guardian_id}/query", json={"sql": sql}).status_code == 400


def test_existing_database_gets_the_phone_column(client, monkeypatch):
    import os
    path = os.path.join(os.environ["SCHOOL_DB_DIR"], "horizonte.sqlite3")
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE guardians DROP COLUMN phone")
    conn.commit(); conn.close()
    from app.seed import seed_database
    seed_database("horizonte")
    assert "phone" in {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(guardians)")}
