"""Aggregates all v1 sub-routers into a single APIRouter.

Mounted under `settings.API_V1_PREFIX` (/api/v1) by app.main.create_app().
"""
from fastapi import APIRouter

from app.api.v1 import auth, categories, orders, payments, products, users

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(categories.router)
api_router.include_router(products.router)
api_router.include_router(orders.router)
api_router.include_router(payments.router)
