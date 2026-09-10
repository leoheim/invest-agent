import json

import pytest

from invest_agent.telegram.client import TelegramClient, TelegramError

TOKEN, CHAT = "tok-SECRETO", "42"


def _client(responses):
    calls = []

    def transport(url, body):
        calls.append((url, body))
        payload = responses.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload).encode()

    client = TelegramClient(TOKEN, CHAT, transport=transport)
    return client, calls


def test_send_message_simples():
    client, calls = _client([{"ok": True}])
    client.send_message("olá")
    url, body = calls[0]
    assert url.endswith(f"/bot{TOKEN}/sendMessage")
    data = json.loads(body)
    assert data == {"chat_id": CHAT, "text": "olá"}


def test_send_message_com_botoes():
    client, calls = _client([{"ok": True}])
    client.send_message("aprovar?", buttons=[("Aprovar", "ap:d1"),
                                             ("Rejeitar", "rj:d1")])
    data = json.loads(calls[0][1])
    teclado = data["reply_markup"]["inline_keyboard"]
    assert teclado == [[{"text": "Aprovar", "callback_data": "ap:d1"},
                        {"text": "Rejeitar", "callback_data": "rj:d1"}]]


def test_get_updates_filtra_chat_autorizado():
    updates = {"ok": True, "result": [
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/status"}},
        {"update_id": 2, "message": {"chat": {"id": 999}, "text": "/kill"}},
        {"update_id": 3, "callback_query": {"id": "cb1", "data": "ap:d1",
                                            "message": {"chat": {"id": 42}}}},
        {"update_id": 4, "callback_query": {"id": "cb2", "data": "ap:d2",
                                            "message": {"chat": {"id": 999}}}},
    ]}
    client, calls = _client([updates])
    out = client.get_updates(offset=7)
    assert [u["update_id"] for u in out] == [1, 3]  # 999 ignorado
    assert "offset=7" in calls[0][0] and "timeout=25" in calls[0][0]
    assert calls[0][1] is None  # GET


def test_answer_callback():
    client, calls = _client([{"ok": True}])
    client.answer_callback("cb1")
    assert calls[0][0].endswith("/answerCallbackQuery")
    assert json.loads(calls[0][1]) == {"callback_query_id": "cb1"}


def test_erro_nao_vaza_token():
    client, _ = _client([{"ok": False, "description": "bad request"}])
    with pytest.raises(TelegramError) as err:
        client.send_message("x")
    assert TOKEN not in str(err.value)


def test_erro_de_transporte_nao_vaza_token():
    client, _ = _client([OSError(f"http://api.telegram.org/bot{TOKEN}/x")])
    with pytest.raises(TelegramError) as err:
        client.send_message("x")
    assert TOKEN not in str(err.value)
