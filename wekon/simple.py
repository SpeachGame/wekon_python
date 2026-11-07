"""Простой бот: обычные функции без async и await, как в telebot.

Возможности те же, что у асинхронного ``wekon.aio.Bot``: это обёртка над ним. Сетью занят
отдельный поток, а обработчики идут в своих потоках, поэтому долгая функция не мешает другим
чатам. Если обработчик объявлен через ``async def``, он работает как есть.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import inspect
import threading
from typing import Any, Callable, Optional

from .bot import Bot as AsyncBot
from .bot import Context


class _Loop:
    """Цикл событий в фоновом потоке: туда уходят все обращения к сети."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="wekon", daemon=True).start()

    def run(self, coroutine: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result()


def _blocking(value: Any, loop: _Loop) -> Any:
    """Асинхронный метод становится обычным; остальное остаётся как есть."""
    if not inspect.iscoroutinefunction(value):
        return value

    @functools.wraps(value)
    def call(*args: Any, **kwargs: Any) -> Any:
        return loop.run(value(*args, **kwargs))
    return call


class SimpleContext:
    """То же, что ``Context``, но методы вызываются без ``await``."""

    def __init__(self, context: Context, loop: _Loop, bot: "Bot") -> None:
        self._context = context
        self._loop = loop
        self.bot = bot

    def __getattr__(self, name: str) -> Any:
        return _blocking(getattr(self._context, name), self._loop)


class Bot:
    """Бот Wekon без async.

    >>> bot = Bot("echo_bot")          # ключ из WEKON_BOT_API_KEY
    >>> @bot.message()
    ... def echo(msg):
    ...     msg.reply(msg.text)
    >>> bot.run()

    Все методы API вызываются напрямую: ``bot.send_message(chat_id, "Привет")``.
    """

    def __init__(self, nik: str, api_key: Optional[str] = None, **options: Any) -> None:
        self._core = AsyncBot(nik, api_key, **options)
        self._loop = _Loop()

    def __getattr__(self, name: str) -> Any:
        return _blocking(getattr(self._core, name), self._loop)

    # --- обработчики ---

    def _adapt(self, handler: Callable) -> Callable:
        """Обычная функция идёт в своём потоке и получает контекст без await."""
        if inspect.iscoroutinefunction(handler):
            return handler

        async def run(ctx: Context) -> Any:
            return await asyncio.to_thread(handler, SimpleContext(ctx, self._loop, self))
        return run

    def _decorator(self, register: Callable) -> Callable:
        def decorate(handler: Callable) -> Callable:
            register(self._adapt(handler))
            return handler
        return decorate

    def command(self, *names: str) -> Callable:
        """Команда: ``@bot.command("start", "help")``."""
        return self._decorator(self._core.command(*names))

    def message(self, text: Optional[str] = None, **filters: Any) -> Callable:
        """Сообщение: без условий — любое; ``text``, ``regex``, ``state``, ``types``."""
        return self._decorator(self._core.message(text, **filters))

    def callback(self, *patterns: str) -> Callable:
        """Нажатие: ``@bot.callback("buy:{item}")``."""
        return self._decorator(self._core.callback(*patterns))

    def form(self, *patterns: str) -> Callable:
        """Отправленная форма."""
        return self._decorator(self._core.form(*patterns))

    def on(self, *types: str) -> Callable:
        """Любой тип события: ``@bot.on("reaction")``, ``@bot.on("payment")``."""
        return self._decorator(self._core.on(*types))

    def inline(self) -> Callable:
        return self.on("inline_query")

    def edited(self) -> Callable:
        return self.on("edited_message")

    def error(self, handler: Callable) -> Callable:
        """Ошибки обработчиков: исключение в ``ctx.exception``."""
        self._core.error(self._adapt(handler))
        return handler

    def middleware(self, handler: Callable) -> Callable:
        """Код вокруг обработчиков: ``def f(ctx, next)``."""
        if inspect.iscoroutinefunction(handler):
            return self._core.middleware(handler)

        async def run(ctx: Context, call_next: Callable) -> Any:
            def resume() -> Any:
                return self._loop.run(call_next())
            return await asyncio.to_thread(handler, SimpleContext(ctx, self._loop, self), resume)
        self._core.middleware(run)
        return handler

    # --- запуск ---

    def run(self, **options: Any) -> None:
        """Работать до Ctrl+C. Опции — как у ``start_polling``."""
        task = asyncio.run_coroutine_threadsafe(self._core.start_polling(**options), self._loop.loop)
        try:
            while True:
                try:
                    task.result(timeout=0.5)  # с паузами: иначе Ctrl+C не сработает в Windows
                    return
                except concurrent.futures.TimeoutError:
                    continue
        except (KeyboardInterrupt, concurrent.futures.CancelledError):
            task.cancel()
        finally:
            self._loop.run(self._core.close())

    def asgi(self, **options: Any) -> Callable:
        """ASGI-приложение для вебхука и уведомлений касс: ``uvicorn module:app``."""
        inner = self._core.asgi(**options)

        async def app(scope: dict, receive: Callable, send: Callable) -> None:
            if scope["type"] != "http":
                return
            body = b""
            while True:
                chunk = await receive()
                body += chunk.get("body", b"")
                if not chunk.get("more_body"):
                    break
            sent: list = []

            async def replay() -> dict:
                return {"body": body}

            async def collect(event: dict) -> None:
                sent.append(event)

            # Запрос обрабатывается в цикле бота: там живут соединения с сервером.
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(inner(scope, replay, collect), self._loop.loop))
            for event in sent:
                await send(event)
        return app
