"""Кнопки и клавиатуры: сборка без ручных словарей."""
from __future__ import annotations

from typing import Any, Optional, Union


def _clean(data: dict) -> dict:
    return {key: value for key, value in data.items() if value is not None}


def _look(icon=None, style=None, color=None, hint=None, width=None, height=None, haptic=None, loading=None,
          confirm=None, checked=None, expires_at=None, long_data=None) -> dict:
    if isinstance(confirm, str):
        confirm = {"title": confirm}
    return _clean({"icon": icon, "style": style, "color": color, "hint": hint, "width": width, "height": height,
                   "haptic": haptic, "loading": loading, "confirm": confirm, "checked": checked,
                   "expires_at": expires_at, "long_callback_data": long_data})


def button(text: str, data: str, **look: Any) -> dict:
    """Кнопка с нажатием: бот получит ``data``.

    Необязательно: ``icon``, ``style`` (primary, success, danger), ``color``, ``hint``, ``width``,
    ``height``, ``haptic``, ``loading``, ``confirm`` (строка или словарь), ``checked``,
    ``expires_at``, ``long_data`` (действие на удержание).

    Удержание делает что-то одно: с ``long_data`` присылает его боту, а без него показывает
    кнопку крупно с подсказкой ``hint``.
    """
    return {"text": text, "callback_data": data, **_look(**look)}


def url_button(text: str, url: str, **look: Any) -> dict:
    """Кнопка-ссылка."""
    return {"text": text, "url": url, **_look(**look)}


def copy_button(text: str, value: str, **look: Any) -> dict:
    """Кнопка копирует ``value`` в буфер обмена."""
    return {"text": text, "copy_text": {"text": value}, **_look(**look)}


def form_button(text: str, form: Any, **look: Any) -> dict:
    """Кнопка открывает форму."""
    return {"text": text, "form": form.to_dict() if hasattr(form, "to_dict") else form, **_look(**look)}


def inline_button(text: str, query: str = "", here: bool = False, **look: Any) -> dict:
    """Кнопка открывает inline-режим: ``here=True`` — в этом чате, иначе выбор чата."""
    key = "switch_inline_query_current_chat" if here else "switch_inline_query"
    return {"text": text, key: query, **_look(**look)}


def login_button(text: str, url: str, forward_text: Optional[str] = None, **look: Any) -> dict:
    """Вход на сайт через Wekon."""
    return {"text": text, "login_url": _clean({"url": url, "forward_text": forward_text}), **_look(**look)}


def _to_button(item: Union[dict, tuple]) -> dict:
    return button(item[0], item[1]) if isinstance(item, (tuple, list)) else item


class Keyboard:
    """Кнопки под сообщением.

    >>> Keyboard().row(("Да", "yes"), ("Нет", "no")).row(url_button("Сайт", "https://wekon.ru"))
    >>> Keyboard().button("Купить", "buy", style="primary")
    >>> Keyboard(columns=4)  # сетка: у кнопок появляются width и height
    """

    def __init__(self, columns: Optional[int] = None) -> None:
        self.columns = columns
        self.rows: list = []

    def row(self, *items: Any) -> "Keyboard":
        """Новый ряд: кортежи ``(текст, data)`` или готовые кнопки."""
        if items:
            self.rows.append([_to_button(item) for item in items])
        return self

    def add(self, *items: Any) -> "Keyboard":
        """Кнопки в текущий ряд."""
        if not self.rows:
            self.rows.append([])
        self.rows[-1].extend(_to_button(item) for item in items)
        return self

    def button(self, text: str, data: str, **look: Any) -> "Keyboard":
        return self.row(button(text, data, **look))

    def url(self, text: str, url: str, **look: Any) -> "Keyboard":
        return self.row(url_button(text, url, **look))

    def copy(self, text: str, value: str, **look: Any) -> "Keyboard":
        return self.row(copy_button(text, value, **look))

    def form(self, text: str, form: Any, **look: Any) -> "Keyboard":
        return self.row(form_button(text, form, **look))

    def inline(self, text: str, query: str = "", here: bool = False, **look: Any) -> "Keyboard":
        return self.row(inline_button(text, query, here, **look))

    def login(self, text: str, url: str, forward_text: Optional[str] = None, **look: Any) -> "Keyboard":
        return self.row(login_button(text, url, forward_text, **look))

    def to_dict(self) -> dict:
        return _clean({"inline_keyboard": self.rows, "columns": self.columns})


class ReplyKeyboard:
    """Клавиатура вместо системной: нажатие уходит сообщением или открывает окно выбора."""

    def __init__(self, placeholder: Optional[str] = None, one_time: bool = False, persistent: bool = False,
                 selective: bool = False, resize: bool = True) -> None:
        self.options = _clean({"resize_keyboard": resize or None, "one_time_keyboard": one_time or None,
                               "is_persistent": persistent or None, "selective": selective or None,
                               "input_field_placeholder": placeholder})
        self.rows: list = []

    def row(self, *items: Any) -> "ReplyKeyboard":
        """Новый ряд: строки или готовые кнопки."""
        if items:
            self.rows.append([{"text": item} if isinstance(item, str) else item for item in items])
        return self

    def button(self, text: str, **look: Any) -> "ReplyKeyboard":
        """Кнопка с оформлением: ``icon``, ``style``, ``color``, ``hint``."""
        return self.row(_clean({"text": text, **look}))

    def _request(self, text: str, key: str, value: Any, look: dict) -> "ReplyKeyboard":
        return self.row(_clean({"text": text, key: value, **look}))

    def users(self, text: str, request_id: int = 1, max_quantity: int = 1, bots: Optional[bool] = None, **look: Any) -> "ReplyKeyboard":
        """Выбор людей: ответ придёт в ``message.users_shared``."""
        return self._request(text, "request_users", _clean({"request_id": request_id, "max_quantity": max_quantity, "user_is_bot": bots}), look)

    def chat(self, text: str, request_id: int = 1, channel: bool = False, created: Optional[bool] = None, **look: Any) -> "ReplyKeyboard":
        """Выбор группы или канала: ``message.chat_shared``."""
        return self._request(text, "request_chat", _clean({"request_id": request_id, "chat_is_channel": channel, "chat_is_created": created}), look)

    def contact(self, text: str, **look: Any) -> "ReplyKeyboard":
        """Отправка своего телефона: ``message.contact``."""
        return self._request(text, "request_contact", True, look)

    def date(self, text: str, request_id: int = 1, min: Optional[str] = None, max: Optional[str] = None, **look: Any) -> "ReplyKeyboard":
        """Выбор даты: ``message.date_shared``."""
        return self._request(text, "request_date", _clean({"request_id": request_id, "min": min, "max": max}), look)

    def time(self, text: str, request_id: int = 1, **look: Any) -> "ReplyKeyboard":
        """Выбор времени: ``message.time_shared``."""
        return self._request(text, "request_time", {"request_id": request_id}, look)

    def color(self, text: str, request_id: int = 1, **look: Any) -> "ReplyKeyboard":
        """Выбор цвета: ``message.color_shared``."""
        return self._request(text, "request_color", {"request_id": request_id}, look)

    def file(self, text: str, request_id: int = 1, image_only: bool = False, multiple: bool = False, **look: Any) -> "ReplyKeyboard":
        """Выбор файла: придёт обычным сообщением."""
        return self._request(text, "request_file", _clean({"request_id": request_id, "image_only": image_only or None, "multiple": multiple or None}), look)

    def to_dict(self) -> dict:
        return {"keyboard": self.rows, **self.options}


class RemoveKeyboard:
    """Убрать клавиатуру."""

    def to_dict(self) -> dict:
        return {"remove_keyboard": True}


class ForceReply:
    """Попросить ответить именно на это сообщение."""

    def __init__(self, placeholder: Optional[str] = None) -> None:
        self.placeholder = placeholder

    def to_dict(self) -> dict:
        return _clean({"force_reply": True, "input_field_placeholder": self.placeholder})


def _simple_button(text: str, target: Any) -> dict:
    """Вид кнопки по цели: форма, ссылка или данные нажатия."""
    if hasattr(target, "to_dict"):
        return form_button(text, target)
    if isinstance(target, str) and target.startswith(("https://", "http://", "wekon://")):
        return url_button(text, target)
    return button(text, target)


def _to_simple(item: Any) -> dict:
    return _simple_button(item[0], item[1]) if isinstance(item, tuple) else item


def buttons_of(buttons: Any) -> dict:
    """Простая запись кнопок.

    ``{"Да": "yes"}`` или ``[("Да", "yes"), ("Сайт", "https://…")]`` — по кнопке в ряд;
    вложенный список — кнопки в один ряд. Вид определяется сам: ссылка, форма или нажатие.
    """
    if isinstance(buttons, dict):
        buttons = list(buttons.items())
    rows = [[_to_simple(entry) for entry in row] if isinstance(row, list) else [_to_simple(row)] for row in buttons]
    return {"inline_keyboard": rows}


def keys_of(keys: Any) -> dict:
    """Клавиатура вместо системной: список подписей — один ряд, вложенные списки — ряды."""
    keyboard = ReplyKeyboard()
    for row in keys if keys and isinstance(keys[0], list) else [keys]:
        keyboard.row(*row)
    return keyboard.to_dict()


def markup_of(keyboard: Any) -> Optional[dict]:
    """Клавиатура в виде, который ждёт API; словарь API проходит как есть."""
    if keyboard is None:
        return None
    if isinstance(keyboard, list):
        return buttons_of(keyboard)
    if isinstance(keyboard, dict):
        known = ("inline_keyboard", "keyboard", "remove_keyboard", "force_reply")
        return keyboard if not keyboard or any(key in keyboard for key in known) else buttons_of(keyboard)
    return keyboard.to_dict()
