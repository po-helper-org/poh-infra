"""Имена полей, занятые клиентом.

OpenHands строит на каждый MCP-инструмент свой pydantic-класс действия, и имя
`kind` у него занято под тип события. Инструмент, объявивший в схеме
собственный `kind`, роняет весь прогон агента на первом же ходу — причём с
НУЛЕВЫМ кодом возврата: снаружи отказ неотличим от исправной работы. У Repowise
таких инструмента два: `get_dead_code` и `search_codebase`.

Проверяем обе стороны подмены: клиент видит переименованное поле, а наверх
уходит имя, которое Repowise объявлял сам. Схему по существу не разбираем —
прокси не должен знать, что эти поля значат, иначе привяжется к версии
стороннего пакета.

Запуск:
    python -m pytest repowise/tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "proxy"))


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOWISE_AGENT_TOKEN", "tok-test")
    monkeypatch.setenv("REPOWISE_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("REPOWISE_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    for module in [m for m in list(sys.modules) if m == "proxy"]:
        del sys.modules[module]
    import proxy as module
    return module


def _tools_body(properties: dict, required: list | None = None) -> bytes:
    tool = {"name": "search_codebase",
            "inputSchema": {"type": "object", "properties": properties}}
    if required is not None:
        tool["inputSchema"]["required"] = required
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": [tool]}}
    return f"event: message\ndata: {json.dumps(payload)}\n\n".encode("utf-8")


def _tools_of(body: bytes) -> list:
    for line in body.decode("utf-8").splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())["result"]["tools"]
    raise AssertionError("в теле нет data-кадра")


def test_agent_taken_from_session_name(app):
    assert app._agent_of("rw-openhands-o__r-56") == "openhands"
    assert app._agent_of("rw-analysis-o__r-56") == "analysis"
    assert app._agent_of("имя не по нашему правилу") == ""


def test_reserved_field_renamed_for_the_client(app):
    body, touched = app._rename_in_schemas(
        _tools_body({"kind": {"type": "string"}, "limit": {"type": "number"}}),
        {"kind"})
    props = _tools_of(body)[0]["inputSchema"]["properties"]
    assert "kind" not in props and "kind_" in props
    assert props["limit"] == {"type": "number"}, "соседнее поле тронуто"
    assert touched == ["search_codebase.kind"]


def test_required_follows_the_rename(app):
    # Иначе клиент обязан прислать поле, которого в схеме уже нет.
    body, _ = app._rename_in_schemas(
        _tools_body({"kind": {"type": "string"}}, required=["kind"]), {"kind"})
    assert _tools_of(body)[0]["inputSchema"]["required"] == ["kind_"]


def test_tool_without_reserved_field_untouched(app):
    original = _tools_body({"limit": {"type": "number"}})
    body, touched = app._rename_in_schemas(original, {"kind"})
    assert touched == []
    assert _tools_of(body) == _tools_of(original)


def test_rename_skipped_when_both_names_taken(app):
    # Слить два поля в одно значило бы потерять одно из них молча. Пусть
    # падение будет заметным.
    body, touched = app._rename_in_schemas(
        _tools_body({"kind": {"type": "string"}, "kind_": {"type": "string"}}),
        {"kind"})
    assert touched == []
    assert "kind" in _tools_of(body)[0]["inputSchema"]["properties"]


def test_argument_name_restored_before_upstream(app):
    call = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "search_codebase",
                                  "arguments": {"kind_": "unused_export",
                                                "limit": 5}}}).encode("utf-8")
    restored = json.loads(app._restore_in_arguments(call, {"kind"}))
    assert restored["params"]["arguments"] == {"kind": "unused_export", "limit": 5}


def test_other_methods_pass_through_untouched(app):
    body = json.dumps({"jsonrpc": "2.0", "id": 3,
                       "method": "tools/list"}).encode("utf-8")
    assert json.loads(app._restore_in_arguments(body, {"kind"})) == json.loads(body)


def test_unparsable_body_returned_as_is(app):
    # Неожиданный формат — не повод отдать пустой перечень инструментов.
    raw = "не json вовсе".encode("utf-8")
    body, touched = app._rename_in_schemas(raw, {"kind"})
    assert touched == []
    assert body == raw
