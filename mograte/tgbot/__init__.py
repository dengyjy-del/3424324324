"""Телеграм-часть раздела оценок."""

from .admin_router import router as admin_router
from .demo_router import router as demo_router
from .rating_router import router as rating_router
from .tries_router import RememberUsername
from .tries_router import router as tries_router

__all__ = [
    "rating_router",
    "admin_router",
    "demo_router",
    "tries_router",
    "RememberUsername",
]
