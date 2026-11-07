"""Карточки: сообщение из блоков и формы. Каждый метод добавляет блок и возвращает карточку."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Union

from .keyboards import _clean, _to_button


def to_unix(moment: Union[int, float, datetime, timedelta]) -> int:
    """Время в unix: число, ``datetime`` или ``timedelta`` от текущего момента."""
    if isinstance(moment, timedelta):
        return int((datetime.now(timezone.utc) + moment).timestamp())
    if isinstance(moment, datetime):
        return int(moment.timestamp())
    return int(moment)


def _day(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def _pairs(items: Any) -> list:
    """Пары из словаря или списка кортежей."""
    return list(items.items()) if isinstance(items, dict) else list(items)


def option(value: str, label: str, icon: Optional[str] = None) -> dict:
    """Вариант списка выбора."""
    return _clean({"value": value, "label": label, "icon": icon})


def cell(text: str = "", value: Optional[str] = None, *, tone=None, color=None, data=None,
         disabled: bool = False, hidden: bool = False, selected: bool = False) -> dict:
    """Клетка сетки, до 5 знаков. ``value`` — для выбора, ``data`` — отдельное нажатие."""
    return _clean({"text": text, "value": value, "tone": tone, "color": color, "callback_data": data,
                   "disabled": disabled or None, "hidden": hidden or None, "selected": selected or None})


def gap() -> dict:
    """Пустая клетка сетки: проход в зале или салоне."""
    return {"hidden": True}


def item(value: str, label: str, description: Optional[str] = None, *, icon=None,
         checked: bool = False, disabled: bool = False) -> dict:
    """Пункт чек-листа."""
    return _clean({"value": value, "label": label, "description": description, "icon": icon,
                   "checked": checked or None, "disabled": disabled or None})


def step(title: str, state: str = "pending", note: Optional[str] = None) -> dict:
    """Этап: ``done``, ``active``, ``pending`` или ``error``."""
    return _clean({"title": title, "state": state, "note": note})


def stat(label: str, value: Any, tone: Optional[str] = None) -> dict:
    """Показатель блока цифр."""
    return _clean({"label": label, "value": str(value), "tone": tone})


def tag(text: str, tone: Optional[str] = None) -> dict:
    """Метка."""
    return _clean({"text": text, "tone": tone})


def mark(day: Any, tone: Optional[str] = None, text: Optional[str] = None) -> dict:
    """Отметка дня календаря: цветная точка, ``text`` идёт в подпись под календарём."""
    return _clean({"date": _day(day), "tone": tone, "text": text})


class Card:
    """Карточка из блоков.

    >>> Card().header("Заказ №42", status="В пути", tone="accent").progress(0.7, "Доставка")
    """

    def __init__(self, fallback: Optional[str] = None) -> None:
        self.blocks: list = []
        self.fallback = fallback

    def _add(self, kind: str, **fields: Any) -> "Card":
        self.blocks.append({"type": kind, **_clean(fields)})
        return self

    def header(self, title: str, subtitle: Optional[str] = None, *, icon=None, avatar: bool = False,
               status: Optional[str] = None, tone: Optional[str] = None) -> "Card":
        return self._add("header", title=title, subtitle=subtitle, icon=icon, avatar="bot" if avatar else None,
                         status=_clean({"text": status, "tone": tone}) if status else None)

    def text(self, text: str, style: Optional[str] = None) -> "Card":
        """Текст: ``style`` — body, caption или title."""
        return self._add("text", text=text, style=style)

    def image(self, url: str, caption: Optional[str] = None, aspect: Optional[float] = None) -> "Card":
        return self._add("image", url=url, caption=caption, aspect=aspect)

    def gallery(self, images: Iterable[Any], aspect: Optional[float] = None) -> "Card":
        """Галерея: ссылки или пары ``(ссылка, подпись)``."""
        items = [{"url": image} if isinstance(image, str) else _clean({"url": image[0], "caption": image[1]}) for image in images]
        return self._add("gallery", images=items, aspect=aspect)

    def divider(self) -> "Card":
        return self._add("divider")

    def callout(self, text: str, tone: Optional[str] = None, icon: Optional[str] = None) -> "Card":
        return self._add("callout", text=text, tone=tone, icon=icon)

    def fields(self, items: Any, columns: Optional[int] = None) -> "Card":
        """Строки «название — значение»: словарь или пары."""
        return self._add("fields", items=[{"label": label, "value": str(value)} for label, value in _pairs(items)], columns=columns)

    def stats(self, *items: Any) -> "Card":
        """Крупные цифры: ``stat(...)`` или кортежи ``(подпись, значение[, тон])``."""
        return self._add("stats", items=[entry if isinstance(entry, dict) else stat(*entry) for entry in items])

    def progress(self, value: Optional[float] = None, label: Optional[str] = None, *, caption=None, tone=None, style=None,
                 segments=None, size=None, value_text=None, show_value=None, animated=None, indeterminate=None) -> "Card":
        """Прогресс 0..1: ``style`` — bar, ring или segments."""
        return self._add("progress", value=value, label=label, caption=caption, tone=tone, style=style, segments=segments,
                         size=size, value_text=value_text, show_value=show_value, animated=animated, indeterminate=indeterminate)

    def steps(self, *items: Any) -> "Card":
        """Этапы: ``step(...)`` или кортежи ``(название, состояние)``."""
        return self._add("steps", items=[entry if isinstance(entry, dict) else step(*entry) for entry in items])

    def rating(self, value: float, count: Optional[int] = None, label: Optional[str] = None) -> "Card":
        return self._add("rating", value=value, count=count, label=label)

    def tags(self, *items: Any) -> "Card":
        """Метки: строки, ``tag(...)`` или кортежи ``(текст, тон)``."""
        def convert(entry: Any) -> dict:
            if isinstance(entry, dict):
                return entry
            return tag(entry) if isinstance(entry, str) else tag(*entry)
        return self._add("tags", items=[convert(entry) for entry in items])

    def countdown(self, until: Any, label: Optional[str] = None, done_text: Optional[str] = None) -> "Card":
        """Обратный отсчёт: unix, ``datetime`` или ``timedelta``."""
        return self._add("countdown", until=to_unix(until), label=label, done_text=done_text)

    def list(self, items: Iterable[str], ordered: bool = False) -> "Card":
        return self._add("list", items=[str(entry) for entry in items], ordered=ordered or None)

    def table(self, columns: Iterable[str], rows: Iterable[Iterable[Any]]) -> "Card":
        return self._add("table", columns=[str(column) for column in columns], rows=[[str(value) for value in row] for row in rows])

    def collapsible(self, title: str, text: str, expanded: bool = False) -> "Card":
        return self._add("collapsible", title=title, text=text, expanded=expanded or None)

    def tabs(self, tabs: Any) -> "Card":
        """Вкладки: ``{"Название": Card(), ...}``."""
        return self._add("tabs", tabs=[{"title": title, "components": card.to_list()} for title, card in _pairs(tabs)])

    def buttons(self, *items: Any) -> "Card":
        """Кнопки внутри карточки: кортежи ``(текст, data)`` или ``button(...)``."""
        return self._add("buttons", buttons=[_to_button(entry) for entry in items])

    def select(self, data: str, options: Iterable[Any], selected: Optional[str] = None, *, label=None, placeholder=None, haptic=None) -> "Card":
        """Список выбора. Варианты: ``option(...)`` или кортежи ``(значение, подпись[, значок])``."""
        return self._add("select", callback_data=data, options=[entry if isinstance(entry, dict) else option(*entry) for entry in options],
                         selected=selected, label=label, placeholder=placeholder, haptic=haptic)

    def toggle(self, data: str, label: str, description: Optional[str] = None, checked: bool = False, *, haptic=None) -> "Card":
        return self._add("toggle", callback_data=data, label=label, description=description, checked=checked, haptic=haptic)

    def slider(self, data: str, min: float, max: float, value: Optional[float] = None, *, step: float = 1, label=None, unit=None, haptic=None) -> "Card":
        return self._add("slider", callback_data=data, min=min, max=max, step=step, value=min if value is None else value, label=label, unit=unit, haptic=haptic)

    def stepper(self, data: str, min: float, max: float, value: Optional[float] = None, *, step: float = 1, label=None, unit=None, haptic=None) -> "Card":
        return self._add("stepper", callback_data=data, min=min, max=max, step=step, value=min if value is None else value, label=label, unit=unit, haptic=haptic)

    def grid(self, columns: int, cells: Iterable[Any], *, select: Optional[str] = None, data: Optional[str] = None,
             max: Optional[int] = None, label: Optional[str] = None) -> "Card":
        """Сетка: поле игры или схема мест. ``select`` — single или multiple, тогда нужен ``data``."""
        return self._add("grid", columns=columns, cells=[entry if isinstance(entry, dict) else cell(str(entry)) for entry in cells],
                         select=select, callback_data=data, max=max, label=label)

    def checklist(self, data: str, items: Iterable[Any], *, select: Optional[str] = None, max: Optional[int] = None, label: Optional[str] = None) -> "Card":
        """Флажки, а при ``select="single"`` — радио. Пункты: ``item(...)`` или кортежи ``(значение, подпись)``."""
        return self._add("checklist", callback_data=data, items=[entry if isinstance(entry, dict) else globals()["item"](*entry) for entry in items],
                         select=select, max=max, label=label)

    def calendar(self, data: str, *, month: Optional[str] = None, min: Any = None, max: Any = None, disabled: Optional[Iterable[Any]] = None,
                 marks: Optional[Iterable[dict]] = None, selected: Optional[Iterable[Any]] = None, select: Optional[str] = None,
                 max_selected: Optional[int] = None, label: Optional[str] = None) -> "Card":
        """Календарь с выбором дня. Даты — строки YYYY-MM-DD или ``date``."""
        return self._add("calendar", callback_data=data, month=month, min=_day(min), max=_day(max),
                         disabled=[_day(day) for day in disabled] if disabled else None, marks=list(marks) if marks else None,
                         selected=[_day(day) for day in selected] if selected else None, select=select,
                         max_selected=max_selected, label=label)

    def to_list(self) -> list:
        return self.blocks

    def fallback_text(self) -> str:
        """Запасной текст для уведомлений и приложений без карточек."""
        if self.fallback:
            return self.fallback
        for block in self.blocks:
            if block["type"] == "header":
                return block["title"]
        for block in self.blocks:
            if block["type"] in ("text", "callout"):
                return block["text"]
        return "Карточка"


class Form:
    """Форма в шторке. ``data`` придёт боту в ``callback_query``, ответы — в ``ctx.form``.

    >>> Form("signup", "Запись").text("name", "Имя", required=True).date("day", "День")
    """

    def __init__(self, data: str, title: str, submit: Optional[str] = None) -> None:
        self.data = data
        self.title = title
        self.submit = submit
        self.pages: list = [{"fields": []}]

    def page(self, title: Optional[str] = None) -> "Form":
        """Новая страница формы."""
        if self.pages[-1]["fields"]:
            self.pages.append({"fields": []})
        if title:
            self.pages[-1]["title"] = title
        return self

    def _field(self, kind: str, id: str, label: str, **extra: Any) -> "Form":
        self.pages[-1]["fields"].append(_clean({"id": id, "type": kind, "label": label, **extra}))
        return self

    def text(self, id: str, label: str, *, required=None, placeholder=None, hint=None, default=None, max_length=None) -> "Form":
        return self._field("text", id, label, required=required, placeholder=placeholder, hint=hint, default=default, max_length=max_length)

    def textarea(self, id: str, label: str, *, required=None, placeholder=None, hint=None, default=None, max_length=None) -> "Form":
        return self._field("textarea", id, label, required=required, placeholder=placeholder, hint=hint, default=default, max_length=max_length)

    def number(self, id: str, label: str, *, required=None, placeholder=None, hint=None, default=None, min=None, max=None) -> "Form":
        return self._field("number", id, label, required=required, placeholder=placeholder, hint=hint, default=default, min=min, max=max)

    def select(self, id: str, label: str, options: Iterable[Any], *, required=None, hint=None, default=None) -> "Form":
        return self._field("select", id, label, options=[entry if isinstance(entry, dict) else option(*entry) for entry in options],
                           required=required, hint=hint, default=default)

    def multiselect(self, id: str, label: str, options: Iterable[Any], *, required=None, hint=None, default=None) -> "Form":
        return self._field("multiselect", id, label, options=[entry if isinstance(entry, dict) else option(*entry) for entry in options],
                           required=required, hint=hint, default=default)

    def date(self, id: str, label: str, *, required=None, hint=None, default=None, min=None, max=None) -> "Form":
        return self._field("date", id, label, required=required, hint=hint, default=_day(default), min=_day(min), max=_day(max))

    def time(self, id: str, label: str, *, required=None, hint=None, default=None) -> "Form":
        return self._field("time", id, label, required=required, hint=hint, default=default)

    def toggle(self, id: str, label: str, *, hint=None, placeholder=None, default=None) -> "Form":
        return self._field("toggle", id, label, hint=hint, placeholder=placeholder, default=default)

    def color(self, id: str, label: str, *, required=None, hint=None, default=None) -> "Form":
        return self._field("color", id, label, required=required, hint=hint, default=default)

    def to_dict(self) -> dict:
        return _clean({"callback_data": self.data, "title": self.title, "submit_text": self.submit, "pages": self.pages})
