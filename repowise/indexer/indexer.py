"""Индексатор Repowise — поддерживает индекс актуальным без участия человека.

Два режима, один контейнер:

`bootstrap` — первичная индексация: клонирование репозиториев из списка состава,
структурный индекс без модели, затем отдельным шагом проза моделью.

`sync` — постоянный цикл: получить изменения, подхватить новые репозитории,
инкрементально обновить устаревшие, записать отметку синхронизации.

**Почему проза отдельным шагом.** `repowise init --no-prose` проходит без модели
и без трат гарантированно и даёт структурный индекс. Генерация страниц моделью —
отдельная стоимость и отдельный источник отказов (провайдер, эмбеддер, лимиты).
Разделение оставляет работоспособный индекс даже при неудаче второго шага, а не
половину результата.

**Почему отметка синхронизации пишется файлом.** Устаревший индекс опаснее
отсутствующего: агент уверенно ответит про код, которого больше нет. Опасность
создаёт невидимость устаревания, поэтому момент и SHA обязаны быть доступны
прокси, а не лежать в логах этого контейнера.

**Про эмбеддер.** У z.ai эмбеддингов нет (спайк FR-1): без отдельного ключа
семантический поиск деградирует до полнотекстового, а структурный индекс, граф,
история и оценка риска работают в полном объёме. Отсутствие `REPOWISE_EMBEDDER`
поэтому не отказ, а режим — но он должен быть виден в логе, а не подразумеваться.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

WORKSPACES_ROOT = Path(os.environ.get("REPOWISE_WORKSPACES_ROOT", "/workspaces"))
CONFIG_ROOT = Path(os.environ.get("REPOWISE_CONFIG_ROOT", "/config"))
SYNC_INTERVAL_SEC = int(os.environ.get("REPOWISE_SYNC_INTERVAL", "3600"))
GIT_TOKEN = os.environ.get("REPOWISE_GIT_TOKEN", "")
PROVIDER = os.environ.get("REPOWISE_PROVIDER", "").strip()
MODEL = os.environ.get("REPOWISE_MODEL", "").strip()
EMBEDDER = os.environ.get("REPOWISE_EMBEDDER", "").strip()

# Общие исключения индексации. `.claude/worktrees/` — критично: в
# poh-issue-agents лежат рабочие копии его самого, и без исключения индекс
# кратно вырастет, а поиск начнёт возвращать устаревшие копии как
# самостоятельные файлы.
EXCLUDES = ["**/.venv/**", "**/node_modules/**", "**/__pycache__/**",
            "**/.claude/worktrees/**", "**/.git/**"]

# Файлы окружения в индекс не попадают: в рабочих копиях лежат действующие
# ключи. Guard явный, а не надежда на дефолты стороннего пакета.
SECRET_GLOBS = ["**/.env", "**/.env.*", "**/*.pem", "**/secrets/**"]


def log(message: str) -> None:
    print(f"[indexer] {message}", flush=True)


def run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = ((result.stdout or "") + (result.stderr or ""))[-800:]
        if check:
            raise RuntimeError(f"{' '.join(args[:3])}… → код {result.returncode}: {tail}")
        log(f"неудача (не критично): {' '.join(args[:3])}… {tail[:200]}")
    return result


def load_workspace(name: str) -> list[dict]:
    path = CONFIG_ROOT / f"{name}.yml"
    if not path.exists():
        raise RuntimeError(f"нет списка состава {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("repos", [])


def clone_url(full_name: str) -> str:
    # Токен уходит в URL только внутри этого процесса и в argv не попадает:
    # git читает его из stdin-хелпера. Прямая подстановка в argv унесла бы
    # живой токен в текст любого исключения subprocess.
    return f"https://github.com/{full_name}.git"


def git_env() -> dict:
    env = {**os.environ}
    if GIT_TOKEN:
        env["GIT_ASKPASS"] = "/usr/local/bin/git-askpass"
        env["REPOWISE_GIT_TOKEN"] = GIT_TOKEN
    return env


def ensure_clone(workspace: str, entry: dict) -> Path | None:
    alias = entry.get("alias") or entry["repo"].split("/")[-1]
    target = WORKSPACES_ROOT / workspace / alias
    if (target / ".git").exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    log(f"клонирую {entry['repo']} → {workspace}/{alias}")
    # --filter=blob:none: история для blame нужна, содержимое прошлых ревизий —
    # нет. На четырнадцати репозиториях это разница в разы по диску.
    result = subprocess.run(
        ["git", "clone", "--filter=blob:none", clone_url(entry["repo"]), str(target)],
        capture_output=True, text=True, env=git_env())
    if result.returncode != 0:
        # Один недоступный репозиторий не должен ронять индексацию остальных.
        log(f"НЕ склонирован {entry['repo']}: {(result.stderr or '')[-300:]}")
        return None
    return target


def head_sha(repo_dir: Path) -> str:
    result = run(["git", "rev-parse", "HEAD"], cwd=repo_dir, check=False)
    return (result.stdout or "").strip()


def set_default_repo(root: Path, alias: str) -> None:
    """Назначить репозиторий по умолчанию — тот, на который падает запрос без alias.

    `repowise init` на корне назначает умолчанием ПЕРВЫЙ ПО АЛФАВИТУ репозиторий
    (проверено: на contour им оказался `poh-bft-writer`). Правило R4 требует
    другого: умолчанием должен быть первый в списке состава, выбранный
    осознанно, — иначе вопрос без alias уедет в случайный репозиторий, и агент
    об этом не узнает.
    """
    run(["repowise", "workspace", "set-default", alias], cwd=root, check=False)


def write_sync_state(repo_dir: Path) -> None:
    state_dir = repo_dir / ".repowise"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "sync-state.json").write_text(
        json.dumps({"sha": head_sha(repo_dir), "synced_at": time.time()},
                   ensure_ascii=False),
        encoding="utf-8")


def scrub_secrets(repo_dir: Path) -> int:
    """Убрать из рабочей копии файлы, которым в индексе не место.

    Именно убрать, а не положиться на исключения индексации: пакет сторонний,
    его дефолты меняются между версиями, а ключ, попавший в индекс, оттуда уже
    не вынуть — он размажется по страницам вики и по векторному индексу.
    """
    removed = 0
    for pattern in SECRET_GLOBS:
        for path in repo_dir.glob(pattern):
            if path.is_file():
                path.unlink()
                removed += 1
    return removed


def index_args() -> list[str]:
    args = []
    if PROVIDER:
        args += ["--provider", PROVIDER]
    if MODEL:
        args += ["--model", MODEL]
    if EMBEDDER:
        args += ["--embedder", EMBEDDER]
    return args


def bootstrap(workspace: str) -> None:
    entries = load_workspace(workspace)
    if not entries:
        log(f"{workspace}: список состава пуст — пропускаю")
        return
    cloned: list[Path] = []
    for entry in entries:
        path = ensure_clone(workspace, entry)
        if path:
            removed = scrub_secrets(path)
            if removed:
                log(f"{path.name}: убрано файлов с секретами — {removed}")
            cloned.append(path)
    if not cloned:
        raise RuntimeError(f"{workspace}: не склонирован ни один репозиторий")

    root = WORKSPACES_ROOT / workspace
    primary = cloned[0]
    excludes = [a for pattern in EXCLUDES for a in ("-x", pattern)]

    # `init` НА КОРНЕ, а не в первичном репозитории. Корень с несколькими
    # репозиториями repowise распознаёт сам: создаёт `.repowise-workspace.yaml`
    # и индексирует все найденные разом.
    #
    # Прежний порядок (init внутри первичного, затем `workspace add` на
    # остальные) не работает вовсе: `workspace add` требует уже созданного
    # workspace и падает с «No .repowise-workspace.yaml found». На workspace из
    # ОДНОГО репозитория это не всплывает — `workspace add` там не вызывается,
    # и ошибка ждёт первого же второго репозитория.
    log(f"{workspace}: структурный индекс на корне, репозиториев {len(cloned)} (без модели)")
    run(["repowise", "init", "--no-prose", "-y", *excludes, str(root)], cwd=root)

    set_default_repo(root, primary.name)
    for repo in cloned:
        write_sync_state(repo)

    if PROVIDER:
        log(f"{workspace}: проза моделью {PROVIDER}/{MODEL or 'по умолчанию'}")
        run(["repowise", "generate", "--unwritten", "-y", *index_args()],
            cwd=root, check=False)
    else:
        log(f"{workspace}: REPOWISE_PROVIDER не задан — вики остаётся структурной")

    if not EMBEDDER:
        log("REPOWISE_EMBEDDER не задан: семантический поиск недоступен, "
            "полнотекстовый работает (см. docs/repowise/runbook.md)")


def sync(workspace: str) -> None:
    root = WORKSPACES_ROOT / workspace
    if not root.exists():
        log(f"{workspace}: каталога нет — нужен bootstrap")
        return
    entries = load_workspace(workspace)
    for entry in entries:
        ensure_clone(workspace, entry)

    repos = [p for p in sorted(root.glob("*")) if (p / ".git").exists()]
    if not repos:
        return
    # Порядок из списка состава, а не из каталога: умолчанием должен остаться
    # тот же репозиторий, что и при первичной индексации (правило R4).
    order = [e.get("alias") or e["repo"].split("/")[-1] for e in entries]
    repos.sort(key=lambda p: order.index(p.name) if p.name in order else len(order))
    primary = repos[0]

    for repo in repos:
        before = head_sha(repo)
        result = subprocess.run(["git", "fetch", "--prune", "origin"], cwd=repo,
                                capture_output=True, text=True, env=git_env())
        if result.returncode != 0:
            log(f"{repo.name}: fetch не удался — {(result.stderr or '')[-200:]}")
            continue
        run(["git", "reset", "--hard", "origin/HEAD"], cwd=repo, check=False)
        removed = scrub_secrets(repo)
        after = head_sha(repo)
        if before != after:
            log(f"{repo.name}: {before[:8]} → {after[:8]}"
                + (f", убрано секретов {removed}" if removed else ""))
        write_sync_state(repo)

    log(f"{workspace}: подхватываю новые репозитории")
    run(["repowise", "workspace", "scan", "-y"], cwd=root, check=False)
    set_default_repo(root, primary.name)
    log(f"{workspace}: инкрементальное обновление")
    run(["repowise", "update", "-w", *index_args()], cwd=root, check=False)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "sync"
    names = [n.strip() for n in os.environ.get("REPOWISE_WORKSPACES", "contour,product").split(",")
             if n.strip()]

    if mode == "bootstrap":
        for name in names:
            bootstrap(name)
        log("первичная индексация завершена")
        return 0

    if mode == "sync-once":
        for name in names:
            sync(name)
        return 0

    log(f"цикл синхронизации, интервал {SYNC_INTERVAL_SEC} с, workspace: {names}")
    while True:
        for name in names:
            try:
                sync(name)
            except Exception as exc:
                # Отказ по одному workspace не должен останавливать цикл:
                # молчащий индексатор хуже отставшего индекса, потому что
                # отставание видно, а остановка — нет.
                log(f"{name}: цикл не удался — {exc}")
        time.sleep(SYNC_INTERVAL_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
