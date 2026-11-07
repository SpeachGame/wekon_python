"""Бот-витрина: всё, что умеет библиотека wekon, в одном файле.

Запуск: WEKON_BOT_NIK=имя WEKON_BOT_API_KEY=ключ python showcase_bot.py

/start — меню разделов. Каждый раздел показывает свою группу возможностей и в конце пишет,
какие шаги прошли. /selftest прогоняет всё подряд и выдаёт сводку, /coverage сверяет код бота
со всеми функциями библиотеки. Цель примера — охват, а не красота.
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import wekon
from wekon import (Attachment, AuthError, BadRequest, Bot, CallbackQuery, Card, Chat, Client, Conflict, Context, Forbidden, ForceReply, Form,
                   InlineQuery, Keyboard, MemoryStorage, Message, NetworkError, NotFound, NotSupported, RateLimited, RemoveKeyboard, ReplyKeyboard,
                   Sent, ServerError, Update, User, WekonError, bind, button, cell, check_login, copy_button, form_button, gap, inline,
                   inline_button, item, login_button, mark, option, payments, stat, step, tag, url_button)

log = logging.getLogger("showcase")

ASSETS = Path(__file__).parent / "assets"
PICTURE = "https://prod.wekon.ru/api/v1/bot/demo/picture/{}.png"
PAY_URL = "https://wekon.ru/pay/{}"
#: Реакция из каталога Wekon: id берётся оттуда, это не символ.
REACTION = os.environ.get("WEKON_REACTION", "339")
#: Номер стикера Wekon: без него шаг со стикером пропускается.
STICKER = os.environ.get("WEKON_STICKER", "")

#: Все типы событий сервера.
EVENT_TYPES = ["message", "photo", "audio", "document", "video", "animation", "voice", "video_note", "media_group", "edited_message",
               "deleted_message", "reaction", "chat_member", "callback_query", "inline_query", "chosen_inline_result", "poll_answer",
               "poll", "my_chat_member"]
ALLOWED_UPDATES = ["message", "edited_message", "deleted_message", "message_reaction", "chat_member", "callback_query", "inline_query",
                   "chosen_inline_result", "poll_answer", "poll", "my_chat_member"]

bot = Bot(os.environ.get("WEKON_BOT_NIK", "showcase_bot"), base_url=os.environ.get("WEKON_API_BASE", "https://prod.wekon.ru/api/v1"),
          storage=MemoryStorage())

SECTIONS: dict = {}
journal: collections.Counter = collections.Counter()
last_events: collections.deque = collections.deque(maxlen=12)
uploaded: dict = {}
controls: dict = {}
games: dict = {}
polls: dict = {}


def section(name: str, title: str, automatic: bool = True):
    """Раздел меню. ``automatic=False`` — только по кнопке, /selftest его пропускает."""
    def register(handler):
        SECTIONS[name] = (title, handler, automatic)
        return handler
    return register


class Report:
    """Шаги раздела: сбой одного шага не мешает остальным."""

    def __init__(self, title: str) -> None:
        self.title, self.done, self.failed = title, [], []

    def do(self, name: str, action):
        try:
            result = action()
            self.done.append(name)
            return result
        except Exception as error:  # отчёт важнее одного сбоя
            self.failed.append((name, f"{type(error).__name__}: {error}"))
            log.warning("%s / %s: %r", self.title, name, error)
            return None

    def expect(self, name: str, error_type: type, action) -> None:
        """Шаг, который обязан завершиться ошибкой нужного типа."""
        def check() -> None:
            try:
                action()
            except error_type:
                return
            raise AssertionError(f"ожидалась {error_type.__name__}")
        self.do(name, check)

    def card(self) -> Card:
        card = Card(f"{self.title}: {len(self.done)} из {len(self.done) + len(self.failed)}")
        card.header(self.title, "Итог раздела", status="Ошибки" if self.failed else "Всё прошло", tone="danger" if self.failed else "success")
        card.stats(stat("Прошло", len(self.done), "success"), stat("Ошибок", len(self.failed), "danger" if self.failed else None))
        if self.failed:
            card.collapsible("Что не прошло", "\n".join(f"{name}: {text}"[:300] for name, text in self.failed)[:2000], expanded=True)
        return card


def back() -> Keyboard:
    return Keyboard().row(("🏠 Меню", "menu"))


def run_section(msg, name: str) -> Report:
    title, handler, _ = SECTIONS[name]
    report = Report(title)
    try:
        handler(report, msg.chat_id, msg)
    except Exception as error:
        report.failed.append(("раздел целиком", f"{type(error).__name__}: {error}"))
        log.exception("Раздел %s", name)
    log.info("SECTION %s", json.dumps({"section": name, "done": len(report.done), "failed": report.failed}, ensure_ascii=False))
    return report


def fid(name: str, media_type: str | None = None) -> str:
    """Файл загружается один раз, дальше идёт по ``file_id``."""
    if name not in uploaded:
        uploaded[name] = bot.upload(ASSETS / name, media_type=media_type)
    return uploaded[name]


# --- меню и команды ---

def menu_keyboard() -> Keyboard:
    keyboard = Keyboard()
    titles = list(SECTIONS.items())
    for index in range(0, len(titles), 2):
        keyboard.row(*[(entry[1][0], f"sec:{entry[0]}") for entry in titles[index:index + 2]])
    return keyboard.row(("🧪 Проверка", "selftest"), ("📊 Покрытие", "coverage"), ("📜 Журнал", "journal"))


def menu_card(user: wekon.User, start: str = "") -> Card:
    card = Card("Витрина библиотеки wekon")
    card.header("Витрина wekon", f"Для @{user.nik or user.id}", avatar=True, status="В работе", tone="success")
    card.text("Выберите раздел. Каждый покажет свою часть библиотеки и в конце напишет, что прошло.")
    if start:
        card.callout(f"Вы пришли по ссылке с меткой: {start}", "accent", "🔗")
    return card


@bot.command("start", "menu", "help")
def start(msg):
    # Метка ссылки: /start promo → msg.args, она же в message.start_param.
    label = msg.message.start_param or " ".join(msg.args)
    msg.reply(card=menu_card(msg.user, label), keyboard=menu_keyboard())


@bot.callback("menu")
def open_menu(msg):
    msg.reply(card=menu_card(msg.user), keyboard=menu_keyboard())


@bot.callback("sec:{name}")
def open_section(msg):
    name = msg.params["name"]
    msg.answer(f"Открываю: {SECTIONS[name][0]}")
    report = run_section(msg, name)
    msg.reply(card=report.card(), keyboard=back())


@bot.callback("selftest")
@bot.command("selftest")
def selftest(msg):
    msg.reply("Прогоняю все разделы. Лимит в личном чате — сообщение в секунду, поэтому это займёт несколько минут.")
    reports = [run_section(msg, name) for name, entry in SECTIONS.items() if entry[2]]
    done = sum(len(report.done) for report in reports)
    failed = [(report.title, *entry) for report in reports for entry in report.failed]
    log.info("SELFTEST %s", json.dumps({"done": done, "failed": failed}, ensure_ascii=False))
    card = Card(f"Проверка: {done} прошло, {len(failed)} ошибок")
    card.header("Сводка", "Все разделы", status="Есть ошибки" if failed else "Всё прошло", tone="danger" if failed else "success")
    card.stats(("Прошло", done, "success"), ("Ошибок", len(failed), "danger" if failed else None))
    card.table(["Раздел", "Да", "Нет"], [[report.title[:28], len(report.done), len(report.failed)] for report in reports][:20])
    if failed:
        card.collapsible("Ошибки", "\n".join(f"{name}: {text}"[:240] for _, name, text in failed)[:2000], expanded=True)
    msg.reply(card=card, keyboard=back())


def missing_from_code() -> list:
    """Что из библиотеки ни разу не названо в этом файле."""
    source = Path(__file__).read_text(encoding="utf-8")
    names = set(wekon.__all__)
    for owner in (Client, Context, Card, Form, Keyboard, ReplyKeyboard):
        names |= {name for name in vars(owner) if not name.startswith("_") and name not in ("to_dict", "to_list")}
    return sorted(name for name in names if not re.search(rf"\b{name}\b", source))


@bot.callback("coverage")
@bot.command("coverage")
def coverage(msg):
    missing = missing_from_code()
    card = Card("Покрытие библиотеки").header("Покрытие", "Функции библиотеки в коде этого бота", status="Полное" if not missing else "Неполное",
                                              tone="success" if not missing else "warning")
    if missing:
        card.list(missing[:20])
    msg.reply(card=card, keyboard=back())


@bot.callback("journal")
@bot.command("journal")
def show_journal(msg):
    card = Card("Журнал событий").header("Журнал событий", "Что пришло с запуска")
    card.table(["Тип", "Сколько"], [[kind, count] for kind, count in journal.most_common(20)] or [["пока пусто", 0]])
    if last_events:
        card.list(list(last_events)[-10:])
    msg.reply(card=card, keyboard=back())


# --- текст и отправка ---

@section("text", "✍️ Текст")
def text_section(r: Report, c: int, msg) -> None:
    first = r.do("send_message: текст", lambda: bot.send_message(c, "Обычный текст"))
    html = ('<b>жирный</b> <i>курсив</i> <u>подчёркнутый</u> <s>зачёркнутый</s> <code>код</code> '
            '<tg-spoiler>скрытый</tg-spoiler> <a href="https://wekon.ru">ссылка</a>\n<pre>блок кода</pre>')
    r.do("html=True", lambda: bot.send_message(c, html, html=True))
    r.do("ответ на сообщение: reply_to", lambda: bot.send_message(c, "Ответ", reply_to=first))
    r.do("цитата: quote", lambda: bot.send_message(c, "С цитатой", reply_to=first, quote="текст", quote_position=7, allow_without_reply=True))
    r.do("без звука: silent", lambda: bot.send_message(c, "Тихое", silent=True))
    r.do("защита: protect", lambda: bot.send_message(c, "Нельзя переслать", protect=True))
    r.do("самоудаление: self_destruct", lambda: bot.send_message(c, "Исчезну через 30 секунд", self_destruct=30))
    r.do("превью ссылки выключено", lambda: bot.send_message(c, "https://wekon.ru без превью", link_preview=False))
    r.do("настройки превью", lambda: bot.send_message(c, "https://wekon.ru с настройками", link_preview={"url": "https://wekon.ru", "prefer_small_media": True}))
    r.do("простая запись buttons= и keys=", lambda: msg.reply("Кнопки словарём", buttons={"Да": "ans:toast", "Сайт": "https://wekon.ru"}))
    r.do("buttons= списком", lambda: msg.reply("Кнопки списком", buttons=[[("Рядом", "ans:toast"), ("Рядом", "ans:alert")], ("Отдельно", "ans:toast")]))

    edited = r.do("edit_message: готовлю", lambda: bot.send_message(c, "Сейчас изменюсь"))
    r.do("edit_message: текст", lambda: bot.edit_message(c, edited.message_id, "Изменено", keyboard=Keyboard().row(("Кнопка", "ans:toast"))))
    r.do("edit_message: карточка", lambda: bot.edit_message(c, edited.message_id, "С карточкой", card=Card().header("Карточка"), html=True))
    r.do("edit_keyboard: заменить", lambda: bot.edit_keyboard(c, edited.message_id, Keyboard().row(("Другая", "ans:toast"))))
    r.do("edit_keyboard: убрать", lambda: bot.edit_keyboard(c, edited.message_id))

    forwarded = r.do("forward_message", lambda: bot.forward_message(c, c, first.message_id))
    copied = r.do("copy_message", lambda: bot.copy_message(c, c, first.message_id, caption="Копия", keyboard=back()))
    many = r.do("forward_messages", lambda: bot.forward_messages(c, c, [first.message_id]))
    more = r.do("copy_messages", lambda: bot.copy_messages(c, c, [first.message_id]))
    r.do("delete_message", lambda: bot.delete_message(c, forwarded.message_id))
    r.do("delete_messages", lambda: bot.delete_messages(c, [copied.message_id, many[0].message_id, more[0].message_id]))

    r.do("sendMessageDraft: save_draft", lambda: bot.save_draft(c, "Черновик", keyboard=back()))
    for action in ("typing", "upload_photo", "record_voice", "choose_sticker"):
        r.do(f"send_chat_action: {action}", lambda: bot.send_chat_action(c, action))
    r.do("msg.typing", lambda: msg.typing())
    r.do("react: поставить", lambda: bot.react(c, first.message_id, REACTION))
    r.do("react: снять", lambda: bot.react(c, first.message_id))
    r.do("read_messages", lambda: bot.read_messages(c, first.message_id))
    r.do("call: любой метод по имени", lambda: bot.call("getMe"))

    planned = r.do("schedule: через timedelta", lambda: bot.send_message(c, "Отложенное: придёт через 2 минуты", schedule=timedelta(minutes=2)))
    moment = datetime.now(timezone.utc) + timedelta(minutes=10)
    canceled = r.do("schedule: через datetime", lambda: bot.send_message(c, "Это отменится", schedule=moment))
    r.do("get_scheduled_messages", lambda: bot.get_scheduled_messages(c))
    r.do("delete_scheduled_message", lambda: bot.delete_scheduled_message(canceled.scheduled_id))
    log.info("Отложено: %s", planned)

    r.expect("ошибка: неверные данные", BadRequest, lambda: bot.send_message(c, ""))
    r.expect("ошибка: нет сообщения", WekonError, lambda: bot.delete_message(c, 1))


# --- медиа ---

@section("media", "🖼 Отправка медиа")
def media_section(r: Report, c: int, msg) -> None:
    keyboard = Keyboard().row(("Кнопка под фото", "ans:toast"))
    photo = r.do("send_photo: Path", lambda: bot.send_photo(c, ASSETS / "photo1.png", "Фото <b>с подписью</b>", html=True, keyboard=keyboard))
    r.do("send_photo: bytes", lambda: bot.send_photo(c, (ASSETS / "photo2.png").read_bytes(), "Из байтов"))
    r.do("send_photo: https", lambda: bot.send_photo(c, PICTURE.format(1), "По ссылке"))
    r.do("send_photo: file_id", lambda: bot.send_photo(c, fid("photo3.png"), "По file_id"))
    r.do("spoiler и подпись сверху", lambda: bot.send_photo(c, fid("photo3.png"), "Подпись сверху", spoiler=True, caption_above=True))
    r.do("send_document", lambda: bot.send_document(c, ASSETS / "note.txt", "Документ"))
    r.do("send_audio", lambda: bot.send_audio(c, ASSETS / "music.mp3", "Музыка"))
    r.do("send_voice", lambda: bot.send_voice(c, ASSETS / "voice.ogg"))
    r.do("send_video", lambda: bot.send_video(c, ASSETS / "video.mp4", "Видео"))
    r.do("send_animation", lambda: bot.send_animation(c, ASSETS / "anim.gif", "Анимация"))
    r.do("send_video_note", lambda: bot.send_video_note(c, ASSETS / "circle.mp4"))
    r.do("send_media_group", lambda: bot.send_media_group(c, [fid("photo1.png"), {"type": "photo", "file_id": fid("photo2.png")},
                                                               {"type": "video", "file_id": fid("video.mp4")}], "Альбом"))
    r.do("msg.send_photo: короткая запись", lambda: msg.send_photo(fid("photo1.png"), "Через контекст"))
    if STICKER:
        r.do("send_sticker", lambda: bot.send_sticker(c, STICKER))
    else:
        r.do("send_sticker: пропущен, нет WEKON_STICKER", lambda: None)
    r.do("edit_media", lambda: bot.edit_media(c, photo.message_id, fid("photo2.png"), caption="Заменённое фото", keyboard=keyboard))
    r.do("edit_caption", lambda: bot.edit_caption(c, photo.message_id, "Новая <i>подпись</i>", html=True, keyboard=keyboard))
    r.do("get_file: свой файл", lambda: bot.get_file(file_id=fid("photo1.png")))
    own = r.do("download: свой файл", lambda: bot.download(fid("photo1.png")))
    r.do("скачанное совпало с исходным", lambda: _same(own, (ASSETS / "photo1.png").read_bytes()))


def _same(left: bytes, right: bytes) -> None:
    if left != right:
        raise AssertionError(f"разные байты: {len(left)} и {len(right)}")


@section("incoming", "📥 Приём файлов", automatic=False)
def incoming_section(r: Report, c: int, msg) -> None:
    r.do("подсказка", lambda: msg.reply("Пришлите фото, видео, голосовое, кружок, аудио, файл или альбом: бот опишет каждое вложение, "
                                        "скачает его целиком и вернёт обратно."))


@bot.message(types=["photo", "video", "animation", "voice", "video_note", "audio", "document", "media_group"])
def receive_files(msg):
    message = msg.message
    table = []
    for attachment in message.attachments:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "file"
            msg.download(attachment, target)
            data = msg.download(attachment)
            same = target.read_bytes() == data and len(data) == attachment.size
        info = msg.bot.get_file(attachment_id=attachment.id)
        table.append([attachment.media_type, attachment.name[:18] or "—", attachment.size, attachment.duration or f"{attachment.width}×{attachment.height}",
                      "да" if same and info.get("download_url") else "нет"])
    card = Card("Файлы получены").header("Получено", f"Вложений: {len(message.attachments)}, событие {message.kind}")
    card.table(["Вид", "Имя", "Байт", "Длит./размер", "Скачан"], table)
    msg.reply(card=card, reply_to=message.id)
    first = message.file
    if first and message.kind == "voice":
        msg.send_voice(first.ref)
    elif first and message.kind == "video_note":
        msg.send_video_note(first.ref)
    elif first and message.kind == "photo":
        msg.send_photo(first.ref, "То же фото: attachment:id", keyboard=back())
    elif first and message.kind in ("video", "animation", "audio", "document"):
        getattr(msg, f"send_{message.kind}")(first.ref, "Тот же файл: attachment:id", keyboard=back())


# --- кнопки ---

def send_login_buttons(c: int) -> None:
    """Кнопка входа ведёт только на домен бота: его задают командой /setdomain у god_bot."""
    url = "https://wekon.ru/login"
    keyboard = Keyboard().login("Войти на сайт", url, forward_text="Вход через бота").row(login_button("login_button", url))
    try:
        bot.send_message(c, "Вход на сайт через бота", keyboard=keyboard)
    except BadRequest as error:
        if error.code != "login_domain_mismatch":
            raise
        bot.send_message(c, "Кнопку входа сервер не принял: у бота не задан домен wekon.ru. Команда /setdomain у god_bot.")


@section("buttons", "🔘 Кнопки")
def buttons_section(r: Report, c: int, msg) -> None:
    looks = (Keyboard()
             .row(button("Основная", "ans:toast", style="primary"), button("Успех", "ans:toast", style="success"), button("Опасная", "ans:toast", style="danger"))
             .row(button("Свой цвет", "ans:toast", color="#7A5CFF", icon="🎨"), button("Удержи меня", "ans:toast", hint="Подсказка при удержании"))
             .row(button("Отметка", "ans:toast", checked=True), button("Спросит", "ans:toast", confirm="Точно нажать?"))
             .row(button("Окно подтверждения", "ans:toast", confirm={"title": "Удалить?", "text": "Отменить будет нельзя", "ok_text": "Да", "destructive": True}))
             .row(button("Истечёт за 5 минут", "ans:toast", expires_at=int(time.time()) + 300))
             .row(button("Нажми или удержи", "ans:short", long_data="ans:long", haptic="medium")))
    r.do("вид кнопок", lambda: bot.send_message(c, "Вид и поведение кнопок", keyboard=looks))

    feel = Keyboard()
    feel.row(*[button(kind[:5], "ans:toast", haptic=kind) for kind in ("light", "medium", "heavy", "selection")])
    feel.row(*[button(kind[:5], "ans:toast", haptic=kind) for kind in ("success", "warning", "error")])
    feel.row(*[button(kind, "ans:slow", loading=kind) for kind in ("spinner", "border", "none")])
    r.do("отклик и ожидание", lambda: bot.send_message(c, "Вибрация и индикатор ожидания", keyboard=feel))

    grid = (Keyboard(columns=4)
            .row(button("⬅️", "ans:toast", height=2), button("⬆️", "ans:toast", width=2), button("➡️", "ans:toast", height=2))
            .row(button("⬇️", "ans:toast", width=2))
            .row(button("Вся ширина", "ans:toast", width=4)))
    r.do("сетка кнопок: columns, width, height", lambda: bot.send_message(c, "Кнопки разного размера", keyboard=grid))

    kinds = (Keyboard()
             .url("Ссылка", "https://wekon.ru", icon="🌐")
             .copy("Скопировать код", "WEKON-2026")
             .form("Открыть форму", sample_form())
             .inline("Inline здесь", "демо", here=True)
             .inline("Inline в другой чат", "демо")
             .button("Через .button", "ans:toast", style="primary"))
    r.do("виды кнопок", lambda: bot.send_message(c, "Все виды кнопок", keyboard=kinds))
    parts = (Keyboard()
             .row(url_button("url_button", "https://wekon.ru"), copy_button("copy_button", "текст"))
             .row(form_button("form_button", sample_form()), inline_button("inline_button", "демо", True))
             .row(("Рядом", "ans:toast")).add(("в тот же ряд", "ans:toast")))
    r.do("кнопки отдельными функциями", lambda: bot.send_message(c, "То же отдельными функциями", keyboard=parts))
    r.do("кнопка входа", lambda: send_login_buttons(c))

    answers = (Keyboard()
               .row(("Всплывашка", "ans:toast"), ("Окно", "ans:alert"))
               .row(("У кнопки", "ans:near"), ("С вибрацией", "ans:haptic"))
               .row(("Открыть ссылку", "ans:url"), ("С кэшем 30 с", "ans:cache"))
               .row(("Ошибка в обработчике", "ans:crash")))
    r.do("ответы на нажатие", lambda: bot.send_message(c, "Как бот отвечает на нажатие", keyboard=answers))

    fields = {"auth_date": str(int(time.time())), "user_id": "1"}
    text = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    signed = hmac.new(hashlib.sha256(bot.api_key.encode()).digest(), text.encode(), hashlib.sha256).hexdigest()
    r.do("check_login: подпись верна", lambda: _true(check_login({**fields, "hash": signed}, bot.api_key)))
    r.do("check_login: подделка отвергнута", lambda: _true(not check_login({**fields, "hash": "0" * 64}, bot.api_key)))


def _true(value: bool) -> None:
    if not value:
        raise AssertionError("условие не выполнено")


@bot.callback("ans:{kind}")
def answer_button(msg):
    kind = msg.params["kind"]
    if kind == "toast":
        msg.answer("Короткая всплывашка")
    elif kind == "alert":
        msg.answer("Окно с кнопкой ОК", alert=True)
    elif kind == "near":
        msg.answer("Рядом с кнопкой", near_button=True)
    elif kind == "haptic":
        msg.answer("С откликом", haptic="success")
    elif kind == "url":
        msg.answer(url="https://wekon.ru")
    elif kind == "cache":
        msg.answer("Запомнено на 30 секунд", cache_time=30)
    elif kind == "short":
        msg.answer("Нажатие: data")
    elif kind == "long":
        msg.answer("Удержание: long_data", alert=True)
    elif kind == "slow":
        time.sleep(2)
        msg.answer("Ответил через 2 секунды")
    elif kind == "crash":
        raise ValueError("Проверка bot.error: обработчик упал")


# --- клавиатуры ---

@section("keyboards", "⌨️ Клавиатуры")
def keyboards_section(r: Report, c: int, msg) -> None:
    keys = (ReplyKeyboard("Выберите действие", persistent=True)
            .row("Просто текст", "Рядом")
            .button("С оформлением", icon="⭐", style="primary", hint="Подсказка")
            .users("👥 Люди", request_id=1, max_quantity=3)
            .users("🤖 Бот", request_id=2, bots=True)
            .chat("💬 Группа", request_id=3)
            .chat("📢 Канал", request_id=4, channel=True, created=True)
            .contact("📱 Телефон")
            .date("📅 Дата", request_id=5, min="2026-01-01", max="2027-12-31")
            .time("🕒 Время", request_id=6)
            .color("🎨 Цвет", request_id=7)
            .file("📎 Файл", request_id=8)
            .file("🖼 Только фото", request_id=9, image_only=True, multiple=True))
    r.do("ReplyKeyboard: все запросы", lambda: bot.send_message(c, "Нажимайте: ответы придут сюда", keyboard=keys))
    once = ReplyKeyboard(one_time=True, selective=True, resize=False).row("Один раз")
    r.do("ReplyKeyboard: одноразовая", lambda: bot.send_message(c, "Эта исчезнет после нажатия", keyboard=once))
    r.do("keys= простая запись", lambda: msg.reply("Клавиатура списком", keys=[["Да", "Нет"], ["Отмена"]]))
    r.do("keys= убрать", lambda: msg.reply("Убрано через keys=[]", keys=[]))
    r.do("вернуть полную", lambda: bot.send_message(c, "Снова все запросы", keyboard=keys))
    r.do("RemoveKeyboard и ForceReply проверяются кнопками", lambda: msg.reply("Ещё два вида", keyboard=Keyboard().row(("Убрать клавиатуру", "kb:remove"), ("Попросить ответ", "kb:force"))))


@bot.callback("kb:remove")
def remove_keyboard(msg):
    msg.reply("Клавиатура убрана", keyboard=RemoveKeyboard())


@bot.callback("kb:force")
def force_reply(msg):
    msg.reply("Ответьте на это сообщение", keyboard=ForceReply("Пишите здесь"))


# --- карточки: показ ---

@section("cards", "🃏 Карточки: показ")
def cards_section(r: Report, c: int, msg) -> None:
    head = (Card("Шапка и текст")
            .header("Заказ №42", "Доставка завтра", icon="📦", status="В пути", tone="accent")
            .text("Обычный текст", "body").text("Мелкий текст", "caption").text("Заголовок", "title")
            .divider()
            .callout("Сведения", "neutral", "ℹ️").callout("Акцент", "accent").callout("Успех", "success").callout("Внимание", "warning").callout("Ошибка", "danger"))
    r.do("header, text, divider, callout", lambda: bot.send_message(c, card=head))
    pictures = (Card("Картинки")
                .image(PICTURE.format(2), "Картинка", 1.6)
                .gallery([PICTURE.format(1), (PICTURE.format(2), "С подписью"), PICTURE.format(3)], 1.5))
    r.do("image, gallery", lambda: bot.send_message(c, card=pictures))
    numbers = (Card("Цифры")
               .fields({"Имя": "Анна", "Город": "Москва"})
               .fields([("Заказ", 42), ("Сумма", "1 499 ₽"), ("Доставка", "завтра"), ("Оплата", "картой")], columns=2)
               .stats(stat("Выручка", "1,2 млн", "success"), ("Заказы", 340), ("Отмены", 12, "danger"))
               .rating(4.6, 128, "Оценка")
               .tags("Новинка", tag("Скидка", "success"), ("Мало", "warning")))
    r.do("fields, stats, rating, tags", lambda: bot.send_message(c, card=numbers))
    progress = (Card("Прогресс")
                .progress(0.7, "Полоса", caption="7 из 10", tone="accent", value_text="70%", show_value=True, animated=True)
                .progress(0.4, "Кольцо", style="ring", size="l")
                .progress(0.5, "Деления", style="segments", segments=8, tone="success")
                .progress(label="Без конца", indeterminate=True)
                .steps(step("Принят", "done"), step("Собран", "active", "Сейчас"), ("В пути", "pending"), step("Сбой", "error"))
                .countdown(timedelta(hours=2), "До конца акции", "Акция закончилась")
                .countdown(datetime.now(timezone.utc) + timedelta(days=1), "По datetime"))
    r.do("progress, steps, countdown", lambda: bot.send_message(c, card=progress))
    lists = (Card("Списки")
             .list(["Обычный", "Список"])
             .list(["Первый", "Второй"], ordered=True)
             .table(["Товар", "Цена"], [["Чай", 300], ["Кофе", 500]])
             .collapsible("Свёрнуто", "Текст внутри")
             .collapsible("Развёрнуто", "Текст внутри", expanded=True)
             .buttons(("В карточке", "ans:toast"), button("Со стилем", "ans:alert", style="primary")))
    r.do("list, table, collapsible, buttons", lambda: bot.send_message(c, card=lists))
    tabs = Card("Вкладки").tabs({"Описание": Card().text("Первая вкладка"), "Цена": Card().stats(("Цена", "300 ₽")), "Отзывы": Card().rating(5, 3)})
    r.do("tabs", lambda: bot.send_message(c, card=tabs))
    empty = Card().text("Запасной текст берётся из блока")
    r.do("fallback_text и список блоков", lambda: bot.send_message(c, card=empty.to_list(), text=empty.fallback_text()))


# --- карточки: управление ---

def control_card(state: dict) -> Card:
    month = date.today().strftime("%Y-%m")
    today = date.today()
    cells = [cell(str(index + 1), f"s{index}", selected=f"s{index}" in state["seats"], disabled=index == 2) for index in range(4)]
    cells += [gap()] + [cell(str(index + 1), f"s{index}", selected=f"s{index}" in state["seats"]) for index in range(4, 7)]
    card = (Card("Управление")
            .header("Элементы управления", "Значения приходят боту сразу")
            .fields({key: str(value)[:60] or "—" for key, value in state.items()}, columns=2)
            .select("ui:plan", [option("basic", "Базовый", "🌱"), ("pro", "Про", "🚀"), ("team", "Команда")], state["plan"], label="Тариф", placeholder="Выберите", haptic="selection")
            .toggle("ui:notify", "Уведомления", "Присылать новости", state["notify"], haptic="light")
            .slider("ui:volume", 0, 100, state["volume"], step=5, label="Громкость", unit="%")
            .stepper("ui:count", 1, 10, state["count"], label="Количество", unit="шт")
            .grid(4, cells, select="multiple", data="ui:seats", max=3, label="Выберите места")
            .grid(3, [cell("A", tone="accent", data="ui:cell:a"), cell("B", color="#2E7D32", data="ui:cell:b"), cell("скр", hidden=True),
                      cell("C", data="ui:cell:c"), "D", cell("E", disabled=True)], label="Клетки с нажатием")
            .checklist("ui:list", [item("a", "Пункт", "С описанием", icon="✅", checked="a" in state["list"]), ("b", "Без описания"),
                                   item("c", "Недоступный", disabled=True)], select="multiple", max=2, label="Флажки")
            .checklist("ui:radio", [item("x", "Вариант X", checked=state["radio"] == "x"), item("y", "Вариант Y", checked=state["radio"] == "y")], select="single", label="Один из")
            .calendar("ui:day", month=month, min=today - timedelta(days=30), max=today + timedelta(days=180), disabled=[today + timedelta(days=1)],
                      marks=[mark(today, "success", "Сегодня"), mark(today + timedelta(days=3), "warning", "Занято")],
                      selected=[state["day"]] if state["day"] else None, select="single", label="День"))
    return card


@section("controls", "🎛 Карточки: управление")
def controls_section(r: Report, c: int, msg) -> None:
    state = {"plan": "pro", "notify": True, "volume": 40, "count": 2, "seats": [], "list": [], "radio": "x", "day": ""}
    sent = r.do("все элементы управления", lambda: bot.send_message(c, card=control_card(state), keyboard=back()))
    if sent:
        controls[sent.message_id] = state
    one = Card("Один день").calendar("ui:one", label="Просто месяц, день приходит нажатием")
    r.do("calendar: без настроек", lambda: bot.send_message(c, card=one))
    many = Card("Несколько").calendar("ui:many", select="multiple", max_selected=3, selected=[date.today()], label="Несколько дней")
    r.do("calendar: несколько дней", lambda: bot.send_message(c, card=many))


@bot.callback("ui:{name}")
def control_changed(msg):
    state = controls.get(msg.message_id)
    name = msg.params["name"]
    if state is None:
        return msg.answer(f"{name} = {msg.value}")
    if name in ("seats", "list"):
        # Несколько пунктов: приходит «значение:1» или «значение:0».
        picked, on = msg.picked
        chosen = set(state[name])
        (chosen.add if on else chosen.discard)(picked)
        state[name] = sorted(chosen)
    elif name in state:
        state[name] = msg.value == "1" if name == "notify" else msg.value
        if name in ("volume", "count"):
            state[name] = float(msg.value)
    msg.answer(f"{name} = {msg.value}", haptic="selection")
    msg.edit(card=control_card(state), keyboard=back())


@bot.callback("ui:cell:{name}")
def cell_pressed(msg):
    msg.answer(f"Клетка {msg.params['name']}", near_button=True)


# --- формы ---

def sample_form() -> Form:
    today = date.today()
    return (Form("signup", "Анкета", submit="Отправить")
            .page("О вас")
            .text("name", "Имя", required=True, placeholder="Анна", hint="Как к вам обращаться", default="Гость", max_length=40)
            .textarea("about", "О себе", placeholder="Пара слов", max_length=300)
            .number("age", "Возраст", min=1, max=120, default=30, hint="Полных лет")
            .page("Предпочтения")
            .select("plan", "Тариф", [option("basic", "Базовый", "🌱"), ("pro", "Про")], required=True, default="pro", hint="Можно сменить")
            .multiselect("topics", "Темы", [("news", "Новости"), ("sale", "Скидки"), ("tips", "Советы")], default=["news"])
            .date("day", "День", min=today, max=today + timedelta(days=90), default=today + timedelta(days=1), required=True)
            .time("hour", "Время", default="12:00", hint="По Москве")
            .toggle("agree", "Согласен", hint="Можно отозвать", placeholder="Получать письма", default=True)
            .color("color", "Цвет", default="#3478F6"))


@section("forms", "📝 Формы")
def forms_section(r: Report, c: int, msg) -> None:
    r.do("форма на две страницы", lambda: bot.send_message(c, "Форма: все виды полей", keyboard=Keyboard().form("Открыть анкету", sample_form(), style="primary")))
    short = Form("poll:7", "Короткая").text("answer", "Ответ")
    r.do("форма с шаблоном в data", lambda: msg.reply("Форма с номером в data", buttons=[("Короткая форма", short)]))


@bot.form("signup")
def form_signup(msg):
    card = Card("Анкета принята").header("Анкета принята", status="Готово", tone="success")
    card.table(["Поле", "Значение"], [[key, json.dumps(value, ensure_ascii=False)[:40]] for key, value in list(msg.form.items())[:20]])
    msg.answer("Принято")
    msg.reply(card=card, keyboard=back())


@bot.form("poll:{number}")
def form_numbered(msg):
    msg.answer(f"Форма номер {msg.params['number']}: {msg.form.get('answer')}", alert=True)


# --- диалоги ---

@section("dialogs", "💬 Диалоги", automatic=False)
def dialogs_section(r: Report, c: int, msg) -> None:
    r.do("меню диалогов", lambda: msg.reply("Два способа спросить", keyboard=Keyboard().row(("ask: прямо в функции", "dlg:ask")).row(("ask_later: шаги", "dlg:steps"))))


@bot.callback("dlg:ask")
def dialog_ask(msg):
    msg.answer()
    name = msg.ask("Как вас зовут?", timeout=120, placeholder="Имя")
    if name is None:
        return msg.reply("Не дождался ответа")
    city = msg.ask(f"{name}, из какого вы города?", timeout=120)
    msg.reply(f"Записал: {name}, {city}", keyboard=back())


@bot.callback("dlg:steps")
def dialog_steps(msg):
    msg.ask_later("Шаг 1. Как вас зовут?", "name", placeholder="Имя", source="кнопка")


@bot.message(state="name")
def dialog_name(msg):
    msg.ask_later(f"Шаг 2. {msg.text}, сколько вам лет? Только цифры.", "age", name=msg.text)


@bot.message(state="age", regex=r"^\d+$")
def dialog_age(msg):
    data = msg.get_data()
    msg.clear_state()
    msg.reply(f"Готово: {data} и возраст {msg.text}. Шаг сброшен: clear_state.", keyboard=back())


@bot.message(state="age")
def dialog_age_wrong(msg):
    msg.set_state("age", tries=msg.get_data().get("tries", 0) + 1)
    msg.reply("Нужны цифры. Попробуйте ещё раз.")


# --- страницы, привязка, панель ---

@section("pages", "📑 Страницы и панель")
def pages_section(r: Report, c: int, msg) -> None:
    parent = r.do("page: первая страница", lambda: bot.send_message(c, "Страница 1", page="p1", keyboard=Keyboard().row(("На вторую", "page:p2"))))
    r.do("bind_to: к странице", lambda: bot.send_message(c, "Живу только на странице 1", bind_to=bind(parent, "p1")))
    r.do("bind_to: к сообщению", lambda: bot.send_message(c, "Удалюсь вместе с родителем", bind_to=parent))
    r.do("bind_to со временем", lambda: bot.send_message(c, "Привязано и исчезнет через минуту", bind_to=bind(parent), self_destruct=60))
    r.do("edit с page", lambda: bot.edit_keyboard(c, parent.message_id, Keyboard().row(("На вторую", "page:p2")), page="p1"))
    panel = r.do("панель: карточка", lambda: bot.send_message(c, card=Card("Панель").header("Панель чата", "Закреплена под шапкой").progress(0.3, "Прогресс")))
    r.do("pin_message: as_panel", lambda: bot.pin_message(c, panel.message_id, as_panel=True))
    r.do("unpin_message", lambda: bot.unpin_message(c, panel.message_id))
    r.do("pin_message: обычное", lambda: bot.pin_message(c, panel.message_id))
    r.do("unpin_message снова", lambda: bot.unpin_message(c, panel.message_id))


@bot.callback("page:{name}")
def change_page(msg):
    other = "p1" if msg.params["name"] == "p2" else "p2"
    msg.edit(f"Страница {msg.params['name'][1]}", page=msg.params["name"], keyboard=Keyboard().row((f"На {other}", f"page:{other}")))
    msg.answer()


# --- inline ---

@section("inline", "🔎 Inline")
def inline_section(r: Report, c: int, msg) -> None:
    r.do("кнопки inline", lambda: bot.send_message(c, f"Наберите @{bot.nik} в любом чате или нажмите кнопку.", keyboard=Keyboard().inline("Открыть здесь", "демо", here=True).inline("Выбрать чат", "демо")))
    for name, media_type in (("photo1.png", None), ("anim.gif", None), ("video.mp4", None), ("music.mp3", None), ("voice.ogg", "voice"), ("note.txt", None)):
        r.do(f"заранее загрузить {name}", lambda: fid(name, media_type))


@bot.inline()
def inline_answer(msg):
    query = msg.inline_query
    page = int(query.offset or 0)
    results = [
        inline.article("a1", f"Статья: {query.query or 'пусто'}", "Текст из inline", description="Простой текст", url="https://wekon.ru", thumbnail=PICTURE.format(1)),
        inline.article("a2", "Карточка", card=Card("Из inline").header("Из inline").progress(0.5), keyboard=Keyboard().row(("Изменить", "inl:edit"))),
        inline.photo("p1", PICTURE.format(1), title="Фото по ссылке", caption="Фото", thumbnail=PICTURE.format(1)),
        inline.photo("p2", file_id=fid("photo1.png"), title="Фото файлом", keyboard=back()),
        inline.gif("g1", file_id=fid("anim.gif"), title="GIF", caption="GIF"),
        inline.mpeg4_gif("m1", file_id=fid("video.mp4"), title="MPEG4"),
        inline.video("v1", file_id=fid("video.mp4"), title="Видео", description="Описание"),
        inline.audio("u1", file_id=fid("music.mp3"), title="Аудио"),
        inline.voice("o1", file_id=fid("voice.ogg", "voice"), title="Голосовое"),
        inline.document("d1", "Документ", file_id=fid("note.txt"), description="Текстовый файл"),
    ]
    if STICKER:
        results.append(inline.sticker("s1", int(STICKER)))
    # Постраничная выдача: следующая часть придёт с offset=2.
    extra = [inline.article(f"more{index}", f"Страница {page + 1}, №{index}", "Ещё результат") for index in range(3)]
    msg.answer_inline(results if page == 0 else extra, cache_time=0, personal=True, next_offset=str(page + 1) if page < 2 else "",
                      button={"text": "Открыть бота", "start_parameter": "inline"})


@bot.callback("inl:edit")
def inline_edit(msg):
    msg.edit("Изменено через inline_message_id", card=Card("Изменено").header("Изменено из inline").progress(1))
    msg.answer("Изменено")


@bot.on("chosen_inline_result")
def inline_chosen(msg):
    log.info("Выбран результат: %s", msg.payload)


# --- опросы ---

@section("polls", "📊 Опросы")
def polls_section(r: Report, c: int, msg) -> None:
    open_poll = r.do("send_poll: открытый", lambda: bot.send_poll(c, "Открытый опрос: голос придёт боту", ["Да", "Нет"], anonymous=False))
    r.do("send_poll: несколько ответов", lambda: bot.send_poll(c, "Что выбрать?", ["Чай", "Кофе", "Вода"], multiple=True))
    closed = r.do("send_poll: для закрытия", lambda: bot.send_poll(c, "Закроется сразу", ["А", "Б"]))
    r.do("stop_poll", lambda: bot.stop_poll(c, closed.message_id))
    log.info("Опрос: %s", open_poll)


@bot.on("poll_answer", "poll")
def poll_changed(msg):
    log.info("Опрос: %s", msg.payload)


# --- счета ---

class DemoKassa(payments.Provider):
    """Своя касса: класс с двумя методами. Здесь она ненастоящая и деньги не берёт."""

    async def create(self, order_id, amount, title, *, expires_at=None):
        return payments.Payment(f"pay-{order_id}", PAY_URL.format(order_id), order_id)

    async def parse_notification(self, body, headers):
        data = json.loads(body)
        return payments.PaymentEvent(data["order"], data["id"], data["status"])


kassa = DemoKassa()


def invoice_keyboard() -> Keyboard:
    keyboard = Keyboard()
    statuses = ("pending", "authorized", "paid", "failed", "canceled", "expired", "refunded")
    for index in range(0, len(statuses), 3):
        keyboard.row(*[(status, f"inv:{status}") for status in statuses[index:index + 3]])
    return keyboard


@section("payments", "💳 Счета")
def payments_section(r: Report, c: int, msg) -> None:
    r.do("send_invoice: простой", lambda: bot.send_invoice(c, "Подписка на месяц", amount=29900, pay_url=PAY_URL.format("simple")))
    full = r.do("send_invoice: все поля", lambda: bot.send_invoice(
        c, "Заказ №42", amount=149900, pay_url=PAY_URL.format("full"), currency="RUB", description="Чай и кофе", subtitle="Магазин", icon="🧾",
        image=PICTURE.format(4), items=[{"label": "Чай", "amount": 49900}, {"label": "Кофе", "amount": 100000}], expires_at=timedelta(hours=1),
        pay_text="Оплатить заказ", card=Card().callout("Доставка завтра", "neutral"), keyboard=invoice_keyboard()))
    r.do("set_invoice_status: со всеми полями", lambda: bot.set_invoice_status(
        c, full.message_id, "pending", status_text="Ждём оплату", tone="warning", note="Ссылка действует час", receipt_url="https://wekon.ru/receipt/1",
        pay_url=PAY_URL.format("full"), expires_at=timedelta(hours=2), keyboard=invoice_keyboard()))
    order = f"demo{int(time.time())}"
    r.do("send_payment: своя касса", lambda: bot.send_payment(c, kassa, order, 50000, "Оплата через свою кассу"))
    r.do("apply_payment: статус по уведомлению", lambda: bot.apply_payment(payments.PaymentEvent(order, f"pay-{order}", "paid")))
    for name, provider in (("YooKassa", payments.YooKassa("shop", "secret")), ("TBank", payments.TBank("terminal", "secret")),
                           ("Robokassa", payments.Robokassa("login", "one", "two", test=True)), ("CloudPayments", payments.CloudPayments("public", "secret"))):
        r.do(f"готовая касса: {name}", lambda: _true(isinstance(provider, payments.Provider)))


@bot.callback("inv:{status}")
def invoice_status(msg):
    msg.bot.set_invoice_status(msg.chat_id, msg.message_id, msg.params["status"], keyboard=invoice_keyboard())
    msg.answer(msg.params["status"])


@bot.on("payment")
def payment_done(msg):
    msg.bot.send_message(msg.chat_id, f"Событие payment: заказ {msg.payment.order_id}, статус {msg.payment.status}")


# --- группы ---

@section("groups", "👥 Чаты и группы")
def groups_section(r: Report, c: int, msg) -> None:
    info = r.do("get_chat", lambda: bot.get_chat(c)) or {}
    log.info("Чат: %s", info)
    user = msg.user.id
    r.do("get_chat_member", lambda: bot.get_chat_member(c, user))
    r.do("get_chat_member_count", lambda: bot.get_chat_member_count(c))
    r.do("get_user_profile_photos", lambda: bot.get_user_profile_photos(user, c))
    r.do("ссылка на бота: start_link", lambda: msg.reply(bot.start_link("promo")))
    r.do("ссылка в группу: group_link", lambda: msg.reply(bot.group_link("showcase")))
    if (info.get("chat_type") or info.get("type")) == "private":
        r.do("остальное работает в группе", lambda: msg.reply("Добавьте бота в группу админом и откройте там этот раздел."))
        return
    r.do("get_chat_administrators", lambda: bot.get_chat_administrators(c))
    picker = ReplyKeyboard("Кого выбрать", one_time=True, selective=True).users("Выбрать участника", request_id=20)
    r.do("выбор участника", lambda: bot.send_message(c, "Выберите участника: появятся кнопки запрета", keyboard=picker))
    topic = r.do("create_topic", lambda: bot.create_topic(c, "Тема от бота"))
    thread = (topic or {}).get("message_thread_id")
    r.do("отправка в тему: thread", lambda: bot.send_message(c, "В теме", thread=thread))
    r.do("edit_topic", lambda: bot.edit_topic(c, thread, "Новое имя темы"))
    r.do("delete_topic", lambda: bot.delete_topic(c, thread))


def member_keyboard(user_id: int) -> Keyboard:
    return (Keyboard()
            .row(("Запрет на минуту", f"grp:mute:{user_id}"), ("Снять запрет", f"grp:free:{user_id}"))
            .row(("Выгнать", f"grp:ban:{user_id}"), ("Вернуть", f"grp:unban:{user_id}")))


@bot.callback("grp:{action}:{user}")
def group_action(msg):
    user_id, action = int(msg.params["user"]), msg.params["action"]
    if action == "mute":
        msg.bot.restrict_member(msg.chat_id, user_id, until=timedelta(minutes=1))
    elif action == "free":
        msg.bot.restrict_member(msg.chat_id, user_id, can_send_messages=True)
    elif action == "ban":
        msg.bot.ban_member(msg.chat_id, user_id)
    else:
        msg.bot.unban_member(msg.chat_id, user_id)
    msg.answer(f"Готово: {action}")


# --- профиль бота ---

@section("profile", "⚙️ Профиль бота")
def profile_section(r: Report, c: int, msg) -> None:
    r.do("get_me", lambda: bot.get_me())
    name = r.do("get_name", lambda: bot.get_name())
    about = r.do("get_description", lambda: bot.get_description())
    short = r.do("get_short_description", lambda: bot.get_short_description())
    r.do("set_name и обратно", lambda: (bot.set_name("Витрина библиотеки"), bot.set_name(name or "Витрина")))
    r.do("set_description и обратно", lambda: (bot.set_description("Описание из библиотеки"), bot.set_description(about or "")))
    r.do("set_short_description и обратно", lambda: (bot.set_short_description("Коротко"), bot.set_short_description(short or "")))
    r.do("set_commands: список с параметрами", lambda: bot.set_commands([{"command": "buy", "description": "Купить", "params": "<товар>"}], language="en"))
    r.do("get_commands", lambda: bot.get_commands(language="en"))
    r.do("delete_commands", lambda: bot.delete_commands(language="en"))
    r.do("set_commands: для групп", lambda: bot.set_commands({"menu": "Меню"}, scope={"type": "all_group_chats"}))
    r.do("get_commands: для групп", lambda: bot.get_commands(scope={"type": "all_group_chats"}))
    r.do("set_menu_button", lambda: bot.set_menu_button("commands", c))
    r.do("get_menu_button", lambda: bot.get_menu_button(c))
    r.do("get_webhook_info", lambda: bot.get_webhook_info())
    r.do("кнопки: аватар и вебхук", lambda: msg.reply("Эти действия меняют бота, поэтому по кнопке", keyboard=Keyboard()
                                                      .row(("Поставить аватар", "prof:photo"), ("Снять аватар", "prof:nophoto"))
                                                      .row(("Вебхук: включить и выключить", "prof:hook"))))


@bot.callback("prof:photo")
def profile_photo(msg):
    msg.bot.set_profile_photo(ASSETS / "photo1.png")
    msg.answer("Аватар поставлен")


@bot.callback("prof:nophoto")
def profile_no_photo(msg):
    msg.bot.remove_profile_photo()
    msg.answer("Аватар снят")


@bot.callback("prof:hook")
def profile_hook(msg):
    """Пока вебхук включён, опрос не работает: выключаем сразу."""
    msg.bot.set_webhook("https://prod.wekon.ru/api/v1/bot/showcase-hook", secret="showcase", allowed_updates=["message"], drop_pending=False)
    info = msg.bot.get_webhook_info()
    msg.bot.delete_webhook()
    msg.answer(f"Вебхук был: {info.get('url')}", alert=True)


# --- типы и низкий уровень ---

#: Соединение для отдельного клиента: тесты подставляют своё.
client_http = None


async def separate_client() -> dict:
    """``Client`` работает и без бота: скрипт, cron, сервер сайта."""
    client = Client(bot.nik, bot.api_key, base_url=bot.base_url, http=client_http() if client_http else None)
    try:
        return await client.get_me()
    finally:
        await client.close()


def poll_once() -> None:
    """Пока работает ``bot.run()``, второй опрос сервер отклоняет: это защита от двух копий бота."""
    try:
        bot.get_updates(0, limit=1, timeout=0)
    except Conflict as error:
        log.info("Опрос занят: %s", error)


@section("types", "🧬 Типы и низкий уровень")
def types_section(r: Report, c: int, msg) -> None:
    sent = r.do("Sent: результат отправки", lambda: bot.send_message(c, "Результат отправки, кнопки — низкоуровневые вызовы", keyboard=Keyboard()
                                                                     .row(("answer_callback", "raw:answer"), ("msg.delete", "raw:delete"))))
    r.do("Sent: поля", lambda: _true(isinstance(sent, Sent) and sent.message_id > 0))
    update = Update.parse({"update_id": 1, "type": "photo", "message": {"message_id": 5, "chat": {"chat_id": 1, "chat_type": "private"}, "from": {"user_id": 2, "nik_name": "анна"},
                                                                        "text": "Подпись", "attachments": [{"id": 9, "media_type": "image"}]}})
    r.do("Update, Message, Chat, User, Attachment", lambda: _true(
        isinstance(update, Update) and isinstance(update.message, Message) and isinstance(update.message.chat, Chat) and update.message.chat.is_private
        and isinstance(update.user, User) and isinstance(update.message.file, Attachment) and update.message.file.ref == "attachment:9"))
    press = Update.parse({"type": "callback_query", "callback_query": {"id": "1", "data": "x", "chat": {"chat_id": 1}, "form_data": {"a": 1}}})
    r.do("CallbackQuery", lambda: _true(isinstance(press.callback, CallbackQuery) and press.callback.form == {"a": 1}))
    query = Update.parse({"type": "inline_query", "inline_query": {"id": "1", "query": "q", "offset": ""}})
    r.do("InlineQuery", lambda: _true(isinstance(query.inline_query, InlineQuery) and query.inline_query.query == "q"))
    r.do("Client: отдельно и close", lambda: asyncio.run(separate_client()))
    r.do("get_updates: занято опросом", poll_once)


@bot.callback("raw:answer")
def raw_answer(msg):
    msg.answered = True
    msg.bot.answer_callback(msg.callback.id, "Ответ напрямую: answer_callback")


@bot.callback("raw:delete")
def raw_delete(msg):
    msg.delete()


# --- токен и ошибки ---

@section("errors", "🛡 Вход и ошибки")
def errors_section(r: Report, c: int, msg) -> None:
    r.expect("NotFound: нет файла", NotFound, lambda: bot.download(1))
    r.expect("WekonError: неизвестный метод", WekonError, lambda: bot.call("noSuchMethod"))
    r.expect("BadRequest: неверная карточка", BadRequest, lambda: bot.send_message(c, "x", card=[{"type": "нет такого"}]))
    r.do("классы ошибок", lambda: _true(all(issubclass(kind, WekonError) for kind in (AuthError, BadRequest, Conflict, Forbidden, NetworkError, NotFound, NotSupported, RateLimited, ServerError))))
    r.do("logout: следующий вызов войдёт заново", lambda: bot.logout())
    r.do("вход после logout", lambda: bot.get_me())


@bot.command("error")
def error_command(msg):
    raise ValueError("Проверка bot.error")


@bot.error
def on_error(msg):
    log.error("Обработчик упал: %r", msg.exception)
    msg.bot.send_message(msg.chat_id, f"⚠️ {type(msg.exception).__name__}: {msg.exception}"[:500])


# --- игра ---

GAME_COLORS = {2: "#EEE4DA", 4: "#EDE0C8", 8: "#F2B179", 16: "#F59563", 32: "#F67C5F", 64: "#F65E3B", 128: "#EDCF72", 256: "#EDCC61"}
GAME_LINES = {"l": [[r * 4 + c for c in range(4)] for r in range(4)], "r": [[r * 4 + 3 - c for c in range(4)] for r in range(4)],
              "u": [[c * 4 + r for c in range(4)] for r in range(4)], "d": [[(3 - c) * 4 + r for c in range(4)] for r in range(4)]}


def game_new() -> dict:
    game = {"board": [0] * 16, "score": 0, "turn": 0}
    game_spawn(game)
    game_spawn(game)
    return game


def game_spawn(game: dict) -> None:
    empty = [index for index, value in enumerate(game["board"]) if not value]
    if empty:
        game["turn"] += 1
        game["board"][empty[(game["turn"] * 7) % len(empty)]] = 2


def game_move(game: dict, direction: str) -> bool:
    moved = False
    for line in GAME_LINES[direction]:
        values, merged = [game["board"][index] for index in line if game["board"][index]], []
        while values:
            value = values.pop(0)
            if values and values[0] == value:
                value += values.pop(0)
                game["score"] += value
            merged.append(value)
        merged += [0] * (4 - len(merged))
        for index, value in zip(line, merged):
            moved = moved or game["board"][index] != value
            game["board"][index] = value
    return moved


def game_screen(game: dict) -> tuple:
    def arrow(text: str, direction: str, **size) -> dict:
        return button(text, f"game:{direction}", loading="none", haptic="light", hint="Сдвинуть плитки", **size)

    cells = [cell(str(value), color=GAME_COLORS.get(value, "#3C3A32")) if value else cell() for value in game["board"]]
    card = Card(f"2048: {game['score']}").header("2048", icon="🎮", status="Ваш ход", tone="accent").stats(("Счёт", game["score"], "accent")).grid(4, cells)
    keyboard = (Keyboard(columns=4)
                .row(arrow("⬅️", "l", height=2), arrow("⬆️", "u", width=2), arrow("➡️", "r", height=2))
                .row(arrow("⬇️", "d", width=2))
                .row(button("🔄 Заново", "game:new", width=4, long_data="game:now", confirm="Начать заново?")))
    return card, keyboard


@section("game", "🎮 Игра")
def game_section(r: Report, c: int, msg) -> None:
    games[c] = game_new()
    card, keyboard = game_screen(games[c])
    r.do("поле-сетка, крестовина, удержание", lambda: bot.send_message(c, card=card, keyboard=keyboard))


@bot.callback("game:{direction}")
def game_play(msg):
    direction = msg.params["direction"]
    game = games.setdefault(msg.chat_id, game_new())
    if direction in ("new", "now"):
        game = games[msg.chat_id] = game_new()
    elif game_move(game, direction):
        game_spawn(game)
    else:
        return msg.answer("Ход ничего не меняет")
    card, keyboard = game_screen(game)
    msg.edit(card=card, keyboard=keyboard)


# --- асинхронный код рядом с обычным ---

@bot.command("async")
async def async_command(ctx):
    """``async def`` работает рядом с обычными функциями."""
    await ctx.typing()
    await ctx.reply(f"Асинхронный обработчик, аргументы: {ctx.args}")


# --- сообщения: фильтры и общий обработчик ---

@bot.message("привет")
def hello(msg):
    msg.reply(f"Привет, {msg.user.nik}! Точное совпадение текста.")
    msg.react(REACTION)


@bot.message(regex=r"^(?P<left>\d+)\s*\+\s*(?P<right>\d+)$")
def add_numbers(msg):
    msg.reply(f"Шаблон regex: {int(msg.params['left']) + int(msg.params['right'])}")


@bot.message()
def any_message(msg):
    message = msg.message
    for field in ("users_shared", "chat_shared", "contact", "date_shared", "time_shared", "color_shared"):
        value = getattr(message, field)
        if value:
            msg.reply(f"{field}: {json.dumps(value, ensure_ascii=False)[:500]}")
            if field == "users_shared" and not message.chat.is_private:
                for entry in (value.get("users") or value.get("user_ids") or [])[:3]:
                    target = entry.get("user_id") if isinstance(entry, dict) else entry
                    msg.reply(f"Участник {target}", keyboard=member_keyboard(int(target)))
            return
    if message.chat.is_private:
        kind = message.raw.get("message_kind") or message.kind
        msg.reply(f"Получено ({kind}): {message.text[:200]}", buttons={"🏠 Меню": "menu"})


@bot.edited()
def edited(msg):
    msg.reply(f"Вижу правку: {msg.message.text[:200]}")


@bot.on("reaction")
def reacted(msg):
    log.info("Реакция: %s", msg.payload)


@bot.on(*EVENT_TYPES)
def write_journal(msg):
    journal[msg.update.type] += 1
    last_events.append(f"{datetime.now().strftime('%H:%M:%S')} {msg.update.type}")


@bot.middleware
def timing(msg, call_next):
    """Код вокруг всех обработчиков."""
    started = time.monotonic()
    try:
        return call_next()
    finally:
        log.info("%s: %.0f мс", msg.update.type, (time.monotonic() - started) * 1000)


@bot.command("clean")
def clean_screen(msg):
    """В личном чате бот убирает и сообщения человека: экран остаётся чистым."""
    try:
        msg.delete()
    except WekonError as error:
        msg.reply(f"Удалить сообщение нельзя: {error}")
        return
    msg.reply("Ваша команда /clean удалена: бот сам держит экран чистым.", keyboard=back())


COMMANDS = {"start": "Меню", "selftest": "Проверить всё", "coverage": "Покрытие библиотеки", "journal": "Журнал событий",
            "clean": "Удалить моё сообщение", "async": "Асинхронный обработчик", "error": "Проверка обработки ошибок"}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bot.set_commands(COMMANDS)
    bot.run(allowed_updates=ALLOWED_UPDATES, delete_webhook=True)


if __name__ == "__main__":
    main()
