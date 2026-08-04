"""Canonical conversations package (Phase P3A)."""

from .repository import SQLiteConversationRepository
from .router import router as conversations_router
from .service import ConversationError, ConversationService

__all__ = [
    "SQLiteConversationRepository",
    "ConversationError",
    "ConversationService",
    "conversations_router",
]
