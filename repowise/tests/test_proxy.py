"""Контракт MCP-прокси.

Проверяем ровно то, ради чего прокси и заведён: он не пускает без токена и без
сессии, ведёт журнал, из которого транскрипт получается механически, и не
разбирает семантику инструментов — иначе привязался бы к версии стороннего
пакета и требовал правки на каждое её обновление.

Сеть не трогаем: обращения к MCP-эндпоинту подменяются.

Запуск:
    pip install fastapi httpx pytest
    python -m pytest repowise/tests -q
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "proxy"))

TOKEN = "tok-test"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOWISE_AGENT_TOKEN", TOKEN)
    monkeypatch.setenv("REPOWISE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("REPOWISE_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    for module in [m for m in list(sys.modules) if m == "proxy"]:
        del sys.modules[module]
    import proxy as module
    return module


@pytest.fixture
def client(app):
    return TestClient(app.app)


def _auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


# --- Разграничение доступа ---

def test_health_open_without_token(client):
    # Открыта намеренно: иначе отличить «прокси лежит» от «токен протух»
    # станет нельзя, а на эту точку опирается ветвь деградации у агента.
    assert client.get("/health").status_code == 200


def test_mcp_rejects_without_token(client):
    r = client.post("/mcp", params={"workspace": "contour", "session": "s1"})
    assert r.status_code == 401


def test_mcp_rejects_wrong_token(client):
    r = client.post("/mcp", params={"workspace": "contour", "session": "s1"},
                    # Заголовки HTTP — latin-1, поэтому токен здесь ASCII.
                    headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401


def test_mcp_rejects_without_session(client):
    # Артефакт рендерится из журнала; обмен без сессии восстановить нельзя.
    r = client.post("/mcp", params={"workspace": "contour"}, headers=_auth())
    assert r.status_code == 400


def test_mcp_rejects_unknown_workspace(client):
    r = client.post("/mcp", params={"workspace": "неизвестный", "session": "s1"},
                    headers=_auth())
    assert r.status_code == 400


def test_empty_token_closes_the_door(app, monkeypatch):
    # Пустой токен в конфигурации — НЕ «пускать всех». За прокси клоны всех
    # репозиториев организации.
    monkeypatch.setattr(app, "AGENT_TOKEN", "")
    with TestClient(app.app) as c:
        r = c.post("/mcp", params={"workspace": "contour", "session": "s1"},
                   headers=_auth())
    assert r.status_code == 503


def test_session_id_cannot_escape_the_directory(app):
    # Имя сессии приходит от агента и уезжает в имя файла.
    path = app._journal_path("../../etc/passwd")
    assert path.parent == app.SESSIONS_DIR
    assert ".." not in path.name


# --- Разбор конверта, но не семантики ---

def test_describe_request_reads_envelope_only(app):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "get_overview",
                                  "arguments": {"repo": "poh-core"}}}).encode()
    described = app._describe_request(body)
    assert described["method"] == "tools/call"
    assert described["tool"] == "get_overview"
    assert described["arguments"] == {"repo": "poh-core"}


def test_unknown_tool_needs_no_proxy_change(app):
    # Инструмент, о котором прокси ничего не знает, проходит как любой другой:
    # имена и сигнатуры у стороннего пакета меняются между версиями.
    body = json.dumps({"method": "tools/call",
                       "params": {"name": "инструмент_из_будущего",
                                  "arguments": {"x": 1}}}).encode()
    assert app._describe_request(body)["tool"] == "инструмент_из_будущего"


def test_broken_json_does_not_raise(app):
    # Битый запрос не должен ронять прокси: журнал с пометкой полезнее отказа.
    assert app._describe_request("не json".encode())["method"] == "(не разобран)"


def test_extract_text_from_sse(app):
    payload = {"jsonrpc": "2.0", "id": 1,
               "result": {"content": [{"type": "text", "text": "ответ индекса"}]}}
    body = f"event: message\ndata: {json.dumps(payload)}\n\n".encode()
    assert app._extract_text(body) == "ответ индекса"


def test_extract_text_keeps_error(app):
    payload = {"jsonrpc": "2.0", "id": 1,
               "error": {"code": -32601, "message": "нет такого инструмента"}}
    body = f"data: {json.dumps(payload)}\n".encode()
    assert "нет такого инструмента" in app._extract_text(body)


def test_extract_text_falls_back_to_raw(app):
    # Не разобралось — кладём сырьё: артефакт с сырым ответом полезнее
    # артефакта с пропуском.
    assert "необычный ответ" in app._extract_text("необычный ответ".encode())


# --- Журнал и рендер ---

def _turn(app, session, tool, response, workspace="product"):
    app._append(session, {"ts": 1_760_000_000, "workspace": workspace,
                          "method": "tools/call", "tool": tool,
                          "arguments": {"repo": "poh-demo-checkout"},
                          "response": response, "bytes": len(response)})


def test_render_counts_turns_and_keeps_them_all(app):
    for i in range(3):
        _turn(app, "s1", f"tool{i}", f"ответ {i}")
    text = app._render_markdown("s1")
    assert "turns: 3" in text
    for i in range(3):
        assert f"ответ {i}" in text
    # Число ходов в шапке обязано совпадать с числом ходов в теле: расхождение
    # означало бы транскрипт, которому нельзя верить.
    assert text.count("## Ход ") == 3


def test_render_of_empty_session_says_so(app):
    app._append("s2", {"ts": 1_760_000_000, "method": "(закрытие сессии)"})
    text = app._render_markdown("s2")
    assert "turns: 0" in text
    assert "Обращений к индексу не было" in text


def test_render_of_missing_session_is_404(app):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        app._render_markdown("нет-такой")
    assert exc.value.status_code == 404


def test_broken_journal_line_does_not_break_render(app):
    _turn(app, "s3", "get_overview", "живой ответ")
    with app._journal_path("s3").open("a", encoding="utf-8") as fh:
        fh.write("{битая строка\n")
    text = app._render_markdown("s3")
    assert "живой ответ" in text


def test_failed_calls_appear_in_transcript(app):
    # Диалог, в котором половина вопросов не дошла, должен выглядеть именно
    # так, а не как короткий диалог.
    _turn(app, "s4", "get_overview", "ответ")
    app._append("s4", {"ts": 1_760_000_001, "error": "эндпоинт недоступен: timeout"})
    text = app._render_markdown("s4")
    assert "Сбои обращений" in text
    assert "timeout" in text


def test_index_age_reaches_the_header(app, tmp_path):
    repo = tmp_path / "workspaces" / "product" / "poh-demo-checkout" / ".repowise"
    repo.mkdir(parents=True)
    (repo / "sync-state.json").write_text(
        json.dumps({"sha": "2e7c62aa955e7030", "synced_at": 1_760_000_000}),
        encoding="utf-8")
    _turn(app, "s5", "get_overview", "ответ")
    text = app._render_markdown("s5")
    # Возраст индекса в шапке — не украшение: устаревший индекс опаснее
    # отсутствующего, и опасность создаёт именно невидимость устаревания.
    assert "Свежесть индекса" in text
    assert "2e7c62aa955e" in text


def test_service_methods_are_not_turns(app, client, monkeypatch):
    """Рукопожатие и перечень инструментов — подготовка, а не диалог.

    В артефакте они дали бы шум на каждый прогон и сместили счётчик ходов.
    """
    class FakeResponse:
        status_code = 200
        content = b'data: {"jsonrpc":"2.0","id":1,"result":{}}\n'
        headers = {"content-type": "text/event-stream"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResponse()

    monkeypatch.setattr(app.httpx, "AsyncClient", lambda **k: FakeClient())

    client.post("/mcp", params={"workspace": "product", "session": "s6"},
                headers=_auth(),
                content=json.dumps({"method": "initialize"}))
    client.post("/mcp", params={"workspace": "product", "session": "s6"},
                headers=_auth(),
                content=json.dumps({"method": "tools/list"}))
    client.post("/mcp", params={"workspace": "product", "session": "s6"},
                headers=_auth(),
                content=json.dumps({"method": "tools/call",
                                    "params": {"name": "get_overview", "arguments": {}}}))

    assert "turns: 1" in app._render_markdown("s6")


def test_unreachable_endpoint_is_journaled_and_reported(app, client, monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise OSError("соединение отклонено")

    monkeypatch.setattr(app.httpx, "AsyncClient", lambda **k: FakeClient())
    r = client.post("/mcp", params={"workspace": "product", "session": "s7"},
                    headers=_auth(),
                    content=json.dumps({"method": "tools/call",
                                        "params": {"name": "get_overview"}}))
    assert r.status_code == 502
    # Сбой обязан попасть в журнал: иначе диалог выглядел бы просто коротким.
    assert "соединение отклонено" in app._render_markdown("s7")


def test_token_never_lands_in_transcript(app, client, monkeypatch):
    class FakeResponse:
        status_code = 200
        content = b'data: {"result":{"content":[{"type":"text","text":"ok"}]}}\n'
        headers = {"content-type": "text/event-stream"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResponse()

    monkeypatch.setattr(app.httpx, "AsyncClient", lambda **k: FakeClient())
    client.post("/mcp", params={"workspace": "product", "session": "s8"},
                headers=_auth(),
                content=json.dumps({"method": "tools/call",
                                    "params": {"name": "get_overview", "arguments": {}}}))
    assert TOKEN not in app._render_markdown("s8")


# --- Host для MCP-эндпоинта ---
#
# Регрессия, найденная сквозной проверкой. У MCP-сервера включена защита от
# DNS-rebinding: проксированный запрос с Host вида `repowise-product-mcp:7338`
# он отвергает кодом 421 «Invalid Host header». Эндпоинт при этом выглядит
# полностью исправным и отвечает на каждый запрос — просто не по делу, и в
# артефакте диалога это читается как ответ индекса «не могу».

def test_loopback_host_keeps_the_port(app):
    # Проверено на живом сервере: голый `localhost` он отвергает так же, как
    # имя контейнера. Принимается ТОЛЬКО с портом.
    assert app._loopback_host("http://repowise-product-mcp:7338/mcp") == "localhost:7338"


def test_loopback_host_without_port(app):
    assert app._loopback_host("http://repowise-product-mcp/mcp") == "localhost"


def test_upstream_gets_loopback_host(app, client, monkeypatch):
    seen = {}

    class FakeResponse:
        status_code = 200
        content = b'data: {"result":{"content":[{"type":"text","text":"ok"}]}}\n'
        headers = {"content-type": "text/event-stream"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            seen["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(app.httpx, "AsyncClient", lambda **k: FakeClient())
    client.post("/mcp", params={"workspace": "product", "session": "s9"},
                headers=_auth(),
                content=json.dumps({"method": "tools/call",
                                    "params": {"name": "get_overview"}}))
    assert seen["headers"]["host"] == "localhost:7338"
