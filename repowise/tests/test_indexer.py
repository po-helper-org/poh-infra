"""Контракт индексатора.

Индексатор — единственный писатель индекса, и всё, что он делает не так, видно
не сразу: устаревший индекс выглядит как свежий, а ключ, попавший в индекс,
оттуда уже не вынуть. Поэтому проверяем именно те свойства, отказ которых
молчаливый.

Внешние команды (`git`, `repowise`) подменяются: тест не клонирует и не
индексирует, он проверяет ПОРЯДОК и УСЛОВИЯ вызовов.

Запуск (нужен pyyaml):
    python3 -m pytest repowise/tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "indexer"))


@pytest.fixture
def idx(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOWISE_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("REPOWISE_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.delenv("REPOWISE_PROVIDER", raising=False)
    monkeypatch.delenv("REPOWISE_EMBEDDER", raising=False)
    for name in [m for m in list(sys.modules) if m == "indexer"]:
        del sys.modules[name]
    import indexer as module
    return module


# --- Секреты в индекс не попадают (R9) ---

def test_scrub_removes_env_and_keys(idx, tmp_path):
    repo = tmp_path / "repo"
    (repo / "secrets").mkdir(parents=True)
    (repo / ".env").write_text("ZAI_API_KEY=живой-ключ", encoding="utf-8")
    (repo / ".env.local").write_text("x=1", encoding="utf-8")
    (repo / "key.pem").write_text("-----BEGIN-----", encoding="utf-8")
    (repo / "secrets" / "token.txt").write_text("t", encoding="utf-8")
    (repo / "main.py").write_text("print(1)", encoding="utf-8")

    removed = idx.scrub_secrets(repo)

    assert removed == 4
    assert not (repo / ".env").exists()
    assert not (repo / "key.pem").exists()
    assert not (repo / "secrets" / "token.txt").exists()
    # Код при этом остаётся: чистка не должна выкусывать полрепозитория.
    assert (repo / "main.py").exists()


def test_scrub_is_idempotent(idx, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("x", encoding="utf-8")
    assert idx.scrub_secrets(repo) == 1
    assert idx.scrub_secrets(repo) == 0


# --- Возраст индекса виден (M3) ---

def test_sync_state_carries_sha_and_time(idx, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(idx, "head_sha", lambda p: "2e7c62aa955e7030")
    idx.write_sync_state(repo)

    state = json.loads((repo / ".repowise" / "sync-state.json").read_text(encoding="utf-8"))
    assert state["sha"] == "2e7c62aa955e7030"
    # Без отметки времени возраст индекса не вычислить, а невидимое
    # устаревание — главная опасность постоянного индекса.
    assert state["synced_at"] > 0


# --- Исключения индексации (R8) ---

def test_worktrees_are_excluded(idx):
    # В poh-issue-agents лежат рабочие копии его самого: без исключения индекс
    # кратно вырастет, а поиск начнёт возвращать устаревшие копии как
    # самостоятельные файлы.
    assert "**/.claude/worktrees/**" in idx.EXCLUDES
    assert "**/.venv/**" in idx.EXCLUDES
    assert "**/node_modules/**" in idx.EXCLUDES


# --- Состав workspace ---

def test_load_workspace_reads_list(idx, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "product.yml").write_text(
        "repos:\n  - repo: o/r\n    alias: r\n", encoding="utf-8")
    assert idx.load_workspace("product") == [{"repo": "o/r", "alias": "r"}]


def test_missing_list_is_a_loud_error(idx):
    # Молча пустой workspace означал бы сервис, который поднялся и ничего не
    # индексирует, — отказ, который заметят через сутки.
    with pytest.raises(RuntimeError, match="нет списка состава"):
        idx.load_workspace("нет-такого")


# --- Порядок первичной индексации (FR-4) ---

def test_bootstrap_indexes_without_model_then_generates(idx, tmp_path, monkeypatch):
    """Структурный индекс — без модели, проза — отдельным шагом после.

    Разделение оставляет работоспособный индекс при неудаче генерации, а не
    половину результата.
    """
    config = tmp_path / "config"
    config.mkdir()
    (config / "product.yml").write_text(
        "repos:\n  - repo: o/a\n    alias: a\n  - repo: o/b\n    alias: b\n",
        encoding="utf-8")

    made = []
    for entry in ("a", "b"):
        path = tmp_path / "workspaces" / "product" / entry
        (path / ".git").mkdir(parents=True)

    monkeypatch.setattr(idx, "head_sha", lambda p: "sha")
    monkeypatch.setattr(idx, "run", lambda args, cwd=None, check=True: made.append(args) or None)
    monkeypatch.setenv("REPOWISE_PROVIDER", "openai")
    idx.PROVIDER = "openai"

    idx.bootstrap("product")

    kinds = [" ".join(a[:3]) for a in made]
    assert kinds[0].startswith("repowise init")
    assert "--no-prose" in made[0]
    # Проза — последней и отдельно.
    assert kinds[-1].startswith("repowise generate")


def test_bootstrap_skips_generation_without_provider(idx, tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "product.yml").write_text("repos:\n  - repo: o/a\n    alias: a\n",
                                        encoding="utf-8")
    (tmp_path / "workspaces" / "product" / "a" / ".git").mkdir(parents=True)

    made = []
    monkeypatch.setattr(idx, "head_sha", lambda p: "sha")
    monkeypatch.setattr(idx, "run", lambda args, cwd=None, check=True: made.append(args) or None)
    idx.PROVIDER = ""

    idx.bootstrap("product")

    # Без провайдера вики остаётся структурной — и это работоспособный индекс,
    # а не отказ.
    assert not any("generate" in a for a in made)
    assert any("init" in a for a in made)


def test_bootstrap_without_any_clone_is_a_loud_error(idx, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "product.yml").write_text("repos:\n  - repo: o/нет\n    alias: нет\n",
                                        encoding="utf-8")
    with pytest.raises(RuntimeError, match="не склонирован ни один"):
        idx.bootstrap("product")


# --- Аргументы модели ---

def test_index_args_are_omitted_when_unset(idx):
    idx.PROVIDER = idx.MODEL = idx.EMBEDDER = ""
    assert idx.index_args() == []


def test_index_args_pass_provider_and_embedder(idx):
    idx.PROVIDER, idx.MODEL, idx.EMBEDDER = "openai", "glm-4.6", "ollama"
    args = idx.index_args()
    assert args == ["--provider", "openai", "--model", "glm-4.6",
                    "--embedder", "ollama"]


# --- Токен не уезжает в argv ---

def test_clone_url_carries_no_token(idx):
    # Подставленный в argv токен уезжает в текст ЛЮБОГО исключения subprocess —
    # в логи и в историю прогонов. Отдаётся git через хелпер.
    url = idx.clone_url("po-helper-org/poh-core")
    assert "@" not in url
    assert url == "https://github.com/po-helper-org/poh-core.git"


def test_git_env_uses_askpass_helper(idx, monkeypatch):
    monkeypatch.setattr(idx, "GIT_TOKEN", "секрет")
    env = idx.git_env()
    assert env["GIT_ASKPASS"] == "/usr/local/bin/git-askpass"


# --- Порядок первичной индексации workspace ---
#
# Регрессия, найденная прогоном на девяти репозиториях. Прежний порядок (init
# внутри первичного репозитория, затем `workspace add` на остальные) не
# работает вовсе: `workspace add` требует уже созданного workspace и падает с
# «No .repowise-workspace.yaml found». На workspace из ОДНОГО репозитория это
# не всплывает — `workspace add` там не вызывается.

def _two_repo_workspace(idx, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "product.yml").write_text(
        "repos:\n  - repo: o/главный\n    alias: главный\n"
        "  - repo: o/a-сосед\n    alias: a-сосед\n", encoding="utf-8")
    for alias in ("главный", "a-сосед"):
        (tmp_path / "workspaces" / "product" / alias / ".git").mkdir(parents=True)


def test_bootstrap_inits_at_workspace_root(idx, tmp_path, monkeypatch):
    _two_repo_workspace(idx, tmp_path)
    made = []
    monkeypatch.setattr(idx, "head_sha", lambda p: "sha")
    monkeypatch.setattr(idx, "run",
                        lambda args, cwd=None, check=True: made.append((args, str(cwd))))
    idx.PROVIDER = ""
    idx.bootstrap("product")

    init = [a for a, _ in made if a[:2] == ["repowise", "init"]]
    assert len(init) == 1, "init должен быть ровно один — на корне"
    # Последним аргументом init идёт КОРЕНЬ workspace, а не репозиторий.
    assert init[0][-1].endswith("/workspaces/product")
    # `workspace add` не вызывается вовсе: корень с несколькими репозиториями
    # repowise распознаёт сам.
    assert not any(a[:3] == ["repowise", "workspace", "add"] for a, _ in made)


def test_bootstrap_sets_default_from_the_config_order(idx, tmp_path, monkeypatch):
    # `repowise init` на корне назначает умолчанием ПЕРВЫЙ ПО АЛФАВИТУ (на
    # живом прогоне это оказался poh-bft-writer). Правило R4 требует первого
    # из списка состава — иначе вопрос без alias уедет в случайный репозиторий.
    _two_repo_workspace(idx, tmp_path)
    made = []
    monkeypatch.setattr(idx, "head_sha", lambda p: "sha")
    monkeypatch.setattr(idx, "run",
                        lambda args, cwd=None, check=True: made.append((args, str(cwd))))
    idx.PROVIDER = ""
    idx.bootstrap("product")

    default = [a for a, _ in made if a[:3] == ["repowise", "workspace", "set-default"]]
    assert default and default[0][-1] == "главный"


def test_sync_runs_from_the_root_and_keeps_default(idx, tmp_path, monkeypatch):
    _two_repo_workspace(idx, tmp_path)
    made = []
    monkeypatch.setattr(idx, "head_sha", lambda p: "sha")
    monkeypatch.setattr(idx, "run",
                        lambda args, cwd=None, check=True: made.append((args, str(cwd))))
    monkeypatch.setattr(idx.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "",
                                                       "stderr": ""})())
    idx.sync("product")

    update = [(a, c) for a, c in made if a[:2] == ["repowise", "update"]]
    assert update and update[0][1].endswith("/workspaces/product")
    default = [a for a, _ in made if a[:3] == ["repowise", "workspace", "set-default"]]
    assert default and default[0][-1] == "главный"


# --- Эмбеддер доезжает до конфигурации ---
#
# Регрессия с живого прогона: `reindex --embedder gemini` строит векторы, но
# решение «искать по смыслу или по тексту» MCP-эндпоинт принимает по
# КОНФИГУРАЦИИ репозитория. Без `--embedder` у `init` там остаётся `mock`, и
# эндпоинт отвечает `semantic_search: false` и пустым результатом — при
# построенных векторах и оплаченных вызовах эмбеддинга.

def test_init_carries_the_embedder(idx, tmp_path, monkeypatch):
    _two_repo_workspace(idx, tmp_path)
    made = []
    monkeypatch.setattr(idx, "head_sha", lambda p: "sha")
    monkeypatch.setattr(idx, "run", lambda args, cwd=None, check=True: made.append(args))
    idx.PROVIDER = ""
    idx.EMBEDDER = "gemini"
    idx.bootstrap("product")

    init = [a for a in made if a[:2] == ["repowise", "init"]][0]
    assert "--embedder" in init and "gemini" in init


def test_bootstrap_builds_vectors_when_embedder_set(idx, tmp_path, monkeypatch):
    _two_repo_workspace(idx, tmp_path)
    made = []
    monkeypatch.setattr(idx, "head_sha", lambda p: "sha")
    monkeypatch.setattr(idx, "run", lambda args, cwd=None, check=True: made.append(args))
    idx.PROVIDER = ""
    idx.EMBEDDER = "gemini"
    idx.bootstrap("product")

    assert any(a[:2] == ["repowise", "reindex"] for a in made)


def test_no_vectors_without_embedder(idx, tmp_path, monkeypatch):
    # Без эмбеддера векторы не строятся вовсе: платить за вызовы, которых
    # никто не просил, незачем.
    _two_repo_workspace(idx, tmp_path)
    made = []
    monkeypatch.setattr(idx, "head_sha", lambda p: "sha")
    monkeypatch.setattr(idx, "run", lambda args, cwd=None, check=True: made.append(args))
    idx.PROVIDER = idx.EMBEDDER = ""
    idx.bootstrap("product")

    assert not any(a[:2] == ["repowise", "reindex"] for a in made)
    assert not any("--embedder" in a for a in made)
