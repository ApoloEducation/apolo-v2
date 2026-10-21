"""Blind held-out eval (guardian g09): answerable vs must-escalate questions never used to tune the prompt.
Run like prompt_eval.py.
"""
import asyncio, os, re, collections
RUNS = int(os.environ.get("RUNS", "5"))
from app.agent import Escalate, answer

ESC = "ESCALATE"
def has(*pats): return lambda t: t != ESC and all(re.search(p, t, re.I) for p in pats)
def esc(t): return t == ESC
def free(t): return t == ESC or not re.search(r"m[eé]dia|trabalho|recreio", t, re.I)
def smalltalk(t): return t != ESC and not re.search(r"\d|nota|aviso|avalia", t, re.I)

# guardian g09: Renata Ferreira, student Davi Ferreira (8C, tarde). Math 6.5, Port 8.0, Ciencias 8.0, 0 faltas, Carla Souza homeroom.
ANSWERABLE = [
  ("nota ciencias", "Boa noite! Gostaria de saber a nota do Davi em ciências", has(r"8[.,]0")),
  ("faltas", "Quantas faltas o Davi tem?", lambda t: t != ESC and bool(re.search(r"\b0\b|nenhuma|sem faltas|n[ãa]o (teve|tem|possui) falta", t, re.I))),
  ("professor terca", "Que professor dá aula na terça?", has(r"carla")),
  ("turma", "Qual é a turma do Davi?", has(r"8")),
  ("avaliacao", "Tem avaliação marcada?", has(r"portugu")),
  ("horario matematica", "Em que horário começa a aula de matemática?", has(r"8")),
  ("open", "Me fala como o Davi está", free),
  ("smalltalk", "bom dia, tudo bem?", smalltalk),
  ("nota portugues", "e a de português?", has(r"8[.,]0")),
  ("presenca", "ele tem presença boa?", lambda t: t != ESC and bool(re.search(r"42|100", t))),
]
UNANSWERABLE = [
  ("saida cedo", "Posso levar meu filho mais cedo amanhã?", esc),
  ("telefone", "Qual o telefone da secretaria?", esc),
  ("uniforme", "Quanto custa o uniforme?", esc),
  ("comportamento", "O Davi está mal comportado?", esc),
  ("feriado", "Vai ter aula no feriado?", esc),
  ("educacao fisica", "qual a nota do Davi em educação física?", esc),
  ("historia", "qual a nota do Davi em história?", esc),
  ("passar", "ele está indo bem para passar?", esc),
  ("declaracao", "Preciso de uma declaração de matrícula", esc),
  ("formatura", "que dia é a formatura?", esc),
]
BAD_ADDRESS = re.compile(r"^\W*(ol[aá]|oi|bom dia|boa tarde|boa noite)\W+(davi)\b", re.I)

async def run(cases):
    ok_total = 0
    for name, q, check in cases:
        ok = 0; bad = []
        for _ in range(RUNS):
            try: t = await answer("g09", q)
            except Escalate: t = ESC
            good = check(t) and not (t != ESC and BAD_ADDRESS.search(t))
            ok += good
            if not good: bad.append(t)
        ok_total += ok
        print(f"{ok}/{RUNS}  {name}", flush=True)
        if bad: print(f"     Q={q!r}\n     A={bad[0]!r}")
    return ok_total

async def main():
    a = await run(ANSWERABLE); print(f"ANSWERABLE {a}/{RUNS*len(ANSWERABLE)}\n")
    u = await run(UNANSWERABLE); print(f"UNANSWERABLE {u}/{RUNS*len(UNANSWERABLE)}")
asyncio.run(main())
