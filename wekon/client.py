"""Клиент Bot API: вход, повторы и все методы сервера."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional, Union

import httpx

from .cards import Card, to_unix
from .errors import AuthError, NetworkError, RateLimited, ServerError, WekonError, error_from_response
from .keyboards import _clean, markup_of
from .types import Sent

log = logging.getLogger("wekon")

DEFAULT_BASE_URL = "https://prod.wekon.ru/api/v1"
WEB_URL = "https://web.wekon.ru"
#: Токен обновляется заранее: он живёт 10 минут.
TOKEN_MARGIN_SECONDS = 60
MAX_RETRIES = 4
#: Начало файла и расширение: сервер определяет вид файла по расширению имени.
FILE_SIGNATURES = ((b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF8", ".gif"), (b"%PDF", ".pdf"), (b"OggS", ".ogg"),
                   (b"ID3", ".mp3"), (b"\xff\xfb", ".mp3"), (b"\xff\xf3", ".mp3"), (b"\x1a\x45\xdf\xa3", ".webm"))

Media = Union[str, Path, bytes]


def _bytes_name(content: bytes) -> str:
    """Имя для байтов без имени: без расширения сервер примет фото за документ."""
    for start, extension in FILE_SIGNATURES:
        if content.startswith(start):
            return "file" + extension
    if content[:4] == b"RIFF":
        return {b"WEBP": "file.webp", b"WAVE": "file.wav"}.get(content[8:12], "file")
    if content[4:8] == b"ftyp":
        return "file.m4a" if content[8:11].lower() == b"m4a" else "file.mov" if content[8:10] == b"qt" else "file.mp4"
    return "file"


def _message_id(message: Any) -> Any:
    """Номер сообщения из ``Message``, ``Sent`` или самого числа."""
    return getattr(message, "message_id", None) or getattr(message, "id", None) or message


def bind(message: Any, page: Optional[str] = None) -> dict:
    """Значение ``bind_to``: сообщение или его id, необязательная страница."""
    return _clean({"message_id": _message_id(message), "page": page})


def check_login(params: dict, api_key: str, max_age: int = 86400) -> bool:
    """Проверить данные кнопки ``login_url`` на своём сайте: подпись и свежесть."""
    fields = {key: value for key, value in params.items() if key != "hash"}
    text = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    expected = hmac.new(hashlib.sha256(api_key.encode()).digest(), text.encode(), hashlib.sha256).hexdigest()
    fresh = time.time() - int(fields.get("auth_date") or 0) < max_age
    return fresh and hmac.compare_digest(expected, str(params.get("hash", "")))


class Client:
    """Все методы Bot API. ``Bot`` наследует их, поэтому обычно достаточно ``bot.send_message(...)``."""

    def __init__(self, nik: str, api_key: Optional[str] = None, *, base_url: str = DEFAULT_BASE_URL,
                 http: Optional[httpx.AsyncClient] = None) -> None:
        self.nik = nik
        self.api_key = api_key or os.environ.get("WEKON_BOT_API_KEY", "")
        if not self.api_key:
            raise AuthError("Нужен ключ бота: передайте api_key или задайте WEKON_BOT_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))
        self._token = ""
        self._token_until = 0.0
        self._auth_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.http.aclose()

    def start_link(self, param: str = "") -> str:
        """Ссылка на бота: метка ``param`` придёт в ``ctx.message.start_param`` и ``ctx.args``."""
        return f"{WEB_URL}/#/bot/{self.nik}" + (f"?start={param}" if param else "")

    def group_link(self, param: str = "") -> str:
        """Ссылка, которая предлагает добавить бота в группу."""
        return f"{WEB_URL}/#/bot/{self.nik}?startgroup={param}"

    # --- транспорт ---

    async def _post(self, path: str, *, json: Any = None, headers: Optional[dict] = None, timeout: Optional[float] = None, **kwargs: Any) -> dict:
        try:
            options = {"timeout": timeout} if timeout else {}
            response = await self.http.post(self.base_url + path, json=json, headers=headers, **options, **kwargs)
        except httpx.HTTPError as error:
            raise NetworkError(f"Нет связи с сервером: {error}") from error
        try:
            body = response.json()
        except ValueError:
            body = {"description": response.text[:200]}
        if response.status_code >= 400 or body.get("ok") is False:
            raise error_from_response(response.status_code, body)
        return body

    async def _authorize(self, force: bool = False, stale: str = "") -> str:
        async with self._auth_lock:
            if force and self._token != stale:
                return self._token  # другой запрос уже обновил токен
            if not force and self._token and time.time() < self._token_until:
                return self._token
            body = await self._post("/bot/auth", json={"bot_nik_name": self.nik, "api_key": self.api_key})
            self._token = body["bot_token"]
            self._token_until = time.time() + float(body.get("expires_in", 600)) - TOKEN_MARGIN_SECONDS
            return self._token

    async def _request(self, path: str, retry_network: bool = True, **kwargs: Any) -> dict:
        """Запрос с токеном: повторы при лимите и сбоях, новый токен при 401."""
        for attempt in range(MAX_RETRIES + 1):
            token = await self._authorize()
            try:
                return await self._post(path, headers={"Authorization": f"Bot {token}"}, **kwargs)
            except RateLimited as error:
                if attempt == MAX_RETRIES:
                    raise
                await asyncio.sleep(error.retry_after)
            except AuthError as error:
                if error.status != 401 or attempt > 0:
                    raise
                await self._authorize(force=True, stale=token)
            except (NetworkError, ServerError):
                if not retry_network or attempt == MAX_RETRIES:
                    raise
                await asyncio.sleep(min(2 ** attempt, 15))
        raise WekonError("Запрос не удался")

    async def call(self, method: str, **details: Any) -> Any:
        """Вызов любого метода по имени API: ``await bot.call("getMe")``."""
        # Отправку без client_message_id нельзя повторять после обрыва: сообщение могло уйти.
        safe = "client_message_id" in details or method.startswith(("get", "answer", "edit", "set", "stop", "read", "delete"))
        body = {"header": {"request_id": uuid.uuid4().hex, "bot_nik_name": self.nik, "api_version": "v1"},
                "method": method, "details": _clean(details)}
        return (await self._request("/bot/call", retry_network=safe, json=body))["result"]

    async def get_updates(self, offset: int = 0, *, limit: int = 50, timeout: int = 25, allowed_updates: Optional[Iterable[str]] = None) -> list:
        """Забрать события (сырые словари). Обычно это делает ``bot.run()``."""
        payload = _clean({"offset": offset, "limit": limit, "timeout": timeout,
                          "allowed_updates": list(allowed_updates) if allowed_updates else None})
        return (await self._request("/bot/updates/get", json=payload, timeout=timeout + 10))["result"]

    async def logout(self) -> None:
        """Отозвать текущий токен."""
        await self._request("/bot/auth/logout", retry_network=False)
        self._token, self._token_until = "", 0.0

    # --- файлы ---

    async def upload(self, file: Union[Path, str, bytes], filename: Optional[str] = None, media_type: Optional[str] = None) -> str:
        """Загрузить файл и получить ``file_id``.

        Вид файла сервер определяет по расширению имени. Для ``bytes`` библиотека подбирает имя по началу файла;
        если формат редкий, передайте ``filename``. ``media_type="voice"`` делает аудио голосовым.
        """
        if isinstance(file, bytes):
            content, name = file, filename or _bytes_name(file)
        else:
            path = Path(file)
            content, name = await asyncio.to_thread(path.read_bytes), filename or path.name
        data = {"media_type": media_type} if media_type else None
        body = await self._request("/bot/files/upload", retry_network=False, files={"file": (name, content)}, data=data, timeout=120)
        return body["result"]["file_id"]

    async def get_file(self, file_id: Optional[str] = None, attachment_id: Optional[int] = None) -> dict:
        """Сведения о файле и ссылка на него."""
        return await self.call("getFile", file_id=file_id, attachment_id=attachment_id)

    async def download(self, file: Any, path: Union[str, Path, None] = None) -> Union[bytes, Path]:
        """Скачать файл: вложение, ``attachment:<id>``, id вложения или свой ``file_id``.

        Без ``path`` вернёт байты, с ``path`` запишет на диск по частям: так удобнее для видео.
        """
        file = getattr(file, "ref", file)
        params = {"attachment_id": file} if isinstance(file, int) else {"file_id": file}
        for attempt in range(MAX_RETRIES + 1):
            token = await self._authorize()
            try:
                return await self._fetch(params, token, path)
            except RateLimited as error:
                if attempt == MAX_RETRIES:
                    raise
                await asyncio.sleep(error.retry_after)
            except AuthError as error:
                if error.status != 401 or attempt > 0:
                    raise
                await self._authorize(force=True, stale=token)
            except (NetworkError, ServerError):
                if attempt == MAX_RETRIES:
                    raise
                await asyncio.sleep(min(2 ** attempt, 15))
        raise WekonError("Запрос не удался")

    async def _fetch(self, params: dict, token: str, path: Union[str, Path, None]) -> Union[bytes, Path]:
        try:
            async with self.http.stream("GET", self.base_url + "/bot/files/download", params=params,
                                        headers={"Authorization": f"Bot {token}"}, timeout=httpx.Timeout(300, connect=10)) as response:
                if response.status_code >= 400:
                    raw = await response.aread()
                    try:
                        body = response.json()
                    except ValueError:
                        body = {"description": raw[:200].decode(errors="replace")}
                    raise error_from_response(response.status_code, body)
                if path is None:
                    return await response.aread()
                target = Path(path)
                partial = target.with_name(target.name + ".part")
                try:
                    with partial.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            await asyncio.to_thread(output.write, chunk)
                    partial.replace(target)
                finally:
                    partial.unlink(missing_ok=True)
                return target
        except httpx.HTTPError as error:
            raise NetworkError(f"Нет связи с сервером: {error}") from error

    async def _media(self, value: Media, media_type: Optional[str] = None) -> str:
        """``Path`` и ``bytes`` загружаются сами; строка — file_id, attachment:… или https."""
        return value if isinstance(value, str) else await self.upload(value, media_type=media_type)

    # --- общие настройки отправки ---

    @staticmethod
    def _options(reply_to=None, thread=None, silent=None, protect=None, spoiler=None, caption_above=None, quote=None, quote_position=None,
                 allow_without_reply=None, bind_to=None, self_destruct=None, link_preview=None, **rest: Any) -> dict:
        """Понятные имена общих полей; остальные поля API проходят как есть."""
        options = dict(rest)
        if reply_to is not None and (quote is not None or allow_without_reply is not None):
            options["reply_parameters"] = _clean({"message_id": _message_id(reply_to), "quote": quote, "quote_position": quote_position,
                                                  "allow_sending_without_reply": allow_without_reply})
        elif reply_to is not None:
            options["reply_to_message_id"] = _message_id(reply_to)
        if isinstance(link_preview, bool):
            link_preview = None if link_preview else {"is_disabled": True}
        return {**options, **_clean({
            "message_thread_id": thread, "disable_notification": silent, "protect_content": protect, "has_spoiler": spoiler,
            "show_caption_above_media": caption_above, "self_destruct_seconds": self_destruct, "link_preview_options": link_preview,
            "bind_to": bind(bind_to) if bind_to is not None and not isinstance(bind_to, dict) else bind_to,
        })}

    @staticmethod
    def _body(text: Optional[str], card: Optional[Card]) -> dict:
        components = card.to_list() if isinstance(card, Card) else card
        if text is None and isinstance(card, Card):
            text = card.fallback_text()
        return _clean({"text": text, "components": components})

    # --- сообщения ---

    async def send_message(self, chat_id: int, text: Optional[str] = None, *, card: Any = None, keyboard: Any = None, html: bool = False,
                           schedule: Any = None, page: Optional[str] = None, **options: Any) -> Sent:
        """Отправить текст или карточку. ``html=True`` — разметка HTML, ``schedule`` — отложить."""
        details = {**self._body(text, card), "chat_id": chat_id, "reply_markup": markup_of(keyboard), "page": page,
                   "parse_mode": "HTML" if html else None, "schedule_date": to_unix(schedule) if schedule is not None else None,
                   "client_message_id": options.pop("client_message_id", uuid.uuid4().hex), **self._options(**options)}
        return Sent.parse(await self.call("sendMessage", **details))

    async def edit_message(self, chat_id: int = 0, message_id: int = 0, text: Optional[str] = None, *, card: Any = None, keyboard: Any = None,
                           html: bool = False, page: Optional[str] = None, inline_message_id: Optional[str] = None, **fields: Any) -> dict:
        """Изменить текст и карточку. Без текста меняются только кнопки. ``card=[]`` с текстом убирает карточку."""
        target = {"inline_message_id": inline_message_id} if inline_message_id else {"chat_id": chat_id, "message_id": message_id}
        body = self._body(text, card)
        if not body:
            return await self.call("editMessageReplyMarkup", **target, reply_markup=markup_of(keyboard), page=page)
        return await self.call("editMessageText", **target, **body, reply_markup=markup_of(keyboard), page=page,
                               parse_mode="HTML" if html else None, **fields)

    async def edit_caption(self, chat_id: int = 0, message_id: int = 0, caption: str = "", *, keyboard: Any = None, html: bool = False,
                           page: Optional[str] = None, inline_message_id: Optional[str] = None, **fields: Any) -> dict:
        target = {"inline_message_id": inline_message_id} if inline_message_id else {"chat_id": chat_id, "message_id": message_id}
        return await self.call("editMessageCaption", **target, caption=caption, reply_markup=markup_of(keyboard), page=page,
                               parse_mode="HTML" if html else None, **fields)

    async def edit_keyboard(self, chat_id: int = 0, message_id: int = 0, keyboard: Any = None, *, page: Optional[str] = None,
                            inline_message_id: Optional[str] = None) -> dict:
        """Заменить кнопки; без ``keyboard`` убрать их."""
        target = {"inline_message_id": inline_message_id} if inline_message_id else {"chat_id": chat_id, "message_id": message_id}
        return await self.call("editMessageReplyMarkup", **target, reply_markup=markup_of(keyboard) or {}, page=page)

    async def edit_media(self, chat_id: int, message_id: int, media: Media, *, caption: Optional[str] = None, keyboard: Any = None, html: bool = False, **fields: Any) -> dict:
        return await self.call("editMessageMedia", chat_id=chat_id, message_id=message_id, file_id=await self._media(media), caption=caption,
                               reply_markup=markup_of(keyboard), parse_mode="HTML" if html else None, **fields)

    async def delete_message(self, chat_id: int, message_id: int) -> Any:
        return await self.call("deleteMessage", chat_id=chat_id, message_id=message_id)

    async def delete_messages(self, chat_id: int, message_ids: Iterable[int]) -> Any:
        return await self.call("deleteMessages", chat_id=chat_id, message_ids=list(message_ids))

    async def forward_message(self, chat_id: int, from_chat_id: int, message_id: int, **options: Any) -> Sent:
        return Sent.parse(await self.call("forwardMessage", chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id, **self._options(**options)))

    async def forward_messages(self, chat_id: int, from_chat_id: int, message_ids: Iterable[int], **options: Any) -> list:
        result = await self.call("forwardMessages", chat_id=chat_id, from_chat_id=from_chat_id, message_ids=list(message_ids), **self._options(**options))
        return [Sent.parse(item) for item in result]

    async def copy_message(self, chat_id: int, from_chat_id: int, message_id: int, *, caption: Optional[str] = None, keyboard: Any = None, **options: Any) -> Sent:
        return Sent.parse(await self.call("copyMessage", chat_id=chat_id, from_chat_id=from_chat_id, message_id=message_id, caption=caption,
                                          reply_markup=markup_of(keyboard), **self._options(**options)))

    async def copy_messages(self, chat_id: int, from_chat_id: int, message_ids: Iterable[int], **options: Any) -> list:
        result = await self.call("copyMessages", chat_id=chat_id, from_chat_id=from_chat_id, message_ids=list(message_ids), **self._options(**options))
        return [Sent.parse(item) for item in result]

    async def save_draft(self, chat_id: int, text: str, *, keyboard: Any = None) -> dict:
        """Сохранить черновик сообщения: люди его не видят."""
        return await self.call("sendMessageDraft", chat_id=chat_id, text=text, reply_markup=markup_of(keyboard))

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> Any:
        """«Печатает…» и другие действия, держится 5 секунд."""
        return await self.call("sendChatAction", chat_id=chat_id, action=action)

    async def pin_message(self, chat_id: int, message_id: int, *, as_panel: bool = False) -> dict:
        """Закрепить; ``as_panel=True`` — своя карточка становится панелью под шапкой чата."""
        return await self.call("pinChatMessage", chat_id=chat_id, message_id=message_id, as_panel=as_panel or None)

    async def unpin_message(self, chat_id: int, message_id: int) -> dict:
        return await self.call("unpinChatMessage", chat_id=chat_id, message_id=message_id)

    async def react(self, chat_id: int, message_id: int, *emoji_ids: str) -> dict:
        """Поставить реакции (до 3, id из каталога Wekon); без них — снять все."""
        return await self.call("setMessageReaction", chat_id=chat_id, message_id=message_id, reaction=list(emoji_ids))

    async def read_messages(self, chat_id: int, message_id: int) -> Any:
        """Отметить прочитанным до этого сообщения включительно."""
        return await self.call("readMessages", chat_id=chat_id, message_id=message_id)

    async def get_scheduled_messages(self, chat_id: Optional[int] = None) -> list:
        return await self.call("getScheduledMessages", chat_id=chat_id)

    async def delete_scheduled_message(self, scheduled_id: int) -> Any:
        return await self.call("deleteScheduledMessage", scheduled_id=scheduled_id)

    # --- медиа ---

    async def _send_media(self, method: str, field: str, chat_id: int, media: Media, caption, keyboard, html, media_type=None, **options: Any) -> Sent:
        details = {"chat_id": chat_id, field: await self._media(media, media_type), "caption": caption, "reply_markup": markup_of(keyboard),
                   "parse_mode": "HTML" if html else None, "client_message_id": options.pop("client_message_id", uuid.uuid4().hex),
                   **self._options(**options)}
        return Sent.parse(await self.call(method, **details))

    async def send_photo(self, chat_id: int, photo: Media, caption: Optional[str] = None, *, keyboard: Any = None, html: bool = False, **options: Any) -> Sent:
        return await self._send_media("sendPhoto", "photo", chat_id, photo, caption, keyboard, html, **options)

    async def send_document(self, chat_id: int, document: Media, caption: Optional[str] = None, *, keyboard: Any = None, html: bool = False, **options: Any) -> Sent:
        return await self._send_media("sendDocument", "document", chat_id, document, caption, keyboard, html, **options)

    async def send_audio(self, chat_id: int, audio: Media, caption: Optional[str] = None, *, keyboard: Any = None, html: bool = False, **options: Any) -> Sent:
        return await self._send_media("sendAudio", "audio", chat_id, audio, caption, keyboard, html, **options)

    async def send_video(self, chat_id: int, video: Media, caption: Optional[str] = None, *, keyboard: Any = None, html: bool = False, **options: Any) -> Sent:
        return await self._send_media("sendVideo", "video", chat_id, video, caption, keyboard, html, **options)

    async def send_animation(self, chat_id: int, animation: Media, caption: Optional[str] = None, *, keyboard: Any = None, html: bool = False, **options: Any) -> Sent:
        return await self._send_media("sendAnimation", "animation", chat_id, animation, caption, keyboard, html, **options)

    async def send_voice(self, chat_id: int, voice: Media, caption: Optional[str] = None, *, keyboard: Any = None, html: bool = False, **options: Any) -> Sent:
        return await self._send_media("sendVoice", "voice", chat_id, voice, caption, keyboard, html, "voice", **options)

    async def send_video_note(self, chat_id: int, video_note: Media, *, keyboard: Any = None, **options: Any) -> Sent:
        return await self._send_media("sendVideoNote", "video_note", chat_id, video_note, None, keyboard, False, **options)

    async def send_media_group(self, chat_id: int, media: Iterable[Any], caption: Optional[str] = None, **options: Any) -> Sent:
        """Альбом: файлы или словари ``{"type": "photo", "file_id": ...}``."""
        items = []
        for entry in media:
            items.append(entry if isinstance(entry, dict) else {"type": "photo", "file_id": await self._media(entry)})
        details = {"chat_id": chat_id, "media": items, "caption": caption,
                   "client_message_id": options.pop("client_message_id", uuid.uuid4().hex), **self._options(**options)}
        return Sent.parse(await self.call("sendMediaGroup", **details))

    async def send_sticker(self, chat_id: int, sticker: Union[int, str], **options: Any) -> Sent:
        return Sent.parse(await self.call("sendSticker", chat_id=chat_id, sticker=sticker, **self._options(**options)))

    # --- опросы ---

    async def send_poll(self, chat_id: int, question: str, options: Iterable[str], *, anonymous: bool = True, multiple: bool = False, **rest: Any) -> Sent:
        return Sent.parse(await self.call("sendPoll", chat_id=chat_id, question=question, options=list(options), is_anonymous=anonymous,
                                          allows_multiple_answers=multiple or None, **self._options(**rest)))

    async def stop_poll(self, chat_id: int, message_id: int) -> dict:
        return await self.call("stopPoll", chat_id=chat_id, message_id=message_id)

    # --- счета ---

    async def send_invoice(self, chat_id: int, title: str, *, amount: Optional[int] = None, pay_url: Optional[str] = None, currency: Optional[str] = None,
                           description: Optional[str] = None, subtitle: Optional[str] = None, icon: Optional[str] = None, image: Optional[str] = None,
                           items: Optional[Iterable[dict]] = None, expires_at: Any = None, pay_text: Optional[str] = None,
                           card: Any = None, keyboard: Any = None, **options: Any) -> Sent:
        """Готовая карточка счёта. ``amount`` — в копейках, ``pay_url`` выдаёт ваша касса."""
        details = {"chat_id": chat_id, "title": title, "amount": amount, "pay_url": pay_url, "currency": currency, "description": description,
                   "subtitle": subtitle, "icon": icon, "image": image, "items": list(items) if items else None,
                   "expires_at": to_unix(expires_at) if expires_at is not None else None, "pay_text": pay_text,
                   "components": card.to_list() if isinstance(card, Card) else card, "reply_markup": markup_of(keyboard),
                   "client_message_id": options.pop("client_message_id", uuid.uuid4().hex), **self._options(**options)}
        return Sent.parse(await self.call("sendInvoice", **details))

    async def set_invoice_status(self, chat_id: int, message_id: int, status: str, *, status_text: Optional[str] = None, tone: Optional[str] = None,
                                 note: Optional[str] = None, receipt_url: Optional[str] = None, pay_url: Optional[str] = None, expires_at: Any = None,
                                 keyboard: Any = None) -> dict:
        """Статус счёта: pending, authorized, paid, failed, canceled, expired, refunded."""
        return await self.call("setInvoiceStatus", chat_id=chat_id, message_id=message_id, status=status, status_text=status_text, tone=tone, note=note,
                               receipt_url=receipt_url, pay_url=pay_url, expires_at=to_unix(expires_at) if expires_at is not None else None,
                               reply_markup=markup_of(keyboard))

    # --- ответы на нажатия и inline ---

    async def answer_callback(self, callback_id: str, text: Optional[str] = None, *, alert: bool = False, near_button: bool = False,
                              url: Optional[str] = None, haptic: Optional[str] = None, cache_time: Optional[int] = None) -> Any:
        return await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text, show_alert=alert or None, url=url, haptic=haptic,
                               position="button" if near_button else None, cache_time=cache_time)

    async def answer_inline(self, inline_query_id: str, results: Iterable[dict], *, cache_time: Optional[int] = None, personal: bool = False,
                            next_offset: Optional[str] = None, button: Optional[dict] = None) -> Any:
        return await self.call("answerInlineQuery", inline_query_id=inline_query_id, results=list(results), cache_time=cache_time,
                               is_personal=personal or None, next_offset=next_offset, button=button)

    # --- чаты ---

    async def get_me(self) -> dict:
        return await self.call("getMe")

    async def get_chat(self, chat_id: int) -> dict:
        return await self.call("getChat", chat_id=chat_id)

    async def get_chat_member(self, chat_id: int, user_id: int) -> dict:
        return await self.call("getChatMember", chat_id=chat_id, user_id=user_id)

    async def get_chat_member_count(self, chat_id: int) -> int:
        return await self.call("getChatMemberCount", chat_id=chat_id)

    async def get_chat_administrators(self, chat_id: int) -> list:
        return await self.call("getChatAdministrators", chat_id=chat_id)

    async def get_user_profile_photos(self, user_id: int, chat_id: Optional[int] = None) -> dict:
        return await self.call("getUserProfilePhotos", user_id=user_id, chat_id=chat_id)

    async def restrict_member(self, chat_id: int, user_id: int, *, can_send_messages: bool = False, until: Any = None) -> Any:
        """Запретить писать (нужно право от админа группы); ``can_send_messages=True`` снимает запрет."""
        return await self.call("restrictChatMember", chat_id=chat_id, user_id=user_id, permissions={"can_send_messages": can_send_messages},
                               until_date=to_unix(until) if until is not None else None)

    async def ban_member(self, chat_id: int, user_id: int) -> Any:
        return await self.call("banChatMember", chat_id=chat_id, user_id=user_id)

    async def unban_member(self, chat_id: int, user_id: int) -> Any:
        return await self.call("unbanChatMember", chat_id=chat_id, user_id=user_id)

    async def create_topic(self, chat_id: int, name: str) -> dict:
        return await self.call("createForumTopic", chat_id=chat_id, name=name)

    async def edit_topic(self, chat_id: int, thread_id: int, name: str) -> Any:
        return await self.call("editForumTopic", chat_id=chat_id, message_thread_id=thread_id, name=name)

    async def delete_topic(self, chat_id: int, thread_id: int) -> Any:
        return await self.call("deleteForumTopic", chat_id=chat_id, message_thread_id=thread_id)

    # --- профиль ---

    async def set_commands(self, commands: Any, *, scope: Optional[dict] = None, language: Optional[str] = None) -> Any:
        """Команды: словарь ``{"start": "Начать"}`` или список ``{command, description, params}``."""
        items = [{"command": name, "description": text} for name, text in commands.items()] if isinstance(commands, dict) else list(commands)
        return await self.call("setMyCommands", commands=items, scope=scope, language_code=language)

    async def get_commands(self, *, scope: Optional[dict] = None, language: Optional[str] = None) -> list:
        return await self.call("getMyCommands", scope=scope, language_code=language)

    async def delete_commands(self, *, scope: Optional[dict] = None, language: Optional[str] = None) -> Any:
        return await self.call("deleteMyCommands", scope=scope, language_code=language)

    async def set_name(self, name: str, language: Optional[str] = None) -> Any:
        return await self.call("setMyName", name=name, language_code=language)

    async def get_name(self, language: Optional[str] = None) -> str:
        return (await self.call("getMyName", language_code=language))["name"]

    async def set_description(self, description: str, language: Optional[str] = None) -> Any:
        return await self.call("setMyDescription", description=description, language_code=language)

    async def get_description(self, language: Optional[str] = None) -> str:
        return (await self.call("getMyDescription", language_code=language))["description"]

    async def set_short_description(self, short_description: str, language: Optional[str] = None) -> Any:
        return await self.call("setMyShortDescription", short_description=short_description, language_code=language)

    async def get_short_description(self, language: Optional[str] = None) -> str:
        return (await self.call("getMyShortDescription", language_code=language))["short_description"]

    async def set_profile_photo(self, photo: Media) -> Any:
        return await self.call("setMyProfilePhoto", file_id=await self._media(photo))

    async def remove_profile_photo(self) -> Any:
        return await self.call("removeMyProfilePhoto")

    async def set_menu_button(self, kind: str = "commands", chat_id: Optional[int] = None) -> Any:
        return await self.call("setChatMenuButton", menu_button={"type": kind}, chat_id=chat_id)

    async def get_menu_button(self, chat_id: Optional[int] = None) -> dict:
        return await self.call("getChatMenuButton", chat_id=chat_id)

    # --- вебхук ---

    async def set_webhook(self, url: str, *, secret: Optional[str] = None, allowed_updates: Optional[Iterable[str]] = None, drop_pending: bool = False) -> Any:
        return await self.call("setWebhook", url=url, secret_token=secret, allowed_updates=list(allowed_updates) if allowed_updates else None,
                               drop_pending_updates=drop_pending or None)

    async def delete_webhook(self, *, drop_pending: bool = False) -> Any:
        return await self.call("deleteWebhook", drop_pending_updates=drop_pending or None)

    async def get_webhook_info(self) -> dict:
        return await self.call("getWebhookInfo")
