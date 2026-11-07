"""Асинхронный бот для тех, кому нужен ``async def``: ``from wekon.aio import Bot``."""
from .bot import Bot, Context, MemoryStorage
from .client import Client

__all__ = ["Bot", "Client", "Context", "MemoryStorage"]
