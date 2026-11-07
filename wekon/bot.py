"""Бот: обработчики событий, контекст, получение событий через опрос и вебхук."""
from __future__ import annotations

import asyncio
import functools
import hmac
import json
import logging
import re
import signal
from collections import OrderedDict, deque
from typing import Any, Awaitable, Callable, Iterable, Optional

from .client import Client
from .errors import Conflict, WekonError
from .keyboards import ForceReply, RemoveKeyboard, keys_of
from .types import CallbackQuery, Chat, Message, Sent, Update, User

log = logging.getLogger("wekon")

Handler = Callable[["Context"], Awaitable[Any]]

#: Сколько последних событий помнить: доставка «как минимум один раз».
SEEN_UPDATES = 4096
WEBHOOK_SECRET_HEADER = "x-wekon-bot-api-secret-token"


def compile_pattern(pattern: str) -> "re.Pattern[str]":
    """``"buy:{item}"`` → регулярное выражение: ``{имя}`` попадёт в ``ctx.params``."""
    parts = re.split(r"\{(\w+)\}", pattern)
    source = "".join(re.escape(part) if index % 2 == 0 else f"(?P<{part}>[^:]+)" for index, part in enumerate(parts))
    return re.compile(f"^{source}$")


class MemoryStorage:
    """Состояние диалогов в памяти: теряется при перезапуске.

    Для работы в проде замените своим классом с теми же методами (Redis, база).
    """

    def __init__(self) -> None:
        self.states: dict = {}

    async def get(self, key: tuple) -> tuple:
        return self.states.get(key, (None, {}))

    async def set(self, key: tuple, state: Optional[str], data: dict) -> None:
        if state is None:
            self.states.pop(key, None)
        else:
            self.states[key] = (state, data)


class Context:
    """Всё о текущем событии и короткие действия с ним."""

    def __init__(self, bot: "Bot", update: Update) -> None:
        self.bot = bot
        self.update = update
        self.message: Optional[Message] = update.message
        self.callback: Optional[CallbackQuery] = update.callback
        self.inline_query = update.inline_query
        self.user: User = (self.message or self.callback or self.inline_query or update).user
        self.chat: Optional[Chat] = self.message.chat if self.message else self.callback.chat if self.callback else None
        #: Части команды после неё: ``/buy 5 red`` → ``["5", "red"]``.
        self.args: list = []
        #: Значения из шаблона: ``buy:{item}`` → ``{"item": "42"}``.
        self.params: dict = {}
        self.answered = False
        self._state_key = (self.chat_id, self.user.id)

    @property
    def chat_id(self) -> int:
        return self.chat.id if self.chat else self.update.chat_id

    @property
    def text(self) -> str:
        return self.message.text if self.message else ""

    @property
    def data(self) -> str:
        """Данные нажатой кнопки."""
        return self.callback.data if self.callback else ""

    @property
    def value(self) -> Optional[str]:
        """Новое значение элемента карточки."""
        return self.callback.value if self.callback else None

    @property
    def form(self) -> dict:
        """Ответы формы."""
        return (self.callback.form if self.callback else None) or {}

    @property
    def picked(self) -> tuple:
        """Выбор нескольких пунктов: ``"r3s5:1"`` → ``("r3s5", True)``."""
        name, _, state = (self.value or "").partition(":")
        return name, state == "1"

    @property
    def payload(self) -> dict:
        """Содержимое события как на сервере: для реакций, опросов, участников."""
        return self.update.payload

    def __getattr__(self, name: str) -> Any:
        # Любая отправка в этот чат: ctx.send_video(...), ctx.send_poll(...).
        if name.startswith("send_") and hasattr(type(self.bot), name):
            return functools.partial(getattr(self.bot, name), self.chat_id)
        raise AttributeError(name)

    @property
    def message_id(self) -> int:
        if self.message:
            return self.message.id
        return self.callback.message_id if self.callback else 0

    # --- ответы ---

    @staticmethod
    def _keyboard(options: dict) -> dict:
        """Простые записи: ``buttons=`` под сообщением, ``keys=`` вместо клавиатуры."""
        if "buttons" in options:
            options["keyboard"] = options.pop("buttons")
        if "keys" in options:
            keys = options.pop("keys")
            options["keyboard"] = keys_of(keys) if keys else RemoveKeyboard()
        return options

    async def reply(self, text: Optional[str] = None, **options: Any) -> Sent:
        """Написать в этот чат: текст, ``card=``, ``buttons=``, ``keys=``, ``html=True`` и общие настройки.

        ``buttons=[("Да", "yes"), ("Сайт", "https://…")]``, ``keys=["Да", "Нет"]``; ``keys=[]`` убирает клавиатуру.
        """
        return await self.bot.send_message(self.chat_id, text, **self._keyboard(options))

    async def edit(self, text: Optional[str] = None, **options: Any) -> dict:
        """Изменить сообщение с нажатой кнопкой (или ``message_id=``)."""
        options = self._keyboard(options)
        message_id = options.pop("message_id", self.message_id)
        inline_id = self.callback.inline_message_id if self.callback else self.update.payload.get("inline_message_id", "")
        return await self.bot.edit_message(self.chat_id, message_id, text, inline_message_id=inline_id or None, **options)

    async def answer(self, text: Optional[str] = None, **options: Any) -> Any:
        """Ответить на нажатие: всплывашка, ``alert=True`` — окно, ``near_button=True`` — у кнопки."""
        self.answered = True
        return await self.bot.answer_callback(self.callback.id, text, **options)

    async def delete(self) -> Any:
        return await self.bot.delete_message(self.chat_id, self.message_id)

    async def typing(self) -> Any:
        return await self.bot.send_chat_action(self.chat_id)

    async def react(self, *emoji_ids: str) -> Any:
        return await self.bot.react(self.chat_id, self.message_id, *emoji_ids)

    async def download(self, file: Any = None, path: Any = None) -> Any:
        """Скачать присланный файл: без ``file`` — первое вложение сообщения."""
        target = file if file is not None else (self.message.file if self.message else None)
        if target is None:
            raise ValueError("В сообщении нет файла")
        return await self.bot.download(target, path)

    async def answer_inline(self, results: Iterable[dict], **options: Any) -> Any:
        return await self.bot.answer_inline(self.inline_query.id, results, **options)

    async def ask(self, text: str, *, timeout: float = 300, placeholder: Optional[str] = None) -> Optional[str]:
        """Задать вопрос и дождаться ответа: ``name = msg.ask("Как вас зовут?")``.

        ``None`` — истекло время или человек ввёл команду. Ожидание живёт в памяти
        программы; для длинных анкет надёжнее ``ask_later`` с шагами диалога.
        """
        waiter = asyncio.get_running_loop().create_future()
        self.bot._waiters[self._state_key] = waiter
        try:
            await self.reply(text, keyboard=ForceReply(placeholder))
            return await asyncio.wait_for(waiter, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self.bot._waiters.pop(self._state_key, None)

    async def ask_later(self, text: str, state: str, *, placeholder: Optional[str] = None, **data: Any) -> Sent:
        """Задать вопрос: ответ попадёт в ``@bot.message(state=...)``, переживает обработчик."""
        await self.set_state(state, **data)
        return await self.reply(text, keyboard=ForceReply(placeholder))

    # --- диалог ---

    async def set_state(self, state: str, **data: Any) -> None:
        """Шаг диалога с этим человеком; ``data`` дополняет сохранённое."""
        _, saved = await self.bot.storage.get(self._state_key)
        await self.bot.storage.set(self._state_key, state, {**saved, **data})

    async def get_data(self) -> dict:
        """Данные текущего диалога."""
        return (await self.bot.storage.get(self._state_key))[1]

    async def clear_state(self) -> None:
        await self.bot.storage.set(self._state_key, None, {})


class Bot(Client):
    """Бот Wekon.

    >>> bot = Bot("shop_bot")          # ключ из WEKON_BOT_API_KEY
    >>> @bot.command("start")
    ... async def start(ctx):
    ...     await ctx.reply("Привет!")
    >>> bot.run()

    Простая версия без async — ``wekon.Bot`` (см. simple.py).
    """

    def __init__(self, nik: str, api_key: Optional[str] = None, *, storage: Any = None, **client: Any) -> None:
        super().__init__(nik, api_key, **client)
        self.storage = storage or MemoryStorage()
        self._commands: dict = {}
        self._messages: list = []
        self._callbacks: list = []
        self._forms: list = []
        self._events: dict = {}
        self._error: Optional[Handler] = None
        self._middleware: list = []
        self._seen: OrderedDict = OrderedDict()
        self._queues: dict = {}
        self._workers: set = set()
        self._waiters: dict = {}

    # --- регистрация ---

    def command(self, *names: str) -> Callable[[Handler], Handler]:
        """Команда: ``@bot.command("start", "help")``. Части после неё — в ``ctx.args``."""
        def register(handler: Handler) -> Handler:
            for name in names:
                self._commands[name.lstrip("/").lower()] = handler
            return handler
        return register

    def message(self, text: Optional[str] = None, *, regex: Optional[str] = None, state: Optional[str] = None,
                types: Optional[Iterable[str]] = None) -> Callable[[Handler], Handler]:
        """Сообщение: без условий — любое. ``text`` — точный текст, ``regex`` — шаблон,
        ``state`` — шаг диалога, ``types`` — тип события (``photo``, ``document``…)."""
        compiled = re.compile(regex) if regex else None
        kinds = set(types) if types else None

        def register(handler: Handler) -> Handler:
            self._messages.append((text, compiled, state, kinds, handler))
            return handler
        return register

    def callback(self, *patterns: str) -> Callable[[Handler], Handler]:
        """Нажатие: ``@bot.callback("buy:{item}")``. Без шаблона — все остальные нажатия."""
        def register(handler: Handler) -> Handler:
            for pattern in patterns or (None,):
                self._callbacks.append((compile_pattern(pattern) if pattern else None, handler))
            return handler
        return register

    def form(self, *patterns: str) -> Callable[[Handler], Handler]:
        """Отправленная форма; ответы в ``ctx.form``."""
        def register(handler: Handler) -> Handler:
            for pattern in patterns:
                self._forms.append((compile_pattern(pattern), handler))
            return handler
        return register

    def on(self, *types: str) -> Callable[[Handler], Handler]:
        """Любой тип события: ``@bot.on("reaction")``. Содержимое — в ``ctx.update.payload``."""
        def register(handler: Handler) -> Handler:
            for kind in types:
                self._events.setdefault(kind, []).append(handler)
            return handler
        return register

    def inline(self) -> Callable[[Handler], Handler]:
        """Запрос ``@бот текст``: ``ctx.inline_query``, ответ — ``ctx.answer_inline``."""
        return self.on("inline_query")

    def edited(self) -> Callable[[Handler], Handler]:
        return self.on("edited_message")

    def error(self, handler: Handler) -> Handler:
        """Ошибки обработчиков: исключение лежит в ``ctx.exception``."""
        self._error = handler
        return handler

    def middleware(self, handler: Callable[["Context", Callable], Awaitable[Any]]) -> Callable:
        """Код вокруг всех обработчиков: ``async def f(ctx, call_next)``."""
        self._middleware.append(handler)
        return handler

    # --- обработка ---

    async def _find_message(self, ctx: Context) -> Optional[Handler]:
        message = ctx.message
        text = message.text.strip()
        if text.startswith("/"):
            head, *args = text.split()
            name, _, target = head[1:].partition("@")
            if (not target or target.lower() == self.nik.lower()) and name.lower() in self._commands:
                ctx.args = args
                return self._commands[name.lower()]
        state, _ = await self.storage.get(ctx._state_key)
        # Шаг диалога важнее общих обработчиков.
        ordered = sorted(self._messages, key=lambda entry: entry[2] is None)
        for wanted, compiled, need_state, kinds, handler in ordered:
            if need_state is not None and need_state != state:
                continue
            if kinds is not None and message.kind not in kinds:
                continue
            if wanted is not None and wanted != text:
                continue
            if compiled is not None:
                match = compiled.search(text)
                if not match:
                    continue
                ctx.params = match.groupdict()
            return handler
        return None

    def _find_callback(self, ctx: Context) -> Optional[Handler]:
        entries = self._forms + self._callbacks if ctx.callback.form is not None else self._callbacks
        for pattern, handler in entries:
            if pattern is None:
                return handler
            match = pattern.match(ctx.data)
            if match:
                ctx.params = match.groupdict()
                return handler
        return None

    async def _run_handler(self, ctx: Context, handler: Handler) -> None:
        async def chain(index: int) -> Any:
            if index == len(self._middleware):
                return await handler(ctx)
            return await self._middleware[index](ctx, lambda: chain(index + 1))
        await chain(0)

    async def process_update(self, raw: dict) -> None:
        """Обработать одно событие. Ошибка обработчика не останавливает бота."""
        update = Update.parse(raw)
        if update.id and update.id in self._seen:
            return
        if update.id:
            self._seen[update.id] = True
            while len(self._seen) > SEEN_UPDATES:
                self._seen.popitem(last=False)
        ctx = Context(self, update)
        handlers: list = []
        if update.type in Update.MESSAGE_TYPES:
            found = await self._find_message(ctx)
            handlers = [found] if found else []
        elif update.type == "callback_query":
            found = self._find_callback(ctx)
            handlers = [found] if found else []
        handlers += self._events.get(update.type, [])
        try:
            for handler in handlers:
                await self._run_handler(ctx, handler)
        except Exception as error:  # обработчик разработчика может упасть как угодно
            ctx.exception = error
            if self._error:
                await self._error(ctx)
            else:
                log.exception("Ошибка обработчика события %s", update.type)
        finally:
            # Без ответа у человека крутился бы индикатор на кнопке.
            if ctx.callback and not ctx.answered:
                try:
                    await self.answer_callback(ctx.callback.id)
                except WekonError as error:
                    log.warning("Не удалось ответить на нажатие: %s", error)

    def _enqueue(self, raw: dict) -> None:
        """События одного чата идут по порядку, разные чаты — одновременно."""
        if self._answers_question(raw):
            return
        key = Update.parse(raw).chat_id or object()
        queue = self._queues.get(key)
        if queue is not None:
            queue.append(raw)
            return
        self._queues[key] = deque([raw])
        task = asyncio.ensure_future(self._drain(key))
        self._workers.add(task)
        task.add_done_callback(self._workers.discard)

    def _answers_question(self, raw: dict) -> bool:
        """Сообщение отвечает на ``ctx.ask``: оно не идёт в обработчики. Команда прерывает вопрос."""
        update = Update.parse(raw)
        message = update.message if update.type in Update.MESSAGE_TYPES else None
        waiter = self._waiters.get((message.chat.id, message.user.id)) if message else None
        if waiter is None or waiter.done():
            return False
        if message.text.startswith("/"):
            waiter.set_result(None)
            return False
        waiter.set_result(message.text)
        return True

    async def _drain(self, key: Any) -> None:
        queue = self._queues[key]
        try:
            while queue:
                await self.process_update(queue.popleft())
        finally:
            del self._queues[key]

    async def wait_idle(self) -> None:
        """Дождаться обработки всех принятых событий."""
        while self._workers:
            await asyncio.wait(list(self._workers))
            # Уступаем циклу: завершённые задачи убирает их колбэк, а без этой
            # паузы он бы не запустился, и цикл крутился бы вечно.
            await asyncio.sleep(0)

    # --- запуск ---

    async def start_polling(self, *, allowed_updates: Optional[Iterable[str]] = None, delete_webhook: bool = False) -> None:
        """Получать события опросом, пока задачу не отменят."""
        if delete_webhook:
            await self.delete_webhook()
        me = await self.get_me()
        log.info("Бот @%s запущен", me.get("nik_name", self.nik))
        offset, pause = 0, 1
        try:
            while True:
                try:
                    updates = await self.get_updates(offset, allowed_updates=allowed_updates)
                    pause = 1
                except Conflict as error:
                    if error.code == "webhook_active":
                        raise
                    log.warning("Бот уже запущен в другом месте: ждём")
                    await asyncio.sleep(5)
                    continue
                except WekonError as error:
                    if error.status in (401, 403):
                        raise
                    log.warning("Сервер недоступен, повтор через %s с: %s", pause, error)
                    await asyncio.sleep(pause)
                    pause = min(pause * 2, 30)
                    continue
                for raw in updates:
                    offset = max(offset, int(raw.get("update_id") or 0))
                    self._enqueue(raw)
        finally:
            await self.wait_idle()

    def run(self, **options: Any) -> None:
        """Запустить бота и работать до Ctrl+C. Опции — как у ``start_polling``."""
        async def main() -> None:
            task = asyncio.ensure_future(self.start_polling(**options))
            loop = asyncio.get_running_loop()
            for name in ("SIGINT", "SIGTERM"):
                try:
                    loop.add_signal_handler(getattr(signal, name), task.cancel)
                except (NotImplementedError, AttributeError):
                    pass  # Windows: остановит KeyboardInterrupt
            try:
                await task
            except asyncio.CancelledError:
                pass
            finally:
                await self.close()
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass

    def asgi(self, *, secret: Optional[str] = None, path: str = "/wekon", payments: Optional[dict] = None) -> Callable:
        """ASGI-приложение для вебхука: ``uvicorn module:app``.

        ``secret`` — тот же, что в ``set_webhook``. ``payments`` — ``{"/pay/yookassa": касса}``:
        уведомления касс на этом же сервере, статус счёта поставится сам.
        """
        payments = payments or {}

        async def app(scope: dict, receive: Callable, send: Callable) -> None:
            if scope["type"] != "http":
                return

            async def respond(status: int, body: bytes = b"", kind: bytes = b"text/plain") -> None:
                await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", kind)]})
                await send({"type": "http.response.body", "body": body})

            raw = b""
            while True:
                chunk = await receive()
                raw += chunk.get("body", b"")
                if not chunk.get("more_body"):
                    break
            headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
            route = scope.get("path", "")
            if scope.get("method") != "POST":
                return await respond(405)
            if route in payments:
                try:
                    event = await payments[route].parse_notification(raw, headers)
                except (ValueError, KeyError):
                    return await respond(400)
                if event is not None:
                    await self.apply_payment(event)
                reply = payments[route].notification_reply(event)
                return await respond(200, reply.encode(), b"application/json" if reply.startswith("{") else b"text/plain")
            if route != path:
                return await respond(404)
            if secret and not hmac.compare_digest(headers.get(WEBHOOK_SECRET_HEADER, ""), secret):
                return await respond(401)
            try:
                self._enqueue(json.loads(raw))
            except ValueError:
                return await respond(400)
            await respond(200)
        return app

    # --- оплата ---

    async def send_payment(self, chat_id: int, provider: Any, order_id: str, amount: int, title: str, **invoice: Any) -> Sent:
        """Создать платёж в кассе и прислать карточку счёта. ``amount`` — в копейках."""
        payment = await provider.create(order_id, amount, title, expires_at=invoice.get("expires_at"))
        sent = await self.send_invoice(chat_id, title, amount=amount, pay_url=payment.url, **invoice)
        await self.storage.set(("invoice", order_id), "invoice", {"chat_id": chat_id, "message_id": sent.message_id})
        return sent

    async def apply_payment(self, event: Any) -> None:
        """Поставить статус счёта по уведомлению кассы."""
        _, invoice = await self.storage.get(("invoice", event.order_id))
        if not invoice:
            log.warning("Счёт заказа %s не найден", event.order_id)
            return
        await self.set_invoice_status(invoice["chat_id"], invoice["message_id"], event.status)
        for handler in self._events.get("payment", []):
            ctx = Context(self, Update(0, "payment", {"chat_id": invoice["chat_id"], "event": event}))
            ctx.payment = event
            await self._run_handler(ctx, handler)
