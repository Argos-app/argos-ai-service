"""Agregador das rotas da API v1."""

from fastapi import APIRouter

from app.api.v1.routes import interactions, sessions

router = APIRouter(prefix="/v1")
router.include_router(sessions.router)
router.include_router(interactions.router)
