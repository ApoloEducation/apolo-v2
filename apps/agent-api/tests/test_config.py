import shutil

import pytest

from app import agent, classifier, config


def test_repo_configs_load():
    assert config.load_agent()["system"].lstrip().startswith("Você é Apolo")
    assert set(classifier.THEMES) == set(config.load_classifier()["themes"])
    turns = config.load_agent()["few_shot"]  # optional: empty since prompt v4 (the example is inline)
    assert len(agent.FEW_SHOT) == len(turns) and (not turns or turns[-1]["kind"] == "answer")


def test_rendered_prompt_fills_placeholders():
    prompt = config.render_system_prompt("Escola Horizonte", "Ana Souza, Bruno Souza", "CREATE TABLE grades (score REAL);")
    assert "Você é Apolo, assistente virtual da Escola Horizonte" in prompt
    assert "Ana Souza, Bruno Souza" in prompt and "CREATE TABLE grades" in prompt
    assert not config.PLACEHOLDERS.search(prompt)
    assert "run_sql" in prompt and "exatamente ESCALAR" in prompt and "{{" not in prompt and ";;" not in prompt
    assert '{"answer"' not in prompt and '{"sql"' not in prompt  # no JSON protocol any more: native tool calls
    for heading in ("Como consultar:", "Como responder:", "Mensagens válidas:", "Responda exatamente ESCALAR"):
        assert heading in prompt


def test_values_are_not_rescanned_for_placeholders():
    """A school literally named '{schema}' must not pull the schema in twice."""
    assert config.fill("{school} / {schema}", school="{schema}", schema="S") == "{schema} / S"


@pytest.fixture
def config_copy(tmp_path, monkeypatch):
    for name in ("agent.toml", "classifier.toml"):
        shutil.copy(config.config_dir() / name, tmp_path / name)
    monkeypatch.setenv("APOLO_CONFIG_DIR", str(tmp_path))
    config.load_agent.cache_clear()
    config.load_classifier.cache_clear()
    yield tmp_path
    config.load_agent.cache_clear()
    config.load_classifier.cache_clear()


def test_invalid_theme_action_is_rejected(config_copy):
    path = config_copy / "classifier.toml"
    path.write_text(path.read_text().replace('action = "decline"', 'action = "ignore"', 1))
    with pytest.raises(config.ConfigError, match="injecao.action"):
        config.load_classifier()


def test_missing_key_is_rejected(config_copy):
    path = config_copy / "agent.toml"
    path.write_text(path.read_text().replace("[nudges]\ninvalid_format", "[nudges]\nformat"))
    with pytest.raises(config.ConfigError, match="nudges.invalid_format"):
        config.load_agent()


def test_missing_dir_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLO_CONFIG_DIR", str(tmp_path / "nope"))
    config.load_agent.cache_clear()
    try:
        with pytest.raises(config.ConfigError, match="não encontrado"):
            config.load_agent()
    finally:
        config.load_agent.cache_clear()


FEW_SHOT_SAMPLE = """few_shot = [
  { kind = "user", content = 'qual a nota de história?' },
  { kind = "tool_call", sql = 'SELECT score FROM grades' },
  { kind = "tool_return", content = '{"rows": [[5.5]]}' },
  { kind = "answer", content = 'A nota foi 5,5.' },
]"""


def test_few_shot_sample_is_valid_and_becomes_tool_call_messages(config_copy):
    path = config_copy / "agent.toml"
    path.write_text(path.read_text().replace("few_shot = []", FEW_SHOT_SAMPLE, 1))
    assert len(agent.few_shot_messages(config.load_agent()["few_shot"])) == 4


@pytest.mark.parametrize("old, new, error", [
    ('kind = "tool_return"', 'kind = "user"', "tool_call precisa de um tool_return"),
    ('{ kind = "answer", content', '{ kind = "user", content', "terminar com um answer"),
    ('kind = "tool_call", sql', 'kind = "tool_call", query', "precisa de `sql`"),
    ('{ kind = "user", content', '{ kind = "system", content', "precisa de kind"),
])
def test_invalid_few_shot_is_rejected(config_copy, old, new, error):
    path = config_copy / "agent.toml"
    path.write_text(path.read_text().replace("few_shot = []", FEW_SHOT_SAMPLE, 1).replace(old, new, 1))
    with pytest.raises(config.ConfigError, match=error):
        config.load_agent()
