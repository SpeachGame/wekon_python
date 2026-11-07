"""Результаты inline-режима: все виды, которые принимает сервер."""
from __future__ import annotations

from typing import Any, Optional

from .cards import Card
from .keyboards import _clean, markup_of


def article(id: str, title: str, text: Optional[str] = None, *, description: Optional[str] = None, card: Optional[Card] = None, keyboard: Any = None,
            url: Optional[str] = None, thumbnail: Optional[str] = None) -> dict:
    """Текст или карточка, которую человек отправит в чат."""
    content = _clean({"message_text": text or (card.fallback_text() if card else title), "components": card.to_list() if card else None})
    return _clean({"type": "article", "id": id, "title": title, "description": description, "url": url, "thumbnail_url": thumbnail,
                   "input_message_content": content, "reply_markup": markup_of(keyboard)})


def _media(kind: str, id: str, url: Optional[str], file_id: Optional[str], title: Optional[str], description: Optional[str], caption: Optional[str],
           thumbnail: Optional[str], keyboard: Any) -> dict:
    """Медиа: либо ссылка https, либо файл бота."""
    return _clean({"type": kind, "id": id, f"{kind}_url": url, f"{kind}_file_id": file_id, "title": title, "description": description,
                   "caption": caption, "thumbnail_url": thumbnail, "reply_markup": markup_of(keyboard)})


def photo(id: str, url: Optional[str] = None, *, file_id=None, title=None, description=None, caption=None, thumbnail=None, keyboard: Any = None) -> dict:
    return _media("photo", id, url, file_id, title, description, caption, thumbnail, keyboard)


def gif(id: str, url: Optional[str] = None, *, file_id=None, title=None, description=None, caption=None, thumbnail=None, keyboard: Any = None) -> dict:
    return _media("gif", id, url, file_id, title, description, caption, thumbnail, keyboard)


def mpeg4_gif(id: str, url: Optional[str] = None, *, file_id=None, title=None, description=None, caption=None, thumbnail=None, keyboard: Any = None) -> dict:
    return _media("mpeg4", id, url, file_id, title, description, caption, thumbnail, keyboard) | {"type": "mpeg4_gif"}


def video(id: str, url: Optional[str] = None, *, file_id=None, title=None, description=None, caption=None, thumbnail=None, keyboard: Any = None) -> dict:
    return _media("video", id, url, file_id, title, description, caption, thumbnail, keyboard)


def audio(id: str, url: Optional[str] = None, *, file_id=None, title=None, description=None, caption=None, thumbnail=None, keyboard: Any = None) -> dict:
    return _media("audio", id, url, file_id, title, description, caption, thumbnail, keyboard)


def voice(id: str, url: Optional[str] = None, *, file_id=None, title=None, description=None, caption=None, thumbnail=None, keyboard: Any = None) -> dict:
    return _media("voice", id, url, file_id, title, description, caption, thumbnail, keyboard)


def document(id: str, title: Optional[str] = None, *, url=None, file_id=None, description=None, caption=None, thumbnail=None, keyboard: Any = None) -> dict:
    return _media("document", id, url, file_id, title, description, caption, thumbnail, keyboard)


def sticker(id: str, sticker_id: int, *, keyboard: Any = None) -> dict:
    """Стикер Wekon."""
    return _clean({"type": "sticker", "id": id, "sticker_file_id": sticker_id, "reply_markup": markup_of(keyboard)})
