"""wekon — библиотека для ботов Wekon."""
from . import inline, payments
from .bot import Context, MemoryStorage
from .cards import Card, Form, cell, gap, item, mark, option, stat, step, tag
from .client import Client, bind, check_login
from .errors import (AuthError, BadRequest, Conflict, Forbidden, NetworkError, NotFound, NotSupported,
                     RateLimited, ServerError, WekonError)
from .keyboards import (ForceReply, Keyboard, RemoveKeyboard, ReplyKeyboard, button, copy_button, form_button,
                        inline_button, login_button, url_button)
from .simple import Bot
from .types import Attachment, CallbackQuery, Chat, InlineQuery, Message, Sent, Update, User

__version__ = "0.1.0"
__all__ = [
    "Bot", "Client", "Context", "MemoryStorage", "Card", "Form", "Keyboard", "ReplyKeyboard", "RemoveKeyboard", "ForceReply",
    "button", "url_button", "copy_button", "form_button", "inline_button", "login_button", "cell", "gap", "item", "mark", "option",
    "stat", "step", "tag", "bind", "check_login", "inline", "payments", "WekonError", "AuthError", "BadRequest", "Conflict", "Forbidden",
    "NetworkError", "NotFound", "NotSupported", "RateLimited", "ServerError", "Attachment", "CallbackQuery", "Chat", "InlineQuery",
    "Message", "Sent", "Update", "User",
]
