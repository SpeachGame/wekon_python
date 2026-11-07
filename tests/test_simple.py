import json
import threading
import time

import httpx
import pytest

from test_library import Server, callback, message
from wekon import Bot, Card, Form
from wekon.keyboards import markup_of


@pytest.fixture
def server():
    return Server()


@pytest.fixture
def bot(server):
    return Bot("shop_bot", "key", http=httpx.AsyncClient(transport=httpx.MockTransport(server)))


def test_plain_functions_without_await(bot, server):
    @bot.command("start")
    def start(msg):
        sent = msg.reply("Привет")
        assert sent.message_id == 7

    bot.process_update(message("/start"))
    assert server.details("sendMessage")["text"] == "Привет"


def test_methods_work_outside_handlers(bot, server):
    sent = bot.send_message(1, "Сам по себе")
    assert sent.chat_id == 1 and server.details("sendMessage")["text"] == "Сам по себе"


def test_simple_buttons_and_keys(bot, server):
    @bot.command("start")
    def start(msg):
        msg.reply("Меню", buttons=[[("Да", "yes"), ("Нет", "no")], ("Сайт", "https://wekon.ru")])
        msg.reply("Выберите", keys=["Да", "Нет"])

    bot.process_update(message("/start"))
    calls = [body["details"] for path, body, _ in server.calls if path == "/bot/call"]
    rows = calls[0]["reply_markup"]["inline_keyboard"]
    assert rows[0][1] == {"text": "Нет", "callback_data": "no"} and rows[1][0]["url"] == "https://wekon.ru"
    assert calls[1]["reply_markup"]["keyboard"] == [[{"text": "Да"}, {"text": "Нет"}]]


def test_buttons_shortcuts():
    assert markup_of({"Да": "yes"}) == {"inline_keyboard": [[{"text": "Да", "callback_data": "yes"}]]}
    assert markup_of([("Форма", Form("f", "Т").text("a", "А"))])["inline_keyboard"][0][0]["form"]["callback_data"] == "f"


def test_ask_waits_for_the_next_message(bot, server):
    answers = []

    @bot.command("start")
    def start(msg):
        answers.append(msg.ask("Как вас зовут?", placeholder="Имя"))

    @bot.message()
    def other(msg):
        answers.append("общий обработчик")

    enqueue = lambda raw: bot._loop.loop.call_soon_threadsafe(bot._core._enqueue, raw)
    enqueue(message("/start"))
    deadline = time.time() + 5
    while "sendMessage" not in server.methods() and time.time() < deadline:
        time.sleep(0.01)
    enqueue(message("Анна", update_id=2))
    bot.wait_idle()
    assert answers == ["Анна"], "ответ на вопрос не идёт в общий обработчик"


def test_ask_timeout_and_command_cancel(bot):
    answers = []

    @bot.command("start")
    def start(msg):
        answers.append(msg.ask("Вопрос", timeout=0.05))

    bot.process_update(message("/start"))
    assert answers == [None]


def test_long_handler_does_not_block_other_chats(bot):
    order = []
    release = threading.Event()

    @bot.message()
    def handle(msg):
        if msg.text == "долго":
            release.wait(5)
        order.append(msg.text)
        release.set()

    enqueue = lambda raw: bot._loop.loop.call_soon_threadsafe(bot._core._enqueue, raw)
    enqueue(message("долго", 1, chat=1))
    enqueue(message("быстро", 2, chat=2))
    time.sleep(0.05)
    bot.wait_idle()
    assert order == ["быстро", "долго"]


def test_card_edit_and_error_handler(bot, server):
    errors = []

    @bot.callback("q")
    def change(msg):
        msg.edit(card=Card().header("Новая"))
        raise ValueError("сломалось")

    @bot.error
    def on_error(msg):
        errors.append(str(msg.exception))

    bot.process_update(callback("q"))
    assert errors == ["сломалось"]
    assert server.details("editMessageText")["text"] == "Новая" and "answerCallbackQuery" in server.methods()


def test_async_handlers_still_work_in_simple_bot(bot, server):
    @bot.command("start")
    async def start(ctx):
        await ctx.reply("Асинхронно")

    bot.process_update(message("/start"))
    assert server.details("sendMessage")["text"] == "Асинхронно"


async def test_asgi_webhook_in_simple_mode(bot):
    seen = []

    @bot.message()
    def text(msg):
        seen.append(msg.text)

    app = bot.asgi(secret="s3")
    sent = []

    async def send(event):
        sent.append(event)

    async def receive():
        return {"body": json.dumps(message("вебхук")).encode()}

    await app({"type": "http", "method": "POST", "path": "/wekon", "headers": [(b"x-wekon-bot-api-secret-token", b"s3")]}, receive, send)
    assert sent[0]["status"] == 200
    bot.wait_idle()
    assert seen == ["вебхук"]


def test_wait_idle_does_not_spin_on_finished_tasks(bot):
    """Задача уже завершена, а её колбэк ещё не сработал: раньше здесь был вечный цикл."""
    @bot.message()
    def text(msg):
        pass

    for number in range(200):
        bot._loop.loop.call_soon_threadsafe(bot._core._enqueue, message("x", number + 1, chat=number))
        bot.wait_idle()
    assert not bot._core._workers
