"""Cliente mínimo da Bot API do Telegram em stdlib (spec §4.6). Um único
chat autorizado: updates de qualquer outro chat são descartados. O token
vive na URL — nenhuma mensagem de erro pode conter a URL."""
from __future__ import annotations

import json
import urllib.request
from typing import Callable

API_BASE = "https://api.telegram.org"


class TelegramError(Exception):
    """Falha na Bot API (mensagem NUNCA contém token/URL)."""


def _default_transport(url: str, body: bytes | None) -> bytes:
    if body is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"},
            method="POST")
    with urllib.request.urlopen(req, timeout=35) as resp:
        return resp.read()


class TelegramClient:
    def __init__(self, token: str, chat_id: str,
                 transport: Callable[[str, bytes | None], bytes] | None = None):
        self._token = token
        self.chat_id = chat_id
        self._transport = transport or _default_transport

    def _call(self, method: str, payload: dict | None,
              query: str = "") -> dict:
        url = f"{API_BASE}/bot{self._token}/{method}"
        if query:
            url += f"?{query}"
        body = (json.dumps(payload).encode() if payload is not None else None)
        try:
            data = json.loads(self._transport(url, body))
        except Exception as err:
            raise TelegramError(
                f"falha de transporte no método {method}: "
                f"{type(err).__name__}") from err
        if not data.get("ok"):
            raise TelegramError(
                f"Bot API recusou {method}: {data.get('description', '?')}")
        return data

    def send_message(self, text: str,
                     buttons: list[tuple[str, str]] | None = None) -> None:
        payload: dict = {"chat_id": self.chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [[
                {"text": label, "callback_data": data}
                for label, data in buttons
            ]]}
        self._call("sendMessage", payload)

    def get_updates(self, offset: int) -> list[dict]:
        data = self._call("getUpdates", None,
                          query=f"offset={offset}&timeout=25")
        out = []
        for update in data.get("result", []):
            message = update.get("message") or {}
            callback = update.get("callback_query") or {}
            chat = (message.get("chat")
                    or (callback.get("message") or {}).get("chat") or {})
            if str(chat.get("id")) == str(self.chat_id):
                out.append(update)
        return out

    def answer_callback(self, callback_id: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id})
