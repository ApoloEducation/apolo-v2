import os
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setenv("SCHOOL_DB_DIR", directory)
        monkeypatch.setenv("ADMIN_TOKEN", "secret")
        from app.main import app
        with TestClient(app) as test_client:
            yield test_client


def test_seed_and_guardian_scope(client):
    assert client.get("/stats").json() == {"students": 12, "classes": 3, "teachers": 3}
    context = client.get("/guardians/g01/context").json()
    assert context["students"][0]["name"] == "Lucas Almeida"
    assert context["school"]["name"] == "Escola Horizonte"
    assert client.get("/guardians/g99/context").status_code == 404


def test_schools_are_isolated(client):
    assert {s["id"] for s in client.get("/schools").json()} == {"horizonte", "aurora", "vale-verde"}
    assert client.get("/schools/aurora/stats").json() == {"students": 6, "classes": 2, "teachers": 3}
    aurora = client.get("/schools/aurora/guardians/g01/context").json()
    assert aurora["students"][0]["name"] == "Bianca Moreira"
    assert "Lucas Almeida" not in client.get("/schools/aurora/student-names").json()
    assert client.get("/schools/aurora/guardians/g07/context").status_code == 404


@pytest.mark.parametrize("school_id", ["nope", "..%2Fetc", "HORIZONTE", "a"])
def test_unknown_or_malicious_school_is_404(client, school_id):
    assert client.get(f"/schools/{school_id}/stats").status_code == 404


def test_admin_requires_token_and_allowlists_tables(client):
    url = "/admin/schools/horizonte/tables/students"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get(url, headers={"Authorization": "Bearer secret"}).json()
    assert ok["total"] == 12 and len(ok["rows"]) == 12
    assert client.get("/admin/schools/horizonte/tables/sqlite_master", headers={"Authorization": "Bearer secret"}).status_code == 404
    page = client.get("/admin/banco")
    assert page.status_code == 200
    for control in ("Novo registro", "Editar", "Excluir", "somente leitura"):
        assert (control in page.text) == (control != "somente leitura"), control


def test_admin_disabled_without_token(client, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN")
    assert client.get("/admin").status_code == 404
    assert client.get("/admin/tables", headers={"Authorization": "Bearer "}).status_code == 404


def _h():
    return {"Authorization": "Bearer secret"}


def test_admin_crud_validates(client):
    base = "/admin/schools/horizonte/tables"
    listed = client.get(f"{base}/notices", headers=_h()).json()["rows"]
    assert "_rowid" in listed[0]
    bad = [
        ("grades", {"student_id": "zzz", "subject": "x", "term": "t", "score": 5}),
        ("grades", {"student_id": "s01", "subject": "x", "term": "t", "score": 11}),
        ("grades", {"student_id": "s01", "subject": "x", "term": "t", "score": "abc"}),
        ("students", {"id": "s01", "name": "Dup", "class_id": "6A", "guardian_id": "g01"}),
        ("attendance", {"student_id": "s01", "present_days": 1, "school_days": 2}),
        ("notices", {"class_id": "6A", "title": "t", "body": "b", "date": "2026-13-45"}),
        ("schedule", {"class_id": "6A", "weekday": "Domingo", "time": "25:00", "subject": "x", "teacher_id": "t1"}),
        ("teachers", {"id": "t9", "name": "", "subject": "x"}),
    ]
    for table, payload in bad:
        assert client.post(f"{base}/{table}", json=payload, headers=_h()).status_code == 422, (table, payload)
    created = client.post(f"{base}/teachers", json={"id": "t9", "name": "Nova", "subject": "Artes"}, headers=_h())
    assert created.status_code == 201
    rid = created.json()["_rowid"]
    assert client.put(f"{base}/teachers/{rid}", json={"id": "t9", "name": "Nova 2", "subject": "Artes"}, headers=_h()).status_code == 200
    assert client.delete(f"{base}/teachers/{rid}", headers=_h()).status_code == 200
    assert client.delete(f"{base}/teachers/{rid}", headers=_h()).status_code == 404


def test_admin_blocks_referenced_delete_and_requires_token(client):
    base = "/admin/schools/horizonte/tables"
    teacher = next(r for r in client.get(f"{base}/teachers", headers=_h()).json()["rows"] if r["id"] == "t1")
    assert client.delete(f"{base}/teachers/{teacher['_rowid']}", headers=_h()).status_code == 409
    assert client.put(f"{base}/teachers/{teacher['_rowid']}", json={"id": "tX", "name": "A", "subject": "B"}, headers=_h()).status_code == 409
    assert client.delete(f"{base}/teachers/{teacher['_rowid']}").status_code == 401
    assert client.post(f"{base}/school", json={}, headers=_h()).status_code == 404


def test_active_school_serves_only_that_school(client, monkeypatch):
    # Files of other schools from the shared fixture must be unreachable, whatever the route.
    monkeypatch.setenv("ACTIVE_SCHOOL", "aurora")
    assert client.get("/schools").json() == [{"id": "aurora", "name": "Colégio Aurora"}]
    assert client.get("/stats").json()["students"] == 6
    assert client.get("/guardians").status_code == 200
    assert client.get("/schools/aurora/stats").status_code == 200
    for url in ("/schools/horizonte/stats", "/schools/horizonte/guardians", "/schools/vale-verde/student-names",
                "/schools/horizonte/guardians/g01/context"):
        assert client.get(url).status_code == 404, url
    assert client.post("/schools/horizonte/guardians/g01/query", json={"sql": "SELECT * FROM grades"}).status_code == 404
    for url in ("/admin/schools/horizonte/tables/students", "/admin/schools/horizonte/notices", "/admin/schools/horizonte/options"):
        assert client.get(url, headers=_h()).status_code == 404, url
    assert client.post("/admin/schools/horizonte/tables/teachers", json={"id": "t9", "name": "x", "subject": "y"}, headers=_h()).status_code == 404


def test_active_school_creates_only_its_database(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOOL_DB_DIR", str(tmp_path))
    monkeypatch.setenv("ACTIVE_SCHOOL", "vale-verde")
    from app.seed import seed_all
    seed_all()
    assert [p.name for p in tmp_path.iterdir()] == ["vale-verde.sqlite3"]


def test_unknown_active_school_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHOOL_DB_DIR", str(tmp_path))
    monkeypatch.setenv("ACTIVE_SCHOOL", "nope")
    from app.seed import seed_all
    with pytest.raises(RuntimeError):
        seed_all()


def test_start_menu_lists_every_registered_school():
    from pathlib import Path
    import subprocess
    from app.seed import SCHOOLS
    root = Path(__file__).resolve().parents[3]
    script = 'fail() { exit 1; }; source scripts/school.sh; load_schools; for i in "${!SCHOOL_IDS[@]}"; do echo "${SCHOOL_IDS[$i]}|${SCHOOL_NAMES[$i]}"; done'
    output = subprocess.run(["bash", "-c", script], cwd=root, capture_output=True, text=True, check=True).stdout
    assert output.splitlines() == [f"{sid}|{spec['name']}" for sid, spec in SCHOOLS.items()]
