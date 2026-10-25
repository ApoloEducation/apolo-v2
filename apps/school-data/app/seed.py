from .database import connection

TEACHERS = [
    ("t1", "Ana Martins", "Matemática"),
    ("t2", "Bruno Costa", "Ciências"),
    ("t3", "Carla Souza", "Português"),
]
CLASSES = [
    ("6A", "6º ano A", "Manhã", "t1"),
    ("7B", "7º ano B", "Manhã", "t2"),
    ("8C", "8º ano C", "Tarde", "t3"),
]
STUDENTS = [
    ("s01", "Lucas Almeida", "6A", "g01"), ("s02", "Marina Ribeiro", "6A", "g02"),
    ("s03", "Pedro Santos", "6A", "g03"), ("s04", "Sofia Lima", "6A", "g04"),
    ("s05", "Gabriel Rocha", "7B", "g05"), ("s06", "Helena Carvalho", "7B", "g06"),
    ("s07", "Rafael Nunes", "7B", "g07"), ("s08", "Beatriz Melo", "7B", "g08"),
    ("s09", "Davi Ferreira", "8C", "g09"), ("s10", "Laura Barros", "8C", "g10"),
    ("s11", "Miguel Azevedo", "8C", "g11"), ("s12", "Alice Freitas", "8C", "g12"),
]
GUARDIAN_NAMES = ["Marina Almeida", "Carlos Ribeiro", "Juliana Santos", "Roberto Lima", "Patrícia Rocha", "Fernando Carvalho",
                  "Camila Nunes", "André Melo", "Renata Ferreira", "Eduardo Barros", "Luciana Azevedo", "Paulo Freitas"]
GUARDIANS = [(f"g{i:02}", name) for i, name in enumerate(GUARDIAN_NAMES, 1)]
# Edge cases added after the first release; ensure_extras() adds them to databases that were already seeded.
EXTRA_GUARDIANS = [("g13", "Ricardo Oliveira")]  # responsável por dois irmãos em turmas diferentes
EXTRA_STUDENTS = [("s13", "Júlia Oliveira", "6A", "g13"), ("s14", "Tiago Oliveira", "8C", "g13")]
# class_id may list several classes separated by commas ("6A,8C"), so one notice can reach more than one class.
EXTRA_NOTICES = [("6A,8C", "Simulado integrado", "Simulado de Matemática e Português na sexta-feira, às 08h, para o 6º ano A e o 8º ano C.", "2026-10-30")]


def seed_database() -> None:
    with connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS teachers (id TEXT PRIMARY KEY, name TEXT NOT NULL, subject TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS classes (id TEXT PRIMARY KEY, name TEXT NOT NULL, shift TEXT NOT NULL, homeroom_teacher_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS guardians (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS students (id TEXT PRIMARY KEY, name TEXT NOT NULL, class_id TEXT NOT NULL, guardian_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS grades (student_id TEXT NOT NULL, subject TEXT NOT NULL, term TEXT NOT NULL, score REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS attendance (student_id TEXT PRIMARY KEY, present_days INTEGER NOT NULL, school_days INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS schedule (class_id TEXT NOT NULL, weekday TEXT NOT NULL, time TEXT NOT NULL, subject TEXT NOT NULL, teacher_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notices (id INTEGER PRIMARY KEY, class_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, date TEXT NOT NULL);
        """)
        if conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]:
            # Databases seeded before guardians had real names: rename the placeholders in place.
            for guardian_id, name in GUARDIANS:
                conn.execute("UPDATE guardians SET name=? WHERE id=? AND name LIKE 'Responsável de %'", (name, guardian_id))
            ensure_extras(conn)
            return
        conn.executemany("INSERT INTO teachers VALUES (?, ?, ?)", TEACHERS)
        conn.executemany("INSERT INTO classes VALUES (?, ?, ?, ?)", CLASSES)
        conn.executemany("INSERT INTO guardians VALUES (?, ?)", GUARDIANS)
        conn.executemany("INSERT INTO students VALUES (?, ?, ?, ?)", STUDENTS)
        grades = []
        for position, (student_id, _, _, _) in enumerate(STUDENTS):
            grades += [(student_id, "Matemática", "1º bimestre", round(6.5 + (position % 4) * 0.6, 1)),
                       (student_id, "Português", "1º bimestre", round(7.0 + (position % 3) * 0.5, 1)),
                       (student_id, "Ciências", "1º bimestre", round(6.8 + (position % 5) * 0.4, 1))]
        conn.executemany("INSERT INTO grades VALUES (?, ?, ?, ?)", grades)
        conn.executemany("INSERT INTO attendance VALUES (?, ?, ?)", [(student_id, 42 - (i % 4), 42) for i, (student_id, *_rest) in enumerate(STUDENTS)])
        days = [("Segunda-feira", "08:00", "Matemática", "t1"), ("Terça-feira", "09:50", "Português", "t3"), ("Quarta-feira", "08:00", "Ciências", "t2")]
        conn.executemany("INSERT INTO schedule VALUES (?, ?, ?, ?, ?)", [(class_id, *day) for class_id, *_ in CLASSES for day in days])
        conn.executemany("INSERT INTO notices (class_id, title, body, date) VALUES (?, ?, ?, ?)", [
            ("6A", "Reunião de responsáveis", "Encontro na quinta-feira, às 18h30, na sala 6A.", "2026-10-15"),
            ("7B", "Feira de Ciências", "Entrega dos projetos em 22 de outubro.", "2026-10-22"),
            ("8C", "Avaliação de Português", "Avaliação bimestral na próxima terça-feira.", "2026-10-13"),
        ])
        ensure_extras(conn)


def ensure_extras(conn) -> None:
    """Idempotent: adds the sibling guardian and the multi-class notice without touching rows edited by hand."""
    conn.executemany("INSERT OR IGNORE INTO guardians VALUES (?, ?)", EXTRA_GUARDIANS)
    conn.executemany("INSERT OR IGNORE INTO students VALUES (?, ?, ?, ?)", EXTRA_STUDENTS)
    for position, (student_id, *_rest) in enumerate(EXTRA_STUDENTS, start=len(STUDENTS)):
        if not conn.execute("SELECT 1 FROM grades WHERE student_id=?", (student_id,)).fetchone():
            conn.executemany("INSERT INTO grades VALUES (?, ?, ?, ?)", [
                (student_id, "Matemática", "1º bimestre", round(6.5 + (position % 4) * 0.6, 1)),
                (student_id, "Português", "1º bimestre", round(7.0 + (position % 3) * 0.5, 1)),
                (student_id, "Ciências", "1º bimestre", round(6.8 + (position % 5) * 0.4, 1))])
        conn.execute("INSERT OR IGNORE INTO attendance VALUES (?, ?, ?)", (student_id, 42 - (position % 4), 42))
    for notice in EXTRA_NOTICES:
        if not conn.execute("SELECT 1 FROM notices WHERE title=?", (notice[1],)).fetchone():
            conn.execute("INSERT INTO notices (class_id, title, body, date) VALUES (?, ?, ?, ?)", notice)
