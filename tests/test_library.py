import base64
import hashlib
import hmac
import json
import re
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from wekon import Card, Form, Keyboard, ReplyKeyboard, badge, bar, button, cell, chip, gap, inline, item, mark, poster, stat, step, tile, url_button
from wekon.aio import Bot
from wekon.errors import AuthError, BadRequest, Conflict, NetworkError, NotFound, RateLimited
from wekon.payments import CloudPayments, Robokassa, TBank, YooKassa
from wekon.types import Update


class Server:
    """Поддельный сервер Wekon: записывает вызовы и отвечает как настоящий."""

    def __init__(self):
        self.calls = []
        self.auths = 0
        self.script = []  # ответы по порядку: (статус, тело)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        if path == "/bot/auth":
            self.auths += 1
            return httpx.Response(200, json={"ok": True, "bot_token": f"t{self.auths}", "expires_in": 600})
        if path == "/bot/files/upload":
            return httpx.Response(200, json={"ok": True, "result": {"file_id": "uploaded"}})
        if path == "/bot/files/download":
            self.calls.append((path, dict(request.url.params), request.headers.get("authorization")))
            if self.script:
                status, answer = self.script.pop(0)
                return httpx.Response(status, json=answer)
            return httpx.Response(200, content=b"\x89PNG-bytes", headers={"content-type": "image/png"})
        body = json.loads(request.content or b"{}")
        self.calls.append((path, body, request.headers.get("authorization")))
        if self.script:
            status, answer = self.script.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return httpx.Response(status, json=answer)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7, "chat_id": 1}})

    def methods(self):
        return [body["method"] for path, body, _ in self.calls if path == "/bot/call"]

    def details(self, method):
        return next(body["details"] for path, body, _ in self.calls if path == "/bot/call" and body["method"] == method)


@pytest.fixture
def server():
    return Server()


@pytest.fixture
def bot(server):
    return Bot("shop_bot", "key", http=httpx.AsyncClient(transport=httpx.MockTransport(server)))


def message(text, update_id=1, chat=1, kind="message"):
    return {"update_id": update_id, "type": kind, "message": {"message_id": 5, "chat": {"chat_id": chat, "chat_type": "private"},
                                                              "from": {"user_id": 77, "nik_name": "alice"}, "text": text}}


def callback(data, value=None, form=None, update_id=2):
    payload = {"id": "cb1", "message_id": 5, "chat_id": 1, "chat": {"chat_id": 1, "chat_type": "private"},
               "from": {"user_id": 77, "nik_name": "alice"}, "data": data}
    if value is not None:
        payload["value"] = value
    if form is not None:
        payload["form_data"] = form
    return {"update_id": update_id, "type": "callback_query", "callback_query": payload}


async def test_authorization_and_call_format(bot, server):
    await bot.send_message(1, "Привет")
    path, body, auth = server.calls[0]
    assert path == "/bot/call" and auth == "Bot t1"
    assert body["header"]["bot_nik_name"] == "shop_bot" and body["header"]["api_version"] == "v1"
    assert body["method"] == "sendMessage" and body["details"]["text"] == "Привет"
    await bot.send_message(1, "Ещё")
    assert server.auths == 1, "токен должен кэшироваться"


async def test_expired_token_is_refreshed(bot, server):
    server.script = [(401, {"ok": False, "error": "unauthorized"})]
    await bot.get_me()
    assert server.auths == 2 and server.calls[-1][2] == "Bot t2"


async def test_rate_limit_waits_and_retries(bot, server):
    server.script = [(429, {"ok": False, "error": "rate_limited", "parameters": {"retry_after": 0}})]
    await bot.get_me()
    assert len(server.calls) == 2


async def test_errors_become_classes(bot, server):
    cases = [(400, "invalid_reply_markup", BadRequest), (404, "message_not_found", NotFound), (409, "webhook_active", Conflict), (403, "bot_suspended", AuthError)]
    for status, code, kind in cases:
        server.script = [(status, {"ok": False, "error": code, "description": "x"})]
        with pytest.raises(kind) as caught:
            await bot.get_me()
        assert caught.value.code == code


async def test_network_error_retries_only_safe_calls(bot, server):
    server.script = [(0, httpx.ConnectError("нет связи")) for _ in range(2)]
    # sendMessage идёт с client_message_id: сервер отсечёт дубль, повторять безопасно.
    await bot.send_message(1, "x")
    assert len(server.calls) == 3
    ids = {body["details"]["client_message_id"] for _, body, _ in server.calls}
    assert len(ids) == 1, "при повторе id сообщения тот же"
    server.calls.clear()
    server.script = [(0, httpx.ConnectError("нет связи"))]
    with pytest.raises(NetworkError):
        await bot.call("sendPoll", chat_id=1, question="?", options=["a", "b"])
    assert len(server.calls) == 1


async def test_friendly_options_map_to_api(bot, server):
    await bot.send_message(1, "x", reply_to=5, thread=7, silent=True, protect=True, self_destruct=30, bind_to=9, link_preview=False, page="p1", schedule=2000000000)
    details = server.details("sendMessage")
    assert details["reply_to_message_id"] == 5 and details["message_thread_id"] == 7 and details["disable_notification"] is True
    assert details["protect_content"] is True and details["self_destruct_seconds"] == 30 and details["bind_to"] == {"message_id": 9}
    assert details["link_preview_options"] == {"is_disabled": True} and details["page"] == "p1" and details["schedule_date"] == 2000000000


async def test_card_gets_fallback_text(bot, server):
    await bot.send_message(1, card=Card().header("Заказ №42").progress(0.5))
    details = server.details("sendMessage")
    assert details["text"] == "Заказ №42" and details["components"][1]["type"] == "progress"


async def test_media_upload_from_bytes(bot, server):
    await bot.send_photo(1, b"png", "Подпись")
    assert server.details("sendPhoto")["photo"] == "uploaded"
    await bot.send_photo(1, "attachment:5")
    assert server.calls[-1][1]["details"]["photo"] == "attachment:5"


@pytest.mark.parametrize("start, name", [
    (b"\x89PNG\r\n\x1a\n", "file.png"), (b"\xff\xd8\xff\xe0", "file.jpg"), (b"GIF89a", "file.gif"), (b"RIFF\0\0\0\0WEBPVP8", "file.webp"),
    (b"RIFF\0\0\0\0WAVEfmt", "file.wav"), (b"\0\0\0\x18ftypisom", "file.mp4"), (b"\0\0\0\x18ftypM4A ", "file.m4a"), (b"\0\0\0\x18ftypqt  ", "file.mov"),
    (b"OggS", "file.ogg"), (b"ID3\x04", "file.mp3"), (b"%PDF-1.7", "file.pdf"), (b"\x1a\x45\xdf\xa3", "file.webm"), (b"plain", "file"),
])
async def test_bytes_get_a_name_the_server_understands(start, name):
    """Сервер определяет вид по расширению: без него живой запуск сделал фото документом."""
    names = []

    def answer(request):
        if request.url.path.endswith("/bot/files/upload"):
            names.append(re.search(rb'filename="([^"]+)"', request.content).group(1).decode())
        return httpx.Response(200, json={"ok": True, "bot_token": "t", "expires_in": 600, "result": {"file_id": "f"}})

    bot = Bot("shop_bot", "key", http=httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    await bot.upload(start + b"data")
    await bot.upload(start + b"data", filename="own.bin")
    assert names == [name, "own.bin"]


async def test_command_args_and_foreign_bot(bot, server):
    seen = []

    @bot.command("buy")
    async def buy(ctx):
        seen.append(ctx.args)

    await bot.process_update(message("/buy 5 red"))
    await bot.process_update(message("/buy@shop_bot 1", update_id=2))
    await bot.process_update(message("/buy@other_bot 9", update_id=3))
    assert seen == [["5", "red"], ["1"]]


async def test_duplicates_are_dropped(bot):
    seen = []

    @bot.message()
    async def any_text(ctx):
        seen.append(ctx.text)

    await bot.process_update(message("a"))
    await bot.process_update(message("a"))
    assert seen == ["a"]


async def test_message_filters_and_state_priority(bot):
    seen = []

    @bot.message()
    async def anything(ctx):
        seen.append("общий")

    @bot.message(regex=r"^код (?P<code>\d+)$")
    async def code(ctx):
        seen.append(ctx.params["code"])

    @bot.message(state="ask_name")
    async def name(ctx):
        seen.append(("имя", ctx.text, await ctx.get_data()))
        await ctx.clear_state()

    @bot.command("start")
    async def start(ctx):
        await ctx.ask_later("Как вас зовут?", "ask_name", source="start")

    await bot.process_update(message("/start"))
    await bot.process_update(message("Анна", update_id=2))
    await bot.process_update(message("Анна", update_id=3))
    assert seen == [("имя", "Анна", {"source": "start"}), "общий"]


async def test_callback_pattern_form_and_auto_answer(bot, server):
    seen = []

    @bot.callback("buy:{item}")
    async def buy(ctx):
        seen.append(ctx.params)

    @bot.callback("seat")
    async def seat(ctx):
        await ctx.answer("Выбрано")
        seen.append(ctx.picked)

    @bot.form("signup")
    async def signup(ctx):
        seen.append(ctx.form)

    await bot.process_update(callback("buy:42"))
    await bot.process_update(callback("seat", value="r1s1:1", update_id=3))
    await bot.process_update(callback("signup", form={"name": "Анна"}, update_id=4))
    assert seen == [{"item": "42"}, ("r1s1", True), {"name": "Анна"}]
    assert server.methods().count("answerCallbackQuery") == 3, "каждое нажатие получает ответ, иначе крутится индикатор"


async def test_handler_error_does_not_stop_and_callback_is_answered(bot, server):
    errors = []

    @bot.callback("x")
    async def broken(ctx):
        raise ValueError("сломалось")

    @bot.error
    async def on_error(ctx):
        errors.append(str(ctx.exception))

    await bot.process_update(callback("x"))
    assert errors == ["сломалось"] and "answerCallbackQuery" in server.methods()


async def test_same_chat_keeps_order_other_chats_run_together(bot):
    import asyncio
    order = []

    @bot.message()
    async def slow(ctx):
        await asyncio.sleep(0.02 if ctx.text == "первое" else 0)
        order.append(ctx.text)

    bot._enqueue(message("первое", 1, chat=1))
    bot._enqueue(message("второе", 2, chat=1))
    bot._enqueue(message("другой чат", 3, chat=2))
    await bot.wait_idle()
    assert order.index("первое") < order.index("второе")
    assert order[0] == "другой чат", "другой чат не ждёт чужие обработчики"


async def test_edit_and_answer_target_pressed_message(bot, server):
    @bot.callback("q")
    async def change(ctx):
        await ctx.edit(card=Card().header("Новая"))

    await bot.process_update(callback("q"))
    details = server.details("editMessageText")
    assert details["message_id"] == 5 and details["chat_id"] == 1 and details["text"] == "Новая"


async def test_webhook_checks_secret(bot):
    seen = []

    @bot.message()
    async def text(ctx):
        seen.append(ctx.text)

    app = bot.asgi(secret="s3")

    async def post(headers, body=json.dumps(message("вебхук")).encode()):
        sent = []

        async def send(event):
            sent.append(event)

        async def receive():
            return {"body": body}

        await app({"type": "http", "method": "POST", "path": "/wekon", "headers": headers}, receive, send)
        return sent[0]["status"]

    assert await post([]) == 401
    assert await post([(b"X-Wekon-Bot-Api-Secret-Token", b"s3")]) == 200
    await bot.wait_idle()
    assert seen == ["вебхук"]


def test_keyboard_forms_and_grid():
    keyboard = Keyboard(columns=4).row(("Да", "yes"), button("Нет", "no", style="danger", width=2, confirm="Точно?")).url("Сайт", "https://wekon.ru").to_dict()
    assert keyboard["columns"] == 4 and keyboard["inline_keyboard"][0][1]["confirm"] == {"title": "Точно?"}
    form = Form("f", "Анкета").text("a", "A", required=True).page("Шаг 2").date("b", "B").to_dict()
    assert len(form["pages"]) == 2 and form["pages"][1]["title"] == "Шаг 2"


def test_update_parsing_uses_real_field_names():
    update = Update.parse(callback("x", value="1"))
    assert update.callback.chat.id == 1 and update.callback.user.nik == "alice" and update.chat_id == 1
    media = Update.parse({"update_id": 3, "type": "photo", "message": {"message_id": 1, "chat": {"chat_id": 2}, "attachments": [{"id": 9}]}})
    assert media.message.attachments[0].ref == "attachment:9"


class Kassa:
    def __init__(self, answers):
        self.answers = answers
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(200, json=self.answers.pop(0))

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


async def test_yookassa_uses_rubles_and_rechecks_notification():
    kassa = Kassa([{"id": "p1", "confirmation": {"confirmation_url": "https://yoomoney.ru/checkout"}},
                   {"status": "succeeded", "metadata": {"order_id": "o1"}}])
    provider = YooKassa("shop", "secret", http=kassa.client())
    payment = await provider.create("o1", 99000, "Заказ")
    sent = json.loads(kassa.requests[0].content)
    assert payment.url.startswith("https://yoomoney.ru") and sent["amount"]["value"] == "990.00"
    event = await provider.parse_notification(json.dumps({"event": "payment.succeeded", "object": {"id": "p1"}}).encode(), {})
    assert event.status == "paid" and event.order_id == "o1"
    assert kassa.requests[1].method == "GET", "уведомлению не верим, спрашиваем кассу"


async def test_yookassa_forged_notification_is_not_paid():
    kassa = Kassa([{"status": "pending", "metadata": {"order_id": "o1"}}])
    provider = YooKassa("shop", "secret", http=kassa.client())
    event = await provider.parse_notification(json.dumps({"event": "payment.succeeded", "object": {"id": "p1"}}).encode(), {})
    assert event is None


async def test_tbank_signature():
    provider = TBank("term", "pass")
    notice = {"OrderId": "o1", "PaymentId": 5, "Status": "CONFIRMED", "Success": True}
    notice["Token"] = provider.token(notice)
    event = await provider.parse_notification(json.dumps(notice).encode(), {})
    assert event.status == "paid" and provider.notification_reply(event) == "OK"
    notice["Status"] = "REJECTED"  # подпись больше не сходится
    assert await provider.parse_notification(json.dumps(notice).encode(), {}) is None


async def test_robokassa_link_and_result():
    provider = Robokassa("shop", "one", "two")
    payment = await provider.create("15", 99000, "Заказ")
    assert "OutSum=990.00" in payment.url and "InvId=15" in payment.url
    sign = hashlib.md5(b"990.00:15:two").hexdigest().upper()
    event = await provider.parse_notification(f"OutSum=990.00&InvId=15&SignatureValue={sign}".encode(), {})
    assert event.status == "paid" and provider.notification_reply(event) == "OK15"
    assert await provider.parse_notification(b"OutSum=1.00&InvId=15&SignatureValue=bad", {}) is None


async def test_cloudpayments_signature():
    kassa = Kassa([{"Success": True, "Model": {"Id": "i1", "Url": "https://orders.cloudpayments.ru/d/1"}}])
    provider = CloudPayments("public", "secret", http=kassa.client())
    payment = await provider.create("o1", 99000, "Заказ")
    assert payment.url.endswith("/d/1") and json.loads(kassa.requests[0].content)["Amount"] == 990.0
    body = b"InvoiceId=o1&TransactionId=5&Status=Completed"
    signature = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
    event = await provider.parse_notification(body, {"Content-HMAC": signature})
    assert event.status == "paid" and event.order_id == "o1"
    assert await provider.parse_notification(body, {"Content-HMAC": "bad"}) is None


async def test_send_payment_then_notification_sets_status(bot, server):
    kassa = Kassa([{"id": "p1", "confirmation": {"confirmation_url": "https://yoomoney.ru/c"}}, {"status": "succeeded", "metadata": {"order_id": "o1"}}])
    provider = YooKassa("shop", "secret", http=kassa.client())
    seen = []

    @bot.on("payment")
    async def paid(ctx):
        seen.append(ctx.payment.status)

    await bot.send_payment(1, provider, "o1", 99000, "Заказ")
    assert server.details("sendInvoice")["pay_url"] == "https://yoomoney.ru/c"
    event = await provider.parse_notification(json.dumps({"event": "payment.succeeded", "object": {"id": "p1"}}).encode(), {})
    await bot.apply_payment(event)
    assert server.details("setInvoiceStatus") == {"chat_id": 1, "message_id": 7, "status": "paid"}
    assert seen == ["paid"]


def test_every_server_method_is_wrapped():
    source = Path(__file__).resolve().parents[2] / "wekon_backend/internal/modules/bots/internal/transport/http/bot_call.go"
    if not source.exists():
        pytest.skip("нет исходников сервера")
    text = source.read_text(encoding="utf-8")
    block = text[text.index("var knownBotMethods"):]
    known = set(re.findall(r'"([a-zA-Z]+)":\s*\{\}', block[:block.index("\n}\n")]))
    client = (Path(__file__).resolve().parents[1] / "wekon/client.py").read_text(encoding="utf-8")
    wrapped = set(re.findall(r'(?:call|_send_media)\("([a-zA-Z]+)"', client))
    missing = known - wrapped - {"answerWebAppQuery", "savePreparedInlineMessage"}
    assert not missing, f"нет обёрток: {sorted(missing)}"


def kitchen_sink():
    """Всё, что умеет библиотека: этот набор проверяется настоящими правилами сервера."""
    card = (Card().header("Заголовок", "Подпись", icon="📦", status="Статус", tone="accent").header("Бот", avatar=True)
            .text("Текст", "caption").image("https://example.com/a.png", "Подпись", 1.5).gallery(["https://example.com/a.png", ("https://example.com/b.png", "Б")])
            .divider().callout("Важно", "warning", "⚠").fields({"Имя": "Анна"}, columns=2).stats(("Цена", 100, "success"), stat("Штук", "2"))
            .progress(0.5, "Прогресс", style="ring", size="m", caption="почти", tone="accent").progress(indeterminate=True).progress(0.5, style="segments", segments=4, value_text="2/4")
            .steps(("Первый", "done"), step("Второй", "active", "идёт"), step("Третий")).rating(4.5, 10, "оценка").tags("Хит", ("Новинка", "accent"))
            .countdown(timedelta(minutes=5), "Осталось", "Всё").list(["а", "б"], ordered=True).table(["Товар", "Цена"], [["А", 100]])
            .collapsible("Ещё", "Текст", True).tabs({"Один": Card().text("1"), "Два": Card().text("2")}).buttons(("Кнопка", "b"), url_button("Сайт", "https://wekon.ru"))
            .select("s", [("a", "А", "🍎"), ("b", "Б")], "a", label="Выбор", placeholder="Что", haptic="selection").toggle("t", "Флаг", "Описание", True)
            .slider("sl", 0, 10, 5, step=5, label="Сколько", unit="шт").stepper("st", 1, 5)
            .grid(3, [cell("1", "a", selected=True), gap(), cell("10:30", "b", disabled=True)], select="multiple", data="g", max=2)
            .grid(2, ["1", cell("2", tone="success", color="#112233"), cell("3", data="press")]))
    card2 = Card().checklist("c", [("a", "А"), item("b", "Б", "Описание", icon="🧳", checked=True, disabled=True)], label="Дела").checklist("r", [("a", "А")], select="single", max=1)
    card2.theme(gradient=["#1B1F3B", "#3A2D6B"], angle=135, text="light", accent="#FFD60A", radius="l", shape="ticket")
    card2.cover("https://example.com/poster.jpg", "Концерт", "12 ноября", mode="overlay", aspect=1.6, fade=True,
                badge=badge("−30%", "top_left", "notch", "danger"), tags=["18+", ("Хит", "accent")])
    card2.text("12 490 ₽", "display", "center").stats(("км", 12), ("мин", 65), style="inline").divider("ticket")
    card2.buttons(button("Купить", "buy", style="primary"), style="large").buttons(url_button("Подробнее", "https://wekon.ru"), style="link")
    card3 = (Card().tiles(tile("Свет", "70%", icon="💡", fill=0.7, fill_style="wave", tone="warning", span=2, data="light"), ("Тепло", "22°"), columns=2)
             .chart({"Пн": 1200, "Вт": 2400}, label="Расходы", unit="₽", max=3000).chart([bar("Янв", 5, highlight=True), ("Фев", 7)])
             .carousel(["https://example.com/1.jpg", ("https://example.com/2.jpg", "Солярис"), poster("https://example.com/3.jpg", "Дюна", badge="IMAX", data="film")], aspect=0.68)
             .chips("day", [chip("17", "17", "ПН", selected=True), ("18", "18", "ВТ"), "19"], label="День").chips("tags", ["a", "b"], select="multiple", max=2, layout="wrap")
             .qr("TICKET-7F3K", "Покажите на входе", size="m", style="dots").text("12 480 ₽", "dots", "center"))
    card2.calendar("d", month="2026-12", min="2026-12-01", max="2027-01-31", disabled=["2026-12-07"], marks=[mark("2026-12-03", "success", "окна")], selected=["2026-12-04"], select="multiple", max_selected=2)
    form = (Form("f", "Анкета", submit="Готово").page("Шаг 1").text("a", "А", required=True, placeholder="?", hint="!", default="x", max_length=20).textarea("b", "Б")
            .number("c", "В", min=1, max=5, default=2).select("d", "Г", [("x", "Икс")], default="x").multiselect("e", "Д", [("x", "Икс")], default=["x"])
            .page("Шаг 2").date("f", "Е", min="2026-01-01").time("g", "Ж", default="10:00").toggle("h", "З", default=True).color("i", "И", default="#112233"))
    inline_keyboard = (Keyboard(columns=4).row(button("Да", "y", icon="✅", style="primary", hint="Подсказка", width=2, height=2, haptic="light", loading="border"), button("Нет", "n", color="#112233"))
                       .row(button("Точно", "c", confirm={"title": "Точно?", "text": "Нельзя вернуть", "ok_text": "Да", "destructive": True}), button("Флаг", "t", checked=True))
                       .row(button("Срок", "e", expires_at=__import__("time").time().__int__() + 3600), button("Удержание", "l", long_data="hold")).url("Сайт", "https://wekon.ru").copy("Код", "PROMO").inline("Inline", "запрос").inline("Здесь", here=True).form("Форма", form))
    dial_keyboard = Keyboard().dial("Сумма", "amount", 0, 5000, 1000, step=50, unit="₽", major=10).button("Готово", "done")
    reply_dials = ReplyKeyboard().dial("Яркость", "bright", 0, 100, 50, unit="%", reset=50).dial("Тон", "tone", -50, 50, 0, reset=0).row("Готово")
    reply = (ReplyKeyboard("Выберите", one_time=True).row("Да", "Нет").users("Люди", 1, 3, icon="👥", hint="Выбрать").chat("Группа", 2).contact("Телефон").date("Дата", 3, min="2026-01-01")
             .time("Время", 4).color("Цвет", 5).file("Файл", 6, image_only=True))
    return {"components": [card.to_list(), card2.to_list(), card3.to_list()], "markups": [inline_keyboard.to_dict(), reply.to_dict(), dial_keyboard.to_dict(), reply_dials.to_dict()], "inline": [inline.article("1", "Статья", "Текст", description="О"), inline.article("2", "Карточка", card=Card().header("К")), inline.photo("3", "https://example.com/a.png", caption="Фото")]}


def test_kitchen_sink_builds(tmp_path):
    data = kitchen_sink()
    assert len({block["type"] for blocks in data["components"] for block in blocks}) == 32
    target = __import__("os").environ.get("WEKON_DUMP")
    if target:
        Path(target).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
