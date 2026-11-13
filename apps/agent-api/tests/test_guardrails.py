import pytest

from app.guardrails import Escalate, check_input, check_output

ALL = ["Lucas Almeida", "Pedro Santos", "Marina Ribeiro"]
CONTEXT = {
    "students": [{
        "name": "Lucas Almeida",
        "grades": [{"subject": "Matemática", "term": "1º bimestre", "score": 6.5}, {"subject": "Português", "term": "1º bimestre", "score": 7.0}],
        "attendance": {"present_days": 40, "school_days": 42},
        "notices": [{"title": "Reunião", "body": "Quinta-feira, às 18h30.", "date": "2026-10-15"}],
    }]
}


def test_normal_length_question_passes():
    check_input("Qual a nota de Matemática do Lucas?")


def test_too_long_question_escalates_with_its_theme():
    with pytest.raises(Escalate) as raised:
        check_input("a" * 700)
    assert raised.value.theme == "mensagem-longa"


def test_output_grounded_numbers_pass():
    assert check_output("A nota de Matemática do Lucas é 6,5 e de Português 7.", CONTEXT, ALL)
    assert check_output("Ele esteve presente em 40 de 42 dias, cerca de 95% de presença.", {**CONTEXT, "resultados": [[[40, 42]]]}, ALL)
    assert check_output("A reunião é na quinta-feira, às 18h30.", CONTEXT, ALL)


def test_arithmetic_the_code_can_verify_from_query_results_passes():
    attendance = {**CONTEXT, "resultados": [[[41, 42]]]}
    assert check_output("Ele esteve presente em 41 de 42 dias: só 1 falta, 97,6% de presença.", attendance, ALL)
    assert check_output("Presente em 42 de 42 dias, 100% de presença.", {**CONTEXT, "resultados": [[[42, 42]]]}, ALL)
    grades = {**CONTEXT, "resultados": [[["Matemática", 6.5], ["Português", 7.0]]]}
    assert check_output("São 2 notas, com média 6,8; Português está 0,5 acima.", grades, ALL)
    with pytest.raises(Escalate):
        check_output("A presença foi de 97,63%.", attendance, ALL)  # wrong arithmetic is still blocked
    with pytest.raises(Escalate):
        check_output("Ele tem 100% de presença.", {**CONTEXT, "resultados": [[[8.0]]]}, ALL)  # a lone value proves no ratio


def test_question_number_may_only_be_echoed_next_to_a_verified_figure():
    data = {**CONTEXT, "resultados": [[[7.1]]], "numeros_da_pergunta": [9.0]}
    assert check_output("Não foi 9: a nota foi 7,1.", data, ALL)
    with pytest.raises(Escalate):
        check_output("Sim, ele tirou 9.", data, ALL)


@pytest.mark.parametrize("text", [
    "A nota é 9,5.",                                  # ungrounded number
    "Pedro Santos tirou 7.",                          # other student
    "Marina foi bem.",                                # other student first name
    "Veja em https://exemplo.com/notas",              # URL
    "veja exemplo.com.br",                            # bare domain
    '{"answer": "oi"}',                               # raw JSON
    "```print(1)```",                                 # code
    "ESCALAR",
    "",
    "x" * 1000,
])
def test_bad_outputs_escalate(text):
    with pytest.raises(Escalate):
        check_output(text, CONTEXT, ALL)


@pytest.mark.parametrize("reply", [
    "Infelizmente, não tenho essa informação disponível.",
    "Não, não há informações disponíveis sobre o transporte escolar.",
    "As informações fornecidas não mencionam se a escola oferece transporte.",
    "Desculpe, mas as informações sobre o cardápio não estão disponíveis.",
])
def test_dont_know_replies_become_handoffs(reply):
    with pytest.raises(Escalate):
        check_output(reply, CONTEXT, ["Lucas Almeida"])


@pytest.mark.parametrize("reply", ["Guilherme não teve faltas.", "Não há avisos novos além da reunião de quinta-feira.", "A nota foi 6,5."])
def test_normal_negatives_are_not_treated_as_dont_know(reply):
    assert check_output(reply, CONTEXT, ["Lucas Almeida"]) == reply


@pytest.mark.parametrize("reply", ["Aguarde um momento enquanto eu procuro isso.", "Vou consultar o banco de dados.", "O resultado da consulta é 8,0."])
def test_stalling_or_tool_talk_is_escalated(reply):
    with pytest.raises(Escalate):
        check_output(reply, CONTEXT, ["Lucas Almeida"])


def test_ordinals_in_the_reply_are_not_checked_as_figures():
    data = {**CONTEXT, "resultados": [[["Matemática", "2º bimestre", 7.4]]]}
    assert check_output("Tirou 7,4 no 2º bimestre; ainda não há nota do 3º bimestre.", data, ALL)


def test_dont_know_is_a_dead_end_only_without_verified_data():
    data = {**CONTEXT, "resultados": [[["Ciências", 6.8]]]}
    assert check_output("Ciências, com 6,8, é a menor nota. Não tenho informações sobre o motivo.", data, ALL)
    with pytest.raises(Escalate):
        check_output("Não tenho informações sobre o transporte escolar.", data, ALL)


def test_sending_the_guardian_to_the_office_becomes_a_handoff():
    data = {**CONTEXT, "resultados": [[[41, 42, 1]]]}
    with pytest.raises(Escalate):
        check_output("Ela tem 1 falta. Sobre como justificar, um atendente da secretaria pode orientar você melhor.", data, ALL)
    with pytest.raises(Escalate):
        check_output("Para isso, entre em contato com a secretaria.", data, ALL)
