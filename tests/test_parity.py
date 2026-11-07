"""Полное покрытие API: каждая функция вызывается, а запросы сверяются с сервером.

С переменной WEKON_CALLS запросы пишутся в файл: их разбирают настоящие структуры сервера.
"""
import hashlib
import hmac
import importlib
import inspect
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

from test_library import Server, callback, kitchen_sink, message
from wekon import Card, Form, Keyboard, ReplyKeyboard, button, inline
from wekon.aio import Bot, Client
from wekon.client import check_login
from wekon.errors import NetworkError, NotFound


def every_call(bot, now):
    """Вызовы всех публичных методов клиента с полным набором настроек."""
    card = Card().header("Карточка")
    keyboard = Keyboard().button("A", "a")
    common = dict(reply_to=5, quote="цитата", quote_position=0, allow_without_reply=True, thread=7, silent=True, protect=True,
                  bind_to=3, self_destruct=60)
    media = dict(caption_entities=[{"type": "bold", "offset": 0, "length": 1}], spoiler=True, caption_above=True, **common)
    return [
        bot.send_message(1, "x", keyboard=keyboard, html=True, page="p", link_preview={"url": "https://wekon.ru", "prefer_large_media": True, "show_above_text": True}, **common),
        bot.send_message(1, "x", entities=[{"type": "bold", "offset": 0, "length": 1}], schedule=now + 60),
        bot.send_message(1, card=card, client_message_id="c1"),
        bot.edit_message(1, 2, "x", card=card, keyboard=keyboard, html=True, page="p"),
        bot.edit_message(1, 2, "x", entities=[{"type": "bold", "offset": 0, "length": 1}]),
        bot.edit_message(text="x", inline_message_id="i1"),
        bot.edit_message(1, 2, keyboard=keyboard, page="p"),
        bot.edit_caption(1, 2, "x", keyboard=keyboard, html=True, caption_entities=None),
        bot.edit_caption(caption="x", inline_message_id="i1"),
        bot.edit_keyboard(1, 2, keyboard),
        bot.edit_keyboard(inline_message_id="i1"),
        bot.edit_media(1, 2, "f", caption="x", keyboard=keyboard),
        bot.delete_message(1, 2), bot.delete_messages(1, [2]),
        bot.forward_message(1, 2, 3, self_destruct=60, bind_to=3, silent=True, protect=True, thread=7),
        bot.forward_messages(1, 2, [3], client_message_ids=["a"]),
        bot.copy_message(1, 2, 3, caption="x", keyboard=keyboard, parse_mode="HTML", caption_entities=None),
        bot.copy_messages(1, 2, [3]),
        bot.save_draft(1, "x", keyboard=keyboard), bot.send_chat_action(1, "upload_photo"),
        bot.pin_message(1, 2, as_panel=True), bot.unpin_message(1, 2),
        bot.react(1, 2, "339"), bot.read_messages(1, 2),
        bot.get_scheduled_messages(1), bot.delete_scheduled_message(5),
        bot.send_photo(1, "f", "x", keyboard=keyboard, html=True, **media),
        bot.send_document(1, "f", "x", **media), bot.send_audio(1, "f", "x", **media), bot.send_video(1, "f", "x", **media),
        bot.send_animation(1, "f", "x", **media), bot.send_voice(1, "f", "x", **media), bot.send_video_note(1, "f", keyboard=keyboard, **common),
        bot.send_media_group(1, ["f", {"type": "video", "file_id": "g", "caption": "x"}], "x", **common),
        bot.send_sticker(1, 5, silent=True, thread=7, bind_to=3, self_destruct=60),
        bot.send_poll(1, "?", ["a", "b"], anonymous=False, multiple=True, silent=True, thread=7, bind_to=3, self_destruct=60),
        bot.stop_poll(1, 2),
        bot.set_invoice_status(1, 2, "failed", status_text="x", tone="danger", note="x", receipt_url="https://a.ru", pay_url="https://a.ru", expires_at=now + 60, keyboard=keyboard),
        bot.answer_callback("c", "x", alert=True, near_button=True, url="https://a.ru", haptic="success", cache_time=5),
        bot.answer_inline("i", [inline.article("1", "A", "x")], cache_time=5, personal=True, next_offset="2", button={"text": "Открыть", "start_parameter": "x"}),
        bot.get_me(), bot.get_chat(1), bot.get_chat_member(1, 2), bot.get_chat_member_count(1), bot.get_chat_administrators(1),
        bot.get_user_profile_photos(1, 2), bot.restrict_member(1, 2, until=now + 60), bot.ban_member(1, 2), bot.unban_member(1, 2),
        bot.create_topic(1, "x"), bot.edit_topic(1, 2, "x"), bot.delete_topic(1, 2),
        bot.set_commands({"start": "Начать"}, scope={"type": "chat", "chat_id": 1}, language="ru"),
        bot.set_commands([{"command": "a", "description": "b", "params": "<x>"}]),
        bot.get_commands(scope={"type": "default"}, language="ru"), bot.delete_commands(language="ru"),
        bot.set_name("x", "ru"), bot.set_description("x", "ru"), bot.set_short_description("x", "ru"),
        bot.set_profile_photo("f"), bot.remove_profile_photo(), bot.set_menu_button("commands", 1), bot.get_menu_button(1),
        bot.set_webhook("https://a.ru", secret="s", allowed_updates=["message"], drop_pending=True), bot.delete_webhook(drop_pending=True), bot.get_webhook_info(),
        bot.get_file("f"), bot.get_file(attachment_id=5),
    ]


@pytest.fixture
def server():
    return Server()


@pytest.fixture
def bot(server):
    return Bot("shop_bot", "key", http=httpx.AsyncClient(transport=httpx.MockTransport(server)))


class Answers(Server):
    """Ответы нужной формы для методов, которые разбирают результат."""

    def __call__(self, request):
        response = super().__call__(request)
        body = json.loads(request.content or b"{}")
        special = {"getMyName": {"name": "x"}, "getMyDescription": {"description": "x"}, "getMyShortDescription": {"short_description": "x"},
                   "forwardMessages": [{"message_id": 1}], "copyMessages": [{"message_id": 1}]}
        if body.get("method") in special:
            return httpx.Response(200, json={"ok": True, "result": special[body["method"]]})
        return response


async def test_every_public_method_reaches_the_server():
    server = Answers()
    bot = Bot("shop_bot", "key", http=httpx.AsyncClient(transport=httpx.MockTransport(server)))
    now = int(time.time())
    calls = every_call(bot, now)
    calls += [bot.get_name("ru"), bot.get_description("ru"), bot.get_short_description("ru"),
              bot.send_invoice(1, "Счёт", amount=100, pay_url="https://a.ru", currency="RUB", description="d", subtitle="s", icon="🧾",
                               image="https://a.ru/i.png", items=[{"label": "A", "amount": 100}], expires_at=now + 3600, pay_text="Оплатить",
                               card=Card().text("x"), keyboard=Keyboard().button("A", "a"), silent=True)]
    for call in calls:
        await call
    seen = {body["method"] for path, body, _ in server.calls if path == "/bot/call"}
    source = Path(__file__).resolve().parents[2] / "wekon_backend/internal/modules/bots/internal/transport/http/bot_call.go"
    if source.exists():
        text = source.read_text(encoding="utf-8")
        import re
        block = text[text.index("var knownBotMethods"):]
        known = set(re.findall(r'"([a-zA-Z]+)":\s*\{\}', block[:block.index("\n}\n")]))
        assert known - seen == {"answerWebAppQuery", "savePreparedInlineMessage"}, sorted(known - seen)
    target = os.environ.get("WEKON_CALLS")
    if target:
        Path(target).write_text(json.dumps([[body["method"], body["details"]] for path, body, _ in server.calls if path == "/bot/call"],
                                           ensure_ascii=False), encoding="utf-8")


def test_every_client_method_is_exercised():
    """Новая функция клиента не должна пропустить проверку выше."""
    source = inspect.getsource(sys.modules[__name__])
    names = [name for name, value in vars(Client).items() if inspect.iscoroutinefunction(value) and not name.startswith("_")]
    skip = {"call", "get_updates", "logout", "close", "upload", "download"}
    missing = [name for name in names if name not in skip and f"bot.{name}(" not in source]
    assert not missing, missing


async def test_upload_and_logout(bot, server):
    assert await bot.upload(b"x", "a.txt") == "uploaded"
    await bot.get_me()
    await bot.logout()
    assert any(path == "/bot/auth/logout" for path, _, _ in server.calls)


async def test_every_event_type_reaches_a_handler(bot):
    events = {
        "message": {"message": {"message_id": 1, "chat": {"chat_id": 1}, "from": {"user_id": 2}}},
        "photo": {"message": {"message_id": 1, "chat": {"chat_id": 1}, "from": {"user_id": 2}}},
        "media_group": {"message": {"message_id": 1, "chat": {"chat_id": 1}}},
        "edited_message": {"edited_message": {"message_id": 1, "chat": {"chat_id": 1}, "from": {"user_id": 2}, "text": "x"}},
        "deleted_message": {"deleted_message": {"message_id": 1, "chat": {"chat_id": 1}, "from": {"user_id": 2}}},
        "reaction": {"reaction": {"message_id": 1, "chat_id": 1, "emoji_id": "339", "from": {"user_id": 2}}},
        "chat_member": {"chat_member": {"chat": {"chat_id": 1}, "user": {"user_id": 2}, "new_role": "member"}},
        "my_chat_member": {"my_chat_member": {"chat": {"chat_id": 1}, "user": {"user_id": 3}}},
        "poll_answer": {"poll_answer": {"user": {"user_id": 2}, "option_ids": [0]}},
        "poll": {"poll": {"id": 1}},
        "callback_query": callback("x")["callback_query"] and {"callback_query": callback("x")["callback_query"]},
        "inline_query": {"inline_query": {"id": "i", "query": "q", "from": {"user_id": 2}}},
        "chosen_inline_result": {"chosen_inline_result": {"result_id": "1", "from": {"user_id": 2}, "inline_message_id": "i1"}},
    }
    seen = {}

    @bot.on(*events)
    async def any_event(ctx):
        seen[ctx.update.type] = (ctx.chat_id, ctx.user.id, ctx.payload)

    for number, (kind, body) in enumerate(events.items(), 1):
        await bot.process_update({"update_id": number, "type": kind, **body})
    assert set(seen) == set(events)
    assert seen["reaction"][:2] == (1, 2) and seen["chat_member"][:2] == (1, 2) and seen["poll_answer"][1] == 2
    assert seen["edited_message"][2]["text"] == "x"


async def test_edit_after_chosen_inline_result_uses_inline_id(bot, server):
    @bot.on("chosen_inline_result")
    async def chosen(ctx):
        await ctx.edit("Готово")

    await bot.process_update({"update_id": 1, "type": "chosen_inline_result", "chosen_inline_result": {"result_id": "1", "from": {"user_id": 2}, "inline_message_id": "i1"}})
    assert server.details("editMessageText")["inline_message_id"] == "i1"


async def test_context_sends_any_media_to_its_chat(bot, server):
    @bot.command("start")
    async def start(ctx):
        await ctx.send_video("f", "Видео")
        await ctx.send_poll("?", ["a", "b"])

    await bot.process_update(message("/start"))
    assert server.details("sendVideo")["chat_id"] == 1 and server.details("sendPoll")["chat_id"] == 1


def test_simple_context_bot_is_blocking(server):
    from wekon import Bot as SimpleBot
    bot = SimpleBot("shop_bot", "key", http=httpx.AsyncClient(transport=httpx.MockTransport(server)))
    results = []

    @bot.command("start")
    def start(msg):
        results.append(msg.bot.get_me())
        results.append(msg.send_document("f"))

    bot.process_update(message("/start"))
    assert "getMe" in server.methods() and "sendDocument" in server.methods() and len(results) == 2


def test_login_check_matches_server_rules():
    params = {"id": "7", "nik_name": "alice", "auth_date": str(int(time.time()))}
    text = "\n".join(f"{key}={params[key]}" for key in sorted(params))
    params["hash"] = hmac.new(hashlib.sha256(b"key").digest(), text.encode(), hashlib.sha256).hexdigest()
    assert check_login(params, "key") and not check_login(params, "other")
    assert not check_login({**params, "nik_name": "bob"}, "key")
    stale = {"id": "7", "auth_date": "1000"}
    text = "\n".join(f"{key}={stale[key]}" for key in sorted(stale))
    stale["hash"] = hmac.new(hashlib.sha256(b"key").digest(), text.encode(), hashlib.sha256).hexdigest()
    assert not check_login(stale, "key")


def test_links(bot):
    assert bot.start_link("promo_42") == "https://web.wekon.ru/#/bot/shop_bot?start=promo_42"
    assert bot.group_link("x") == "https://web.wekon.ru/#/bot/shop_bot?startgroup=x"


def game_screen(monkeypatch):
    monkeypatch.setenv("WEKON_BOT_API_KEY", "key")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
    game = importlib.import_module("game_2048")
    state = game.new_game()
    state["board"][:4] = [2048, 1024, 8192, 4]
    return game.screen(state)


def test_game_uses_full_button_formatting(monkeypatch):
    card, keyboard = game_screen(monkeypatch)
    markup = keyboard.to_dict()
    rows = markup["inline_keyboard"]
    assert markup["columns"] == 4 and rows[0][0]["height"] == 2 and rows[0][1]["width"] == 2 and rows[2][0]["width"] == 4
    restart = rows[2][0]
    assert restart["long_callback_data"] == "restart_now" and restart["confirm"]
    assert rows[0][0]["hint"], "без действия на удержание кнопка показывает подсказку"
    grid = next(block for block in card.to_list() if block["type"] == "grid")
    assert grid["columns"] == 4 and len(grid["cells"]) == 16 and grid["cells"][0]["color"] == "#EDC22E"
    dump = os.environ.get("WEKON_GAME")
    if dump:
        Path(dump).write_text(json.dumps({"components": card.to_list(), "reply_markup": markup}, ensure_ascii=False), encoding="utf-8")


def test_game_rules(monkeypatch):
    monkeypatch.setenv("WEKON_BOT_API_KEY", "key")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
    game = importlib.import_module("game_2048")
    state = {"board": [2, 2, 4, 0] + [0] * 12, "score": 0}
    assert game.move(state, "l") and state["board"][:4] == [4, 4, 0, 0] and state["score"] == 4
    assert not game.move({"board": [2] + [0] * 15, "score": 0}, "l")


def test_reply_keyboard_formatting():
    keys = ReplyKeyboard().button("Да", style="primary", icon="✅", hint="Подтвердить", color="#112233").contact("Телефон", style="success").to_dict()
    assert keys["keyboard"][0][0] == {"text": "Да", "style": "primary", "icon": "✅", "hint": "Подтвердить", "color": "#112233"}
    assert keys["keyboard"][1][0]["style"] == "success"


def test_all_inline_result_types_build():
    results = [inline.article("1", "A", "x", url="https://a.ru", thumbnail="https://a.ru/t.png"), inline.photo("2", "https://a.ru/p.jpg", title="Ф", description="О"),
               inline.gif("3", "https://a.ru/g.gif"), inline.mpeg4_gif("4", "https://a.ru/g.mp4"), inline.video("5", "https://a.ru/v.mp4", title="В"),
               inline.audio("6", "https://a.ru/a.mp3"), inline.voice("7", file_id="f"), inline.document("8", "Д", url="https://a.ru/d.pdf"), inline.sticker("9", 42)]
    assert [item["type"] for item in results] == ["article", "photo", "gif", "mpeg4_gif", "video", "audio", "voice", "document", "sticker"]
    target = os.environ.get("WEKON_INLINE")
    if target:
        Path(target).write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")


PHOTO_EVENT = {"update_id": 9, "type": "photo", "message": {"message_id": 1, "chat": {"chat_id": 1}, "from": {"user_id": 2}, "attachments": [
    {"id": 55, "media_type": "image", "mime_type": "image/png", "original_name": "кот.png", "size_bytes": 2048, "width": 640, "height": 480},
    {"id": 56, "media_type": "voice", "mime_type": "audio/ogg", "duration_sec": 7}]}}


async def test_received_files_are_described_and_downloaded(bot, server, tmp_path):
    seen = {}

    @bot.message(types=["photo"])
    async def photo(ctx):
        file = ctx.message.file
        seen["info"] = (file.media_type, file.name, file.mime_type, file.size, file.width, file.height)
        seen["voice"] = ctx.message.attachments[1].duration
        seen["bytes"] = await ctx.download()
        seen["path"] = await ctx.download(ctx.message.attachments[1], tmp_path / "voice.ogg")

    await bot.process_update(PHOTO_EVENT)
    assert seen["info"] == ("image", "кот.png", "image/png", 2048, 640, 480) and seen["voice"] == 7
    assert seen["bytes"] == b"\x89PNG-bytes" and seen["path"].read_bytes() == b"\x89PNG-bytes"
    requests = [(params, auth) for path, params, auth in server.calls if path == "/bot/files/download"]
    assert requests[0] == ({"file_id": "attachment:55"}, "Bot t1") and requests[1][0] == {"file_id": "attachment:56"}


async def test_download_by_id_and_errors(bot, server):
    assert await bot.download(55) == b"\x89PNG-bytes"
    assert server.calls[-1][1] == {"attachment_id": "55"}
    assert await bot.download("bf_own") == b"\x89PNG-bytes"
    assert server.calls[-1][1] == {"file_id": "bf_own"}
    server.script = [(404, {"ok": False, "error": "file_not_found", "description": "file not found"})]
    with pytest.raises(NotFound):
        await bot.download(99)
    server.script = [(401, {"ok": False, "error": "unauthorized"})]
    assert await bot.download(55) == b"\x89PNG-bytes", "просроченный токен обновляется сам"
    assert server.auths == 2


def test_simple_mode_download(server):
    from wekon import Bot as SimpleBot
    bot = SimpleBot("shop_bot", "key", http=httpx.AsyncClient(transport=httpx.MockTransport(server)))
    got = []

    @bot.message(types=["photo"])
    def photo(msg):
        got.append(msg.download())

    bot.process_update(PHOTO_EVENT)
    assert got == [b"\x89PNG-bytes"] and bot.download(55) == b"\x89PNG-bytes"


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"half"
        raise httpx.ReadError("связь оборвалась")


async def test_broken_download_leaves_no_partial_file(server, tmp_path, monkeypatch):
    from wekon.aio import Bot as AsyncBot
    monkeypatch.setattr("wekon.client.MAX_RETRIES", 0)

    def handler(request):
        if request.url.path.endswith("/bot/files/download"):
            return httpx.Response(200, stream=BrokenStream())
        return server(request)

    bot = AsyncBot("shop_bot", "key", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    target = tmp_path / "video.mp4"
    with pytest.raises(NetworkError):
        await bot.download(55, target)
    assert list(tmp_path.iterdir()) == []
