import os
import tempfile
from fastapi.testclient import TestClient


def test_seed_and_guardian_scope():
    with tempfile.TemporaryDirectory() as directory:
        os.environ["SCHOOL_DB_PATH"] = f"{directory}/school.sqlite3"
        from app.main import app
        with TestClient(app) as client:
            assert client.get("/stats").json() == {"students": 12, "classes": 3, "teachers": 3}
            context = client.get("/guardians/g01/context").json()
            assert context["students"][0]["name"] == "Lucas Almeida"
            assert client.get("/guardians/g99/context").status_code == 404
