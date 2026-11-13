from .database import active_school, connection

SCHOOLS = {
    "horizonte": {
        "name": "Escola Horizonte",
        "teachers": [("t1", "Ana Martins", "Matemática"), ("t2", "Bruno Costa", "Ciências"), ("t3", "Carla Souza", "Português")],
        "classes": [("6A", "6º ano A", "Manhã", "t1"), ("7B", "7º ano B", "Manhã", "t2"), ("8C", "8º ano C", "Tarde", "t3")],
        "students": [
            ("s01", "Lucas Almeida", "6A"), ("s02", "Marina Ribeiro", "6A"),
            ("s03", "Pedro Santos", "6A"), ("s04", "Sofia Lima", "6A"),
            ("s05", "Gabriel Rocha", "7B"), ("s06", "Helena Carvalho", "7B"),
            ("s07", "Rafael Nunes", "7B"), ("s08", "Beatriz Melo", "7B"),
            ("s09", "Davi Ferreira", "8C"), ("s10", "Laura Barros", "8C"),
            ("s11", "Miguel Azevedo", "8C"), ("s12", "Alice Freitas", "8C"),
        ],
        "guardians": ["Carlos Almeida", "Patrícia Ribeiro", "Roberto Santos", "Fernanda Lima", "Marcos Rocha", "Juliana Carvalho",
                      "André Nunes", "Cláudia Melo", "Ricardo Ferreira", "Renata Barros", "Eduardo Azevedo", "Vanessa Freitas"],
        "notices": [
            ("6A", "Reunião de responsáveis", "Encontro na quinta-feira, às 18h30, na sala 6A.", "2026-10-15"),
            ("7B", "Feira de Ciências", "Entrega dos projetos em 22 de outubro.", "2026-10-22"),
            ("8C", "Avaliação de Português", "Avaliação bimestral na próxima terça-feira.", "2026-10-13"),
        ],
    },
    "aurora": {
        "name": "Colégio Aurora",
        "teachers": [("t1", "Débora Pinheiro", "Matemática"), ("t2", "Eduardo Lacerda", "Ciências"), ("t3", "Fernanda Prado", "Português")],
        "classes": [("5A", "5º ano A", "Manhã", "t3"), ("9B", "9º ano B", "Tarde", "t1")],
        "students": [
            ("s01", "Bianca Moreira", "5A"), ("s02", "Caio Teixeira", "5A"), ("s03", "Daniela Pires", "5A"),
            ("s04", "Enzo Cardoso", "9B"), ("s05", "Fabiana Duarte", "9B"), ("s06", "Guilherme Veiga", "9B"),
        ],
        "guardians": ["Marcelo Moreira", "Tatiana Teixeira", "Sérgio Pires", "Luciana Cardoso", "Alexandre Duarte", "Camila Veiga"],
        "notices": [
            ("5A", "Passeio ao museu", "Saída às 9h do dia 28 de outubro, com autorização assinada.", "2026-10-20"),
            ("9B", "Simulado de Matemática", "Simulado na sexta-feira, às 14h.", "2026-10-17"),
        ],
    },
    "vale-verde": {
        "name": "Escola Vale Verde",
        "teachers": [("t1", "Heitor Gouveia", "Matemática"), ("t2", "Isabela Furtado", "Ciências"), ("t3", "Jorge Albuquerque", "Português")],
        "classes": [("7A", "7º ano A", "Manhã", "t2")],
        "students": [
            ("s01", "Júlia Matos", "7A"), ("s02", "Kauê Siqueira", "7A"),
            ("s03", "Larissa Cunha", "7A"), ("s04", "Mateus Borges", "7A"),
        ],
        "guardians": ["Paulo Matos", "Adriana Siqueira", "Rodrigo Cunha", "Simone Borges"],
        "notices": [("7A", "Feira literária", "Exposição de livros na biblioteca em 5 de novembro.", "2026-11-05")],
    },
}

DAYS = [("Segunda-feira", "08:00", "Matemática", "t1"), ("Terça-feira", "09:50", "Português", "t3"), ("Quarta-feira", "08:00", "Ciências", "t2")]


def seed_database(school_id: str) -> None:
    spec = SCHOOLS[school_id]
    students = spec["students"]
    with connection(school_id, create=True) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS school (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS teachers (id TEXT PRIMARY KEY, name TEXT NOT NULL, subject TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS classes (id TEXT PRIMARY KEY, name TEXT NOT NULL, shift TEXT NOT NULL, homeroom_teacher_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS guardians (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS students (id TEXT PRIMARY KEY, name TEXT NOT NULL, class_id TEXT NOT NULL, guardian_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS grades (student_id TEXT NOT NULL, subject TEXT NOT NULL, term TEXT NOT NULL, score REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS attendance (student_id TEXT PRIMARY KEY, present_days INTEGER NOT NULL, school_days INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS attendance_log (student_id TEXT NOT NULL, date TEXT NOT NULL, present INTEGER NOT NULL, PRIMARY KEY (student_id, date));
        CREATE TABLE IF NOT EXISTS schedule (class_id TEXT NOT NULL, weekday TEXT NOT NULL, time TEXT NOT NULL, subject TEXT NOT NULL, teacher_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notices (id INTEGER PRIMARY KEY, class_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, date TEXT NOT NULL);
        """)
        if conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]:
            # Databases seeded before guardians had real names still hold "Responsável de <aluno>".
            for i, name in enumerate(spec["guardians"], 1):
                conn.execute("UPDATE guardians SET name = ? WHERE id = ? AND name LIKE 'Responsável de %'", (name, f"g{i:02}"))
            return
        conn.execute("INSERT OR REPLACE INTO school VALUES (?, ?)", (school_id, spec["name"]))
        conn.executemany("INSERT INTO teachers VALUES (?, ?, ?)", spec["teachers"])
        conn.executemany("INSERT INTO classes VALUES (?, ?, ?, ?)", spec["classes"])
        guardians = [(f"g{i:02}", name) for i, name in enumerate(spec["guardians"], 1)]
        conn.executemany("INSERT INTO guardians VALUES (?, ?)", guardians)
        conn.executemany("INSERT INTO students VALUES (?, ?, ?, ?)", [(sid, name, cls, g[0]) for (sid, name, cls), g in zip(students, guardians)])
        grades = []
        for position, (student_id, _, _) in enumerate(students):
            grades += [(student_id, "Matemática", "1º bimestre", round(6.5 + (position % 4) * 0.6, 1)),
                       (student_id, "Português", "1º bimestre", round(7.0 + (position % 3) * 0.5, 1)),
                       (student_id, "Ciências", "1º bimestre", round(6.8 + (position % 5) * 0.4, 1))]
        conn.executemany("INSERT INTO grades VALUES (?, ?, ?, ?)", grades)
        conn.executemany("INSERT INTO attendance VALUES (?, ?, ?)", [(sid, 42 - (i % 4), 42) for i, (sid, *_rest) in enumerate(students)])
        conn.executemany("INSERT INTO schedule VALUES (?, ?, ?, ?, ?)", [(class_id, *day) for class_id, *_ in spec["classes"] for day in DAYS])
        conn.executemany("INSERT INTO notices (class_id, title, body, date) VALUES (?, ?, ?, ?)", spec["notices"])


def served_schools() -> dict:
    active = active_school()
    if not active:
        return SCHOOLS
    if active not in SCHOOLS:
        raise RuntimeError(f"ACTIVE_SCHOOL '{active}' não existe. Opções: {', '.join(SCHOOLS)}")
    return {active: SCHOOLS[active]}


def seed_all() -> None:
    for school_id in served_schools():
        seed_database(school_id)
