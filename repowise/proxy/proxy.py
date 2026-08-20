"""MCP-прокси Repowise — единственный вход к индексу из контура производства.

Три обязанности, и все три — следствия конкретных решений спеки FNR-5
(`po-helper-org/poh-issue-agents`, `sa_documentation/FNR/FNR_5/`).

**Маршрутизация по workspace.** Workspace repowise — это один корневой каталог,
один репозиторий по умолчанию и один MCP-эндпоинт; два workspace означают два
контейнера на разных портах. Знать про оба должен кто-то один, и это прокси, а
не каждый агент.

**Разграничение доступа.** MCP-эндпоинты наружу не публикуются вовсе: «только
внутренний контур» обеспечивается сетью, а не соглашением. Прокси — тоже
внутренний, но он единственный виден контуру, поэтому требует токен. Точка
`/health` открыта: проверка живости не должна падать из-за неверного токена,
иначе отличить «прокси лежит» от «токен протух» станет нельзя.

**Журнал обмена.** Из него рендерится артефакт `repowise-dialog.md`. Записью
силами модели этот артефакт не получить: guard стадии на той стороне умеет
проверить только существование файла и его размер, и отличить полный транскрипт
от правдоподобного пересказа ему не с чем. Журнал делает полноту свойством
построения. Он же решает вопрос для агента разработки, у которого хуков нет.

Прокси НЕ разбирает семантику инструментов — только конверт JSON-RPC: имя
метода и имя инструмента. Имена и сигнатуры инструментов у стороннего пакета
меняются между версиями, и привязка к ним означала бы правку прокси на каждое
обновление. Полезная нагрузка проходит насквозь без изменений.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response

# --- Конфигурация ---

# Адреса MCP-эндпоинтов по имени workspace. Разделение на contour и product —
# граница вопроса, не команды: «как устроены сами агенты» против «что агенты
# пишут» (правила R1–R9, docs/repowise/workspaces.md).
WORKSPACES = {
    "contour": os.environ.get("REPOWISE_MCP_CONTOUR", "http://repowise-contour-mcp:7338/mcp"),
    "product": os.environ.get("REPOWISE_MCP_PRODUCT", "http://repowise-product-mcp:7338/mcp"),
}

AGENT_TOKEN = os.environ.get("REPOWISE_AGENT_TOKEN", "")
SESSIONS_DIR = Path(os.environ.get("REPOWISE_SESSIONS_DIR", "/sessions"))
WORKSPACES_ROOT = Path(os.environ.get("REPOWISE_WORKSPACES_ROOT", "/workspaces"))
SESSION_TTL_DAYS = int(os.environ.get("REPOWISE_SESSION_TTL_DAYS", "30"))
UPSTREAM_TIMEOUT_SEC = float(os.environ.get("REPOWISE_UPSTREAM_TIMEOUT_SEC", "120"))

# Служебные методы MCP, которые в транскрипт не идут: рукопожатие и перечень
# инструментов — это не диалог, а его подготовка. В артефакте они дали бы шум
# на каждый прогон и сместили счётчик ходов.
SERVICE_METHODS = {"initialize", "notifications/initialized", "tools/list",
                   "prompts/list", "resources/list", "ping"}

# Имена полей, которые клиент занял под своё и в схеме инструмента принять не
# может.
#
# OpenHands строит на каждый MCP-инструмент свой pydantic-класс действия, а имя
# `kind` у него занято под тип события. Инструмент, объявивший в схеме
# собственный `kind`, роняет не себя, а весь прогон: агент падает
# `TypeError: Field 'kind' ... overrides symbol of same name in a parent class`
# на первом же ходу — и выходит с НУЛЕВЫМ кодом, не тронув ни одного файла.
# Снаружи это неотличимо от исправной работы; на стенде так сгорел прогон
# разработки по issue #56. У Repowise таких инструмента два: `get_dead_code` и
# `search_codebase`.
#
# Поле ПЕРЕИМЕНОВЫВАЕТСЯ, а не выбрасывается вместе с инструментом: без
# `search_codebase` агент разработки теряет главный способ искать по индексу —
# то есть смысл всей интеграции. Клиент видит `kind_`, вызов с ним прокси
# переводит обратно, и сам Repowise ни о какой подмене не знает.
#
# Правило заведено по имени ПОЛЯ, а не инструмента: инструменты переименуют с
# версией пакета, а причина останется прежней. И только тому агенту, который на
# этом падает, — остальным перечень достаётся нетронутым.
CLIENT_RESERVED_FIELDS = {"openhands": {"kind"}}

# Чем дополняется занятое имя. Хвост, а не приставка: имя с ведущим
# подчёркиванием pydantic считает приватным и в схему не пустит вовсе.
RENAME_SUFFIX = "_"

app = FastAPI(title="repowise-proxy")


# --- Журнал ---

def _journal_path(session: str) -> Path:
    # Имя сессии приходит от агента и уезжает в имя файла. Разделители пути в
    # нём означали бы запись куда угодно по файловой системе.
    #
    # Точка тоже отброшена, хотя одной её для выхода из каталога мало: имя
    # `....etcpasswd` безопасно, но выглядит как попытка обхода, и следующий
    # человек потратит время, выясняя, обход это или нет. Идентификаторы сессий
    # точек не содержат (`rw-<agent>-<owner>__<repo>-<n>`), так что запрет
    # ничего не стоит.
    safe = "".join(c for c in session if c.isalnum() or c in "-_")
    if not safe:
        raise HTTPException(400, "session пуст после нормализации")
    return SESSIONS_DIR / f"{safe}.jsonl"


def _append(session: str, record: dict) -> None:
    path = _journal_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_journal(session: str) -> list[dict]:
    path = _journal_path(session)
    if not path.exists():
        raise HTTPException(404, f"сессии {session} нет")
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # Битая строка не должна ронять рендер целиком: артефакт с
                # пропуском полезнее отсутствующего артефакта.
                continue
    return records


# --- Разбор конверта JSON-RPC ---

def _loopback_host(upstream_url: str) -> str:
    """Значение Host, которое MCP-эндпоинт согласен принять.

    У сервера включена защита от DNS-rebinding: он сверяет Host со списком
    разрешённых. Проксированный запрос приходит с `repowise-product-mcp:7338` и
    отвергается кодом `421 Invalid Host header` — эндпоинт при этом выглядит
    полностью исправным и отвечает на каждый запрос, просто не по делу.

    Проверено на живом сервере: принимается **только `localhost` С ПОРТОМ**.
    Голый `localhost` и `127.0.0.1` отвергаются так же, как имя контейнера, —
    поэтому порт берётся из адреса эндпоинта, а не зашивается.

    Подмена ничего не ослабляет: соединение устанавливается по адресу из URL, а
    сама защита рассчитана на браузер, которого здесь нет. Доступ у нас
    разграничивают сеть compose и токен прокси.
    """
    port = urllib.parse.urlsplit(upstream_url).port
    return f"localhost:{port}" if port else "localhost"


def _describe_request(body: bytes) -> dict:
    """Имя метода и инструмента из запроса. Аргументы — как есть, без разбора."""
    try:
        payload = json.loads(body)
    except Exception:
        return {"method": "(не разобран)", "tool": None, "arguments": None}
    method = payload.get("method", "(без метода)")
    params = payload.get("params") or {}
    return {"method": method,
            "tool": params.get("name") if method == "tools/call" else None,
            "arguments": params.get("arguments") if method == "tools/call" else None}


def _extract_text(body: bytes) -> str:
    """Текст ответа MCP. Транспорт — SSE, полезная нагрузка — JSON-RPC внутри.

    Разбираем ровно настолько, чтобы получить читаемый текст в транскрипт. Не
    разобралось — кладём сырьё: артефакт с сырым ответом полезнее артефакта с
    пропуском.
    """
    raw = body.decode("utf-8", errors="replace")
    chunks = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            chunks.append(line[len("data:"):].strip())
    payloads = chunks or [raw]
    out = []
    for chunk in payloads:
        try:
            data = json.loads(chunk)
        except Exception:
            out.append(chunk)
            continue
        result = data.get("result")
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            for item in result["content"]:
                if isinstance(item, dict) and item.get("type") == "text":
                    out.append(item.get("text", ""))
        elif "error" in data:
            err = data["error"]
            out.append(f"ОШИБКА {err.get('code')}: {err.get('message')}")
        else:
            out.append(chunk)
    return "\n".join(t for t in out if t)


def _agent_of(session: str) -> str:
    """Агент, которому принадлежит сессия. Имя строит контур: `rw-<агент>-…`."""
    parts = session.split("-")
    return parts[1] if len(parts) > 2 and parts[0] == "rw" else ""


def _map_sse(body: bytes, transform) -> bytes:
    """Применить преобразование к JSON-RPC внутри тела.

    Транспорт — SSE (`data: {…}`), но у ответа на запрос без потока его может и
    не быть. Не разобралось — отдаём тело как есть: лучше нетронутый ответ, чем
    испорченный попыткой починить.
    """
    raw = body.decode("utf-8", errors="replace")

    def one(chunk: str) -> str:
        try:
            data = json.loads(chunk)
        except Exception:
            return chunk
        changed = transform(data)
        return json.dumps(changed, ensure_ascii=False) if changed is not None else chunk

    if "data:" not in raw:
        return one(raw).encode("utf-8")

    out = []
    for line in raw.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped.startswith("data:"):
            ending = line[len(stripped):]
            out.append("data: " + one(stripped[len("data:"):].strip()) + ending)
        else:
            out.append(line)
    return "".join(out).encode("utf-8")


def _rename_in_schemas(body: bytes, reserved: set[str]) -> tuple[bytes, list[str]]:
    """Переименовать занятые клиентом поля в перечне инструментов."""
    touched: list[str] = []

    def transform(data):
        result = data.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return None
        hit = False
        for tool in result["tools"]:
            schema = (tool or {}).get("inputSchema")
            if not isinstance(schema, dict):
                continue
            props = schema.get("properties")
            if not isinstance(props, dict):
                continue
            for name in sorted(reserved & set(props)):
                new = name + RENAME_SUFFIX
                if new in props:
                    # Оба имени заняты — молча слить их значило бы потерять
                    # одно из полей. Оставляем инструмент как есть: пусть
                    # падение будет заметным, а не тихим.
                    continue
                props[new] = props.pop(name)
                required = schema.get("required")
                if isinstance(required, list):
                    schema["required"] = [new if r == name else r for r in required]
                touched.append(f"{tool.get('name', '(без имени)')}.{name}")
                hit = True
        return data if hit else None

    return _map_sse(body, transform), touched


def _restore_in_arguments(body: bytes, reserved: set[str]) -> bytes:
    """Вернуть исходные имена в аргументах вызова.

    Обратная сторона переименования: наверх уходит то, что Repowise объявлял
    сам. Иначе агент звал бы инструмент с полем, которого у сервера нет.
    """
    def transform(data):
        if data.get("method") != "tools/call":
            return None
        arguments = (data.get("params") or {}).get("arguments")
        if not isinstance(arguments, dict):
            return None
        hit = False
        for name in sorted(reserved):
            alias = name + RENAME_SUFFIX
            if alias in arguments and name not in arguments:
                arguments[name] = arguments.pop(alias)
                hit = True
        return data if hit else None

    return _map_sse(body, transform)


# --- Возраст индекса ---

def _index_age() -> list[dict]:
    """Возраст индекса по каждому репозиторию обоих workspace.

    Устаревший индекс опаснее отсутствующего: агент уверенно ответит про код,
    которого больше нет. Опасность создаёт не устаревание само по себе, а его
    невидимость, — поэтому возраст обязан быть доступен потребителю, а не
    лежать в логах индексатора.
    """
    rows = []
    for workspace_dir in sorted(WORKSPACES_ROOT.glob("*")):
        if not workspace_dir.is_dir():
            continue
        for repo_dir in sorted(workspace_dir.glob("*")):
            state = repo_dir / ".repowise" / "sync-state.json"
            if not state.exists():
                continue
            try:
                data = json.loads(state.read_text(encoding="utf-8"))
            except Exception:
                continue
            synced = float(data.get("synced_at", 0))
            rows.append({
                "workspace": workspace_dir.name,
                "alias": repo_dir.name,
                "sha": data.get("sha", ""),
                "synced_at": synced,
                "age_sec": round(time.time() - synced) if synced else None,
            })
    return rows


# --- Точки входа ---

@app.get("/health")
def health() -> dict:
    """Открыта намеренно: см. докстроку модуля."""
    return {"status": "ok", "workspaces": sorted(WORKSPACES)}


@app.get("/index-age")
def index_age(authorization: str = Header(default="")) -> dict:
    _require_token(authorization)
    rows = _index_age()
    stale = [r for r in rows if r["age_sec"] is not None
             and r["age_sec"] > int(os.environ.get("REPOWISE_STALE_AFTER_SEC", "7200"))]
    return {"repos": rows, "stale": [r["alias"] for r in stale]}


def _require_token(authorization: str) -> None:
    if not AGENT_TOKEN:
        # Пустой токен в конфигурации — не «пускать всех», а отказ. Молча
        # открытый вход к коду всех репозиториев организации хуже упавшего.
        raise HTTPException(503, "REPOWISE_AGENT_TOKEN не задан — вход закрыт")
    if authorization != f"Bearer {AGENT_TOKEN}":
        raise HTTPException(401, "неверный или отсутствующий токен")


@app.post("/mcp")
async def mcp(request: Request, workspace: str = "", session: str = "",
              authorization: str = Header(default="")) -> Response:
    _require_token(authorization)
    if workspace not in WORKSPACES:
        raise HTTPException(400, f"неизвестный workspace {workspace!r}; "
                                 f"известны: {sorted(WORKSPACES)}")
    if not session.strip():
        # Обязателен намеренно: артефакт рендерится из журнала, и обмен, не
        # отнесённый к сессии, восстановить нельзя.
        raise HTTPException(400, "параметр session обязателен")

    body = await request.body()
    reserved = CLIENT_RESERVED_FIELDS.get(_agent_of(session), set())
    if reserved:
        body = _restore_in_arguments(body, reserved)
    described = _describe_request(body)
    started = time.time()

    headers = {k: v for k, v in request.headers.items()
               if k.lower() in {"content-type", "accept", "mcp-session-id",
                                "mcp-protocol-version"}}
    headers["host"] = _loopback_host(WORKSPACES[workspace])
    try:
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SEC) as client:
            upstream = await client.post(WORKSPACES[workspace], content=body, headers=headers)
    except Exception as exc:
        _append(session, {"ts": started, "workspace": workspace, **described,
                          "error": f"эндпоинт недоступен: {exc}"})
        raise HTTPException(502, f"MCP-эндпоинт {workspace} недоступен: {exc}") from exc

    content = upstream.content
    if described["method"] == "tools/list" and reserved:
        content, touched = _rename_in_schemas(content, reserved)
        if touched:
            print(f"сессия {session}: поля переименованы под клиента "
                  f"(занято: {', '.join(sorted(reserved))}): "
                  f"{', '.join(touched)}", flush=True)

    if described["method"] not in SERVICE_METHODS:
        _append(session, {
            "ts": started,
            "elapsed_sec": round(time.time() - started, 3),
            "workspace": workspace,
            **described,
            "response": _extract_text(content),
            "bytes": len(content),
        })

    passthrough = {k: v for k, v in upstream.headers.items()
                   if k.lower() in {"content-type", "mcp-session-id", "cache-control"}}
    return Response(content=content, status_code=upstream.status_code,
                    headers=passthrough)


@app.get("/sessions/{session}/render")
def render(session: str, authorization: str = Header(default="")) -> Response:
    _require_token(authorization)
    return Response(content=_render_markdown(session), media_type="text/markdown")


@app.post("/sessions/{session}/close")
def close(session: str, authorization: str = Header(default="")) -> Response:
    """Финализировать сессию и отдать транскрипт.

    Отдельная точка, а не просто рендер: закрытие фиксирует момент завершения в
    журнале, и по нему видно, довёл ли агент диалог до конца либо прогон
    оборвался.
    """
    _require_token(authorization)
    _append(session, {"ts": time.time(), "method": "(закрытие сессии)"})
    return Response(content=_render_markdown(session), media_type="text/markdown")


def _render_markdown(session: str) -> str:
    records = _read_journal(session)
    turns = [r for r in records if r.get("method") == "tools/call"]
    workspaces = sorted({r.get("workspace", "") for r in records if r.get("workspace")})
    started = min((r["ts"] for r in records if "ts" in r), default=0)
    finished = max((r["ts"] for r in records if "ts" in r), default=0)

    ages = {r["alias"]: r for r in _index_age()}
    touched = sorted({str(t.get("arguments", {}).get("repo", "")) for t in turns
                      if isinstance(t.get("arguments"), dict)} - {""})

    head = [
        "---",
        f"session: {session}",
        f"workspace: {', '.join(workspaces) or '—'}",
        f"turns: {len(turns)}",
        f"started: {_stamp(started)}",
        f"finished: {_stamp(finished)}",
        "---",
        "",
        "# Свежесть индекса",
        "",
    ]
    # Возраст индекса — в шапке артефакта, а не только в служебной точке:
    # именно здесь его увидит и человек, и следующая стадия конвейера.
    rows = [ages[a] for a in touched if a in ages] or list(ages.values())
    if rows:
        head += ["| репозиторий | SHA | возраст индекса |", "|---|---|---|"]
        head += [f"| `{r['alias']}` | `{(r['sha'] or '—')[:12]}` | "
                 f"{_human_age(r['age_sec'])} |" for r in rows]
    else:
        head.append("Сведений о синхронизации нет — индексатор ещё не отработал.")

    body = ["", "# Диалог", ""]
    if not turns:
        body.append("Обращений к индексу не было.")
    for i, turn in enumerate(turns, 1):
        args = turn.get("arguments")
        body += [
            f"## Ход {i} · `{turn.get('tool') or '—'}` · {turn.get('workspace', '')}",
            "",
            "**Запрос:**",
            "",
            "```json",
            json.dumps(args, ensure_ascii=False, indent=2) if args is not None else "{}",
            "```",
            "",
            "**Ответ:**",
            "",
            turn.get("response") or turn.get("error") or "(пусто)",
            "",
        ]

    errors = [r for r in records if r.get("error")]
    if errors:
        body += ["# Сбои обращений", ""]
        body += [f"- {_stamp(e['ts'])} — {e['error']}" for e in errors]
        body.append("")

    return "\n".join(head + body)


def _stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else "—"


def _human_age(age_sec: int | None) -> str:
    if age_sec is None:
        return "неизвестен"
    if age_sec < 3600:
        return f"{age_sec // 60} мин"
    if age_sec < 86400:
        return f"{age_sec // 3600} ч"
    return f"{age_sec // 86400} сут"
