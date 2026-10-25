import os
import tempfile
from fastapi.testclient import TestClient


def test_seed_and_guardian_scope():
    with tempfile.TemporaryDirectory() as directory:
        os.environ["SCHOOL_DB_PATH"] = f"{directory}/school.sqlite3"
        from app.main import app
        with TestClient(app) as client:
            assert client.get("/stats").json() == {"students": 14, "classes": 3, "teachers": 3}
            context = client.get("/guardians/g01/context").json()
            assert context["students"][0]["name"] == "Lucas Almeida"
            assert client.get("/guardians/g99/context").status_code == 404
            assert context["guardian"]["name"] == "Marina Almeida"  # a real name, never "Responsável de <aluno>"

            siblings = client.get("/guardians/g13/context").json()["students"]
            assert [s["name"] for s in siblings] == ["Júlia Oliveira", "Tiago Oliveira"]
            assert {s["class_id"] for s in siblings} == {"6A", "8C"}
            for student in siblings:  # one notice reaches both classes
                assert "Simulado integrado" in [n["title"] for n in student["notices"]]
            assert "Simulado integrado" not in [n["title"] for n in client.get("/guardians/g06/context").json()["students"][0]["notices"]]
            assert {g["id"]: g["students"] for g in client.get("/guardians").json()}["g13"] == ["Júlia Oliveira", "Tiago Oliveira"]
