"""Ошибки Bot API: у каждой группы свой класс, стабильный код лежит в ``code``."""
from __future__ import annotations


class WekonError(Exception):
    """Ошибка Wekon. ``code`` — стабильный код сервера, ``status`` — HTTP-статус."""

    def __init__(self, description: str, code: str = "", status: int = 0) -> None:
        super().__init__(f"{code}: {description}" if code else description)
        self.description = description
        self.code = code
        self.status = status


class NetworkError(WekonError):
    """Сервер недоступен: сеть, таймаут, обрыв."""


class AuthError(WekonError):
    """Неверный ключ, отозванный ключ или приостановленный бот."""


class BadRequest(WekonError):
    """Сервер отклонил параметры: смотрите ``code`` и ``description``."""


class Forbidden(WekonError):
    """Нет прав: бот не в чате, чужое сообщение, нет права в группе."""


class NotFound(WekonError):
    """Сообщение, файл, чат или нажатие не найдены."""


class Conflict(WekonError):
    """Ждут события сразу двумя способами: ``polling_conflict`` или ``webhook_active``."""


class RateLimited(WekonError):
    """Слишком часто. ``retry_after`` — сколько секунд подождать."""

    def __init__(self, description: str, code: str = "", status: int = 429, retry_after: float = 1) -> None:
        super().__init__(description, code, status)
        self.retry_after = retry_after


class NotSupported(WekonError):
    """Метод пока не поддерживается сервером."""


class ServerError(WekonError):
    """Сбой на сервере: повторите позже."""


_BY_STATUS = {400: BadRequest, 401: AuthError, 403: Forbidden, 404: NotFound, 409: Conflict, 410: BadRequest, 501: NotSupported}


def error_from_response(status: int, body: dict) -> WekonError:
    """Собирает исключение из ответа сервера."""
    code = str(body.get("error") or "")
    description = str(body.get("description") or code or f"HTTP {status}")
    if status == 429:
        retry_after = (body.get("parameters") or {}).get("retry_after", 1)
        return RateLimited(description, code, status, float(retry_after))
    if code == "bot_suspended":
        return AuthError(description, code, status)
    kind = _BY_STATUS.get(status, ServerError if status >= 500 else WekonError)
    return kind(description, code, status)
