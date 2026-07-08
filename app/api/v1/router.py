"""Aggregates all sub-routers into a single APIRouter."""
# app/api/v1/router.py
from fastapi import APIRouter
from app.api.v1 import (
     auth,
    #sessions,
    #llm_actions,
    # subscriptions,
    # dashboard,
    # admin,
    # auth_webhook,
)

api_router = APIRouter()

# api_router.include_router(topics.router)
#api_router.include_router(sessions.router)
#api_router.include_router(llm_actions.router)
# api_router.include_router(subscriptions.router)
# api_router.include_router(dashboard.router)
# api_router.include_router(admin.router)
# api_router.include_router(auth_webhook.router)