"""Бот-витрина: каждый раздел и обработчик работают, охват библиотеки полный.

С переменной WEKON_CALLS запросы пишутся в файл: их проверяют настоящие валидаторы сервера.
"""
import importlib.util
import json
import logging
import os
import re
import sys
from pathlib import Path

import httpx
import pytest

from test_library import Server, callback, message
from wekon.bot import Context
from wekon.simple import SimpleContext
from wekon.types import Update

EXAMPLE = Path(__file__).parent.parent / "examples" / "showcase_bot.py"
CARD_BLOCKS = {"header", "text", "image", "gallery", "divider", "callout", "fields", "stats", "progress", "steps", "rating", "tags", "countdown", "list",
               "table", "collapsible", "tabs", "buttons", "select", "toggle", "slider", "stepper", "grid", "checklist", "calendar"}
FORM_FIELDS = {"text", "textarea", "number", "select", "multiselect", "date", "time", "toggle", "color"}
INLINE_RESULTS = {"article", "photo", "gif", "mpeg4_gif", "video", "audio", "voice", "document", "sticker"}
BUTTON_KINDS = {"callback_data", "url", "copy_text", "form", "switch_inline_query", "switch_inline_query_current_chat", "login_url"}
KEY_REQUESTS = {"request_users", "request_chat", "request_contact", "request_date", "request_time", "request_color", "request_file"}
EVENTS = ["message", "photo", "audio", "document", "video", "animation", "voice", "video_note", "media_group", "edited_message", "deleted_message",
          "reaction", "chat_member", "callback_query", "inline_query", "chosen_inline_result", "poll_answer", "poll", "my_chat_member"]


class ShowcaseServer(Server):
    """Отвечает по смыслу: списки, сведения и ошибки там, где их ждёт бот."""

    answers = {
        "getMe": {"nik_name": "showcase"}, "getMyName": {"name": "Витрина"}, "getMyDescription": {"description": "о"}, "getMyShortDescription": {"short_description": "к"},
        "getChat": {"chat_type": "group"}, "forwardMessages": [{"message_id": 8, "chat_id": 1}], "copyMessages": [{"message_id": 9, "chat_id": 1}],
        "createForumTopic": {"message_thread_id": 5}, "getWebhookInfo": {"url": "https://x.ru"}, "getScheduledMessages": [], "getMyCommands": [],
        "getFile": {"download_url": "/api/v1/bot/files/download?file_id=x"}, "sendMessage": {"message_id": 7, "chat_id": 1, "scheduled_id": 3},
    }

    def __init__(self):
        super().__init__()
        self.files = {}
        self.login_domain = True

    def __call__(self, request):
        path = request.url.path.removeprefix("/api/v1")
        if path == "/bot/files/upload":
            name = re.search(rb'filename="([^"]+)"', request.content).group(1).decode()
            asset = Path(EXAMPLE).parent / "assets" / name
            self.files[f"id-{name}"] = asset.read_bytes() if asset.exists() else b""
            return httpx.Response(200, json={"ok": True, "result": {"file_id": f"id-{name}"}})
        if path == "/bot/files/download":
            params = dict(request.url.params)
            self.calls.append((path, params, ""))
            data = self.files.get(params.get("file_id"), b"x" * 10)
            if params.get("attachment_id") == "1":
                return httpx.Response(404, json={"ok": False, "error": "file_not_found", "description": "нет"})
            return httpx.Response(200, content=data)
        if path == "/bot/updates/get":
            return httpx.Response(409, json={"ok": False, "error": "polling_conflict", "description": "занято"})
        if path == "/bot/auth/logout":
            return httpx.Response(200, json={"ok": True})
        if path == "/bot/call":
            body = json.loads(request.content)
            method, details = body["method"], body["details"]
            self.calls.append((path, body, ""))
            bad = (method == "sendMessage" and not (details.get("text") or details.get("components"))) or any(
                block.get("type") == "нет такого" for block in details.get("components") or [])
            if bad:
                return httpx.Response(400, json={"ok": False, "error": "invalid_reply_markup", "description": "нет"})
            if not self.login_domain and "login_url" in json.dumps(details):
                return httpx.Response(400, json={"ok": False, "error": "login_domain_mismatch", "description": "нет"})
            if method == "noSuchMethod" or (method == "deleteMessage" and details.get("message_id") == 1):
                return httpx.Response(404, json={"ok": False, "error": "not_found", "description": "нет"})
            return httpx.Response(200, json={"ok": True, "result": self.answers.get(method, {"message_id": 7, "chat_id": 1})})
        return super().__call__(request)


@pytest.fixture(scope="module")
def world():
    os.environ["WEKON_BOT_API_KEY"] = "key"
    os.environ["WEKON_STICKER"] = "5"
    spec = importlib.util.spec_from_file_location("showcase_bot", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["showcase_bot"] = module
    spec.loader.exec_module(module)
    server = ShowcaseServer()
    async def swap():
        module.bot._core.http = httpx.AsyncClient(transport=httpx.MockTransport(server))
    module.bot._loop.run(swap())
    module.client_http = lambda: httpx.AsyncClient(transport=httpx.MockTransport(server))
    return module, server


def simple_context(module, raw):
    core = module.bot._core
    return SimpleContext(Context(core, Update.parse(raw)), module.bot._loop, module.bot)


def feed(module, raw):
    """Событие целиком: обработчики, журнал и посредник. ``update_id`` разные, иначе событие сочтут повтором."""
    feed.number = getattr(feed, "number", 1000) + 1
    module.bot.process_update({**raw, "update_id": feed.number})


def test_every_section_passes(world):
    module, _ = world
    for name in module.SECTIONS:
        report = module.run_section(simple_context(module, callback("menu")), name)
        assert report.failed == [], name
        assert report.card().to_list()


def test_login_button_without_domain(world):
    """Без /setdomain сервер отклоняет кнопку входа: раздел объясняет это и идёт дальше."""
    module, server = world
    server.login_domain = False
    try:
        report = module.run_section(simple_context(module, callback("menu")), "buttons")
    finally:
        server.login_domain = True
    assert report.failed == []
    assert any("/setdomain" in str(body["details"].get("text")) for path, body, _ in server.calls if path == "/bot/call")


ATTACHMENTS = {
    "photo": {"id": 11, "media_type": "image", "original_name": "a.png", "size_bytes": 10, "width": 4, "height": 3},
    "voice": {"id": 12, "media_type": "voice", "size_bytes": 10, "duration_sec": 2},
    "video_note": {"id": 13, "media_type": "video_circle", "size_bytes": 10, "duration_sec": 2},
    "video": {"id": 14, "media_type": "video", "size_bytes": 10},
    "document": {"id": 15, "media_type": "document", "size_bytes": 10},
    "media_group": {"id": 16, "media_type": "image", "size_bytes": 10},
}


@pytest.mark.parametrize("kind", ATTACHMENTS)
def test_received_files(world, kind):
    module, server = world
    raw = message("", kind=kind)
    raw[kind] = raw.pop("message")
    raw[kind] = raw[kind] if kind != "x" else raw[kind]
    event = {"type": kind, "message": {**raw[kind], "attachments": [ATTACHMENTS[kind]]}}
    before = len(server.calls)
    feed(module, event)
    calls = [entry for entry in server.calls[before:] if entry[0] == "/bot/files/download"]
    assert len(calls) == 2 and calls[0][1] == {"file_id": f"attachment:{ATTACHMENTS[kind]['id']}"}


@pytest.mark.parametrize("data,value", [("ui:plan", "team"), ("ui:notify", "1"), ("ui:volume", "55"), ("ui:count", "3"), ("ui:seats", "s1:1"), ("ui:seats", "s1:0"),
                                        ("ui:list", "a:1"), ("ui:radio", "y"), ("ui:day", "2026-12-01"), ("ui:cell:a", None), ("page:p2", None), ("page:p1", None),
                                        ("ans:toast", None), ("ans:alert", None), ("ans:near", None), ("ans:haptic", None), ("ans:url", None), ("ans:cache", None),
                                        ("ans:short", None), ("ans:long", None), ("kb:remove", None), ("kb:force", None), ("inv:paid", None), ("grp:mute:5", None),
                                        ("grp:free:5", None), ("grp:ban:5", None), ("grp:unban:5", None), ("prof:photo", None), ("prof:nophoto", None), ("prof:hook", None),
                                        ("game:new", None), ("game:now", None), ("game:l", None), ("game:d", None), ("inl:edit", None), ("dlg:steps", None), ("raw:answer", None), ("raw:delete", None),
                                        ("menu", None), ("coverage", None), ("journal", None), ("sec:game", None)])
def test_every_press(world, data, value, caplog):
    module, server = world
    module.controls[5] = {"plan": "pro", "notify": True, "volume": 40, "count": 2, "seats": [], "list": [], "radio": "x", "day": ""}
    before = len(server.methods())
    with caplog.at_level(logging.WARNING):
        feed(module, callback(data, value=value))
    assert "упал" not in caplog.text
    assert "answerCallbackQuery" in server.methods()[before:]
    if data == "dlg:steps":
        feed(module, message("Анна"))
        feed(module, message("нет"))
        feed(module, message("30"))


def test_clean_deletes_the_persons_message(world):
    module, server = world
    feed(module, message("/clean"))
    delete = [body["details"] for path, body, _ in server.calls if path == "/bot/call" and body["method"] == "deleteMessage"][-1]
    assert delete["message_id"] == message("/clean")["message"]["message_id"]


def test_forms_and_text(world, caplog):
    module, server = world
    with caplog.at_level(logging.WARNING):
        feed(module, callback("signup", form={"name": "Анна"}))
        feed(module, callback("poll:7", form={"answer": "да"}))
        for text in ("/start promo", "/coverage", "/journal", "/async 1 2", "привет", "2 + 3", "просто текст"):
            feed(module, message(text))
    assert "упал" not in caplog.text
    assert any("Шаблон regex: 5" == body["details"].get("text") for path, body, _ in server.calls if path == "/bot/call")


def test_shared_answers(world):
    module, server = world
    for field, value in (("users_shared", {"request_id": 1, "users": [{"user_id": 9}]}), ("contact", {"phone": "+7"}), ("date_shared", {"date": "2026-01-01"})):
        raw = message("")
        raw["message"][field] = value
        before = len(server.methods())
        feed(module, raw)
        assert "sendMessage" in server.methods()[before:]


def test_error_handler_reports_to_chat(world):
    module, server = world
    feed(module, callback("ans:crash"))
    feed(module, message("/error"))
    texts = [body["details"].get("text", "") for path, body, _ in server.calls if path == "/bot/call" and body["method"] == "sendMessage"]
    assert any(text.startswith("⚠️ ValueError") for text in texts)


def test_inline_all_pages(world):
    module, server = world
    for offset in ("", "1", "2"):
        feed(module, {"type": "inline_query", "inline_query": {"id": "i", "query": "демо", "offset": offset, "from": {"user_id": 7}}})
    answer = next(body["details"] for path, body, _ in server.calls if path == "/bot/call" and body["method"] == "answerInlineQuery")
    assert {result["type"] for result in answer["results"]} == INLINE_RESULTS


def test_other_events_reach_the_journal(world):
    module, _ = world
    for kind in ("edited_message", "deleted_message", "reaction", "chat_member", "my_chat_member", "chosen_inline_result", "poll_answer", "poll"):
        feed(module, {"type": kind, kind: {"chat": {"chat_id": 1}, "from": {"user_id": 7}, "message_id": 1, "text": "x"}})
        assert module.journal[kind] >= 1, kind


def test_selftest_runs_everything(world):
    module, server = world
    feed(module, message("/selftest"))
    assert module.journal["message"] > 0


def walk(value):
    if isinstance(value, dict):
        yield value
        for entry in value.values():
            yield from walk(entry)
    elif isinstance(value, list):
        for entry in value:
            yield from walk(entry)


def test_nothing_in_the_library_is_left_out(world):
    """Запускается последним: смотрит на все запросы, которые ушли за файл."""
    module, server = world
    assert module.missing_from_code() == []
    bodies = [body for path, body, _ in server.calls if path == "/bot/call"]
    methods = {body["method"] for body in bodies}
    source = (Path(__file__).parent.parent.parent / "wekon_backend/internal/modules/bots/internal/transport/http/bot_call.go")
    if source.exists():
        block = source.read_text(encoding="utf-8").split("var knownBotMethods", 1)[1]
        known = set(re.findall(r'"([a-zA-Z]+)":\s*\{\}', block[:block.index("\n}\n")]))
        assert known - methods == {"answerWebAppQuery", "savePreparedInlineMessage"}, sorted(known - methods)
    objects = list(walk([body["details"] for body in bodies]))
    assert CARD_BLOCKS <= {entry["type"] for entry in objects if "type" in entry}, "блоки"
    assert FORM_FIELDS <= {entry["type"] for entry in objects if "type" in entry and "id" in entry and "label" in entry}, "поля форм"
    assert BUTTON_KINDS <= {key for entry in objects if "text" in entry for key in entry}, "кнопки"
    assert KEY_REQUESTS <= {key for entry in objects if "text" in entry for key in entry}, "запросы клавиатуры"
    looks = {key for entry in objects if "callback_data" in entry for key in entry}
    assert {"icon", "style", "color", "hint", "width", "height", "haptic", "loading", "confirm", "checked", "expires_at", "long_callback_data"} <= looks
    sent = {key for body in bodies for key in body["details"]}
    assert {"message_thread_id", "disable_notification", "protect_content", "has_spoiler", "show_caption_above_media", "self_destruct_seconds", "link_preview_options",
            "bind_to", "reply_parameters", "reply_to_message_id", "schedule_date", "page", "parse_mode"} <= sent
    assert set(EVENTS) <= set(module.EVENT_TYPES)
    assert all(len(entry["inline_keyboard"]) <= 10 for entry in objects if "inline_keyboard" in entry), "сервер принимает до 10 рядов"
    target = os.environ.get("WEKON_CALLS")
    if target:
        Path(target).write_text(json.dumps([[body["method"], body["details"]] for body in bodies], ensure_ascii=False), encoding="utf-8")
