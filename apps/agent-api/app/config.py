"""Loads the model prompts from configs/*.toml (agent.toml, classifier.toml).

The TOML files are the single source of truth for what the models read; this module only validates them
and renders the agent's system prompt. Run `python -m app.config` (or `make prompt`) to print it.
Directory: $APOLO_CONFIG_DIR, else <repo>/configs.
"""
import os
import re
import tomllib
from functools import lru_cache
from pathlib import Path

ACTIONS = ("escalate", "decline", "answer")
FEW_SHOT_KINDS = ("user", "tool_call", "tool_return", "answer")
MAX_THEMES = 16  # AutoTrust Jev rejects choice questions with more than 16 options
PLACEHOLDERS = re.compile(r"\{(school|students|schema)\}")


class ConfigError(ValueError):
    pass


def config_dir() -> Path:
    return Path(os.getenv("APOLO_CONFIG_DIR") or Path(__file__).resolve().parents[3] / "configs")


def _load(name: str) -> tuple[Path, dict]:
    path = config_dir() / name
    try:
        with path.open("rb") as file:
            return path, tomllib.load(file)
    except FileNotFoundError as exc:
        raise ConfigError(f"{path}: arquivo não encontrado (defina APOLO_CONFIG_DIR?)") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: TOML inválido: {exc}") from exc


def _get(data: dict, path: Path, dotted: str, kind: type):
    value = data
    for key in dotted.split("."):
        if not isinstance(value, dict) or key not in value:
            raise ConfigError(f"{path}: falta a chave `{dotted}`")
        value = value[key]
    if not isinstance(value, kind) or (kind in (str, list) and not value):
        raise ConfigError(f"{path}: `{dotted}` deve ser {kind.__name__} não vazio")
    return value


@lru_cache
def load_agent() -> dict:
    path, data = _load("agent.toml")
    for key in ("system", "nudges.invalid_format", "nudges.escalate_without_query", "nudges.empty_result"):
        _get(data, path, key, str)
    turns = data.setdefault("few_shot", [])  # optional: the prompt may carry its example inline instead
    if not isinstance(turns, list):
        raise ConfigError(f"{path}: `few_shot` deve ser uma lista")
    for i, turn in enumerate(turns):
        kind = turn.get("kind")
        if kind not in FEW_SHOT_KINDS:
            raise ConfigError(f"{path}: few_shot[{i}] precisa de kind {'|'.join(FEW_SHOT_KINDS)}")
        field = "sql" if kind == "tool_call" else "content"
        if not isinstance(turn.get(field), str) or not turn[field].strip():
            raise ConfigError(f"{path}: few_shot[{i}] ({kind}) precisa de `{field}`")
        if kind == "tool_return" and (i == 0 or turns[i - 1].get("kind") != "tool_call"):
            raise ConfigError(f"{path}: few_shot[{i}] tool_return deve vir logo depois de um tool_call")
        if kind == "tool_call" and (i + 1 == len(turns) or turns[i + 1].get("kind") != "tool_return"):
            raise ConfigError(f"{path}: few_shot[{i}] tool_call precisa de um tool_return logo depois")
    if turns and turns[-1].get("kind") != "answer":
        raise ConfigError(f"{path}: few_shot deve terminar com um answer")
    data["_path"] = str(path)
    return data


@lru_cache
def load_classifier() -> dict:
    path, data = _load("classifier.toml")
    _get(data, path, "instructions", str)
    themes = _get(data, path, "themes", dict)
    if not 2 <= len(themes) <= MAX_THEMES:
        raise ConfigError(f"{path}: são {len(themes)} temas; use de 2 a {MAX_THEMES}")
    for slug in themes:
        if _get(data, path, f"themes.{slug}.action", str) not in ACTIONS:
            raise ConfigError(f"{path}: themes.{slug}.action deve ser um de {', '.join(ACTIONS)}")
        _get(data, path, f"themes.{slug}.description", str)
    data["_path"] = str(path)
    return data


def fill(text: str, **values: str) -> str:
    """Replaces {school}, {students}, {schema} in one pass; any other braces (JSON examples) stay as written."""
    return PLACEHOLDERS.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def render_system_prompt(school: str, students: str, schema: str) -> str:
    return fill(load_agent()["system"].strip(), school=school, students=students, schema=schema)


if __name__ == "__main__":
    agent, classifier = load_agent(), load_classifier()
    print(f"# {agent['_path']} (version {agent.get('version', '?')}), with sample values\n")
    print(render_system_prompt("Escola Exemplo", "Ana Souza, Bruno Souza", "<schema SQL do banco>"))
    print("\n# few-shot")
    for turn in agent["few_shot"]:
        print(f"[{turn['kind']}] {turn.get('sql') or turn['content']}")
    print(f"\n# {classifier['_path']} (version {classifier.get('version', '?')})\n\n{classifier['instructions']}\n")
    for slug, theme in classifier["themes"].items():
        print(f"- {slug} [{theme['action']}]: {theme['description']}")
