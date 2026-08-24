from fastapi import APIRouter, HTTPException, status

from backend.app.core.config import settings
from backend.app.core.logging import get_logger
from backend.app.db.health import check_database_connection

router = APIRouter()
logger = get_logger(__name__)


@router.get("/health")
def health() -> dict[str, str]:
    if not check_database_connection():
        logger.error("Database health check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "error",
                "database": "unavailable",
                "app": settings.app_name,
            },
        )

    return {
        "status": "ok",
        "database": "connected",
        "app": settings.app_name,
    }
