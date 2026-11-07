"""События и объекты Bot API. У каждого есть ``raw`` — исходный словарь сервера."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class User:
    id: int
    nik: str = ""
    is_bot: bool = False
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, data: Optional[dict]) -> "User":
        data = data or {}
        return cls(int(data.get("user_id") or 0), data.get("nik_name") or "", bool(data.get("is_bot")), data)


@dataclass
class Chat:
    id: int
    type: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def is_private(self) -> bool:
        return self.type == "private"

    @classmethod
    def parse(cls, data: Optional[dict], fallback_id: int = 0) -> "Chat":
        data = data or {}
        return cls(int(data.get("chat_id") or fallback_id), data.get("chat_type") or "", data)


@dataclass
class Attachment:
    """Вложение сообщения. ``ref`` можно передать в любой ``send_*``."""

    id: int
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def ref(self) -> str:
        return f"attachment:{self.id}"

    @property
    def media_type(self) -> str:
        """image, video, voice, audio, document и другие."""
        return self.raw.get("media_type") or ""

    @property
    def name(self) -> str:
        return self.raw.get("original_name") or ""

    @property
    def mime_type(self) -> str:
        return self.raw.get("mime_type") or ""

    @property
    def size(self) -> int:
        return int(self.raw.get("size_bytes") or 0)

    @property
    def width(self) -> int:
        return int(self.raw.get("width") or 0)

    @property
    def height(self) -> int:
        return int(self.raw.get("height") or 0)

    @property
    def duration(self) -> int:
        return int(self.raw.get("duration_sec") or 0)


@dataclass
class Message:
    id: int
    chat: Chat
    user: User
    text: str = ""
    date: str = ""
    kind: str = "message"
    reply_to_id: Optional[int] = None
    thread_id: Optional[int] = None
    start_param: str = ""
    attachments: list = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def file(self) -> Optional[Attachment]:
        """Первое вложение; все — в ``attachments``."""
        return self.attachments[0] if self.attachments else None

    @property
    def users_shared(self) -> Any:
        return self.raw.get("users_shared")

    @property
    def chat_shared(self) -> Any:
        return self.raw.get("chat_shared")

    @property
    def contact(self) -> Any:
        return self.raw.get("contact")

    @property
    def date_shared(self) -> Any:
        return self.raw.get("date_shared")

    @property
    def time_shared(self) -> Any:
        return self.raw.get("time_shared")

    @property
    def color_shared(self) -> Any:
        return self.raw.get("color_shared")

    @classmethod
    def parse(cls, data: dict, kind: str = "message") -> "Message":
        return cls(
            id=int(data.get("message_id") or 0),
            chat=Chat.parse(data.get("chat"), int(data.get("chat_id") or 0)),
            user=User.parse(data.get("from")),
            text=data.get("text") or "",
            date=data.get("date") or "",
            kind=kind,
            reply_to_id=data.get("reply_to_message_id"),
            thread_id=data.get("message_thread_id"),
            start_param=data.get("start_param") or "",
            attachments=[Attachment(int(a.get("id") or 0), a) for a in data.get("attachments") or []],
            raw=data,
        )


@dataclass
class CallbackQuery:
    """Нажатие кнопки или элемента карточки, либо отправленная форма."""

    id: str
    message_id: int
    chat: Chat
    user: User
    data: str = ""
    value: Optional[str] = None
    form: Optional[dict] = None
    inline_message_id: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, data: dict) -> "CallbackQuery":
        return cls(
            id=data.get("id") or "",
            message_id=int(data.get("message_id") or 0),
            chat=Chat.parse(data.get("chat"), int(data.get("chat_id") or 0)),
            user=User.parse(data.get("from")),
            data=data.get("data") or "",
            value=data.get("value"),
            form=data.get("form_data"),
            inline_message_id=data.get("inline_message_id") or "",
            raw=data,
        )


@dataclass
class InlineQuery:
    id: str
    query: str
    offset: str
    user: User
    chat: Optional[Chat] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, data: dict) -> "InlineQuery":
        chat = Chat.parse(data["chat"]) if data.get("chat") else None
        return cls(data.get("id") or "", data.get("query") or "", data.get("offset") or "", User.parse(data.get("from")), chat, data)


@dataclass
class Update:
    """Событие: ``type`` как на сервере, ``payload`` — его содержимое."""

    id: int
    type: str
    payload: dict
    raw: dict = field(default_factory=dict, repr=False)

    #: Типы событий о новом сообщении.
    MESSAGE_TYPES = ("message", "photo", "audio", "document", "video", "animation", "voice", "video_note", "media_group")

    @classmethod
    def parse(cls, data: dict) -> "Update":
        kind = data.get("type") or ""
        key = "message" if kind in cls.MESSAGE_TYPES else kind
        return cls(int(data.get("update_id") or 0), kind, data.get(key) or {}, data)

    @property
    def message(self) -> Optional[Message]:
        if self.type in self.MESSAGE_TYPES or self.type == "edited_message":
            return Message.parse(self.payload, self.type)
        return None

    @property
    def callback(self) -> Optional[CallbackQuery]:
        return CallbackQuery.parse(self.payload) if self.type == "callback_query" else None

    @property
    def inline_query(self) -> Optional[InlineQuery]:
        return InlineQuery.parse(self.payload) if self.type == "inline_query" else None

    @property
    def chat_id(self) -> int:
        """Чат события; 0 — событие не из чата. По нему идёт порядок обработки."""
        chat = self.payload.get("chat") or {}
        return int(chat.get("chat_id") or self.payload.get("chat_id") or 0)

    @property
    def user(self) -> User:
        data = self.payload
        return User.parse(data.get("from") or data.get("user"))


@dataclass
class Sent:
    """Результат отправки."""

    message_id: int
    chat_id: int
    date: str = ""
    dedup: bool = False
    scheduled_id: Optional[int] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, data: dict) -> "Sent":
        return cls(int(data.get("message_id") or 0), int(data.get("chat_id") or 0), data.get("date") or "",
                   bool(data.get("dedup")), data.get("scheduled_id"), data)
