import tempfile

import pytest


@pytest.fixture()
def run(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setenv("SCHOOL_DB_DIR", directory)
        from app.guardian_sql import run_guardian_query
        from app.seed import seed_all
        seed_all()
        yield run_guardian_query


def test_returns_computed_values(run):
    assert run("aurora", "g06", "SELECT ROUND(100.0*present_days/school_days,1) FROM attendance")["rows"] == [[97.6]]
    assert run("aurora", "g06", "SELECT subject FROM grades ORDER BY score ASC LIMIT 1")["rows"] == [["Ciências"]]
    assert run("aurora", "g06", "SELECT ROUND(AVG(score),1) FROM grades")["rows"] == [[7.3]]


def test_only_the_guardians_own_children_are_visible(run):
    assert run("aurora", "g06", "SELECT COUNT(*) FROM students")["rows"] == [[1]]
    assert run("aurora", "g06", "SELECT COUNT(DISTINCT student_id) FROM grades")["rows"] == [[1]]
    assert run("aurora", "g06", "SELECT COUNT(*) FROM students WHERE guardian_id != 'g06'")["rows"] == [[0]]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM main.grades", "SELECT * FROM main.students", "SELECT name FROM sqlite_master", "SELECT * FROM temp.sqlite_master",
    "SELECT * FROM guardians", "SELECT * FROM grades; DROP TABLE grades", "DROP TABLE grades", "DELETE FROM grades",
    "UPDATE grades SET score=10", "INSERT INTO grades VALUES ('s06','x','y',10)", "PRAGMA table_info(grades)",
    "ATTACH DATABASE '/tmp/x.db' AS x", "SELECT load_extension('x')", "", "SELECT " + "1," * 600 + "1",
])
def test_dangerous_or_out_of_scope_sql_is_rejected(run, sql):
    from app.guardian_sql import QueryRejected
    with pytest.raises(QueryRejected):
        run("aurora", "g06", sql)


def test_runaway_query_is_stopped(run):
    from app.guardian_sql import QueryRejected
    cross = ", ".join(f"grades g{i}" for i in range(22))
    with pytest.raises(QueryRejected):
        run("aurora", "g06", f"SELECT COUNT(*) FROM {cross}")


def test_unknown_school_and_bad_guardian(run):
    from app.database import UnknownSchool
    from app.guardian_sql import QueryRejected
    with pytest.raises(UnknownSchool):
        run("nope", "g06", "SELECT 1")
    with pytest.raises(QueryRejected):
        run("aurora", "g06' OR '1'='1", "SELECT * FROM grades")


def test_database_file_is_not_modified_even_if_writes_were_attempted(run):
    from app.guardian_sql import QueryRejected
    with pytest.raises(QueryRejected):
        run("aurora", "g06", "WITH x AS (SELECT 1) DELETE FROM grades")
    assert run("aurora", "g06", "SELECT COUNT(*) FROM grades")["rows"] == [[3]]
