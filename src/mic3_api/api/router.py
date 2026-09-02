from fastapi import APIRouter

from mic3_api.api.routes.health import router as health_router
from mic3_api.api.routes.readiness import router as readiness_router


api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(readiness_router)
