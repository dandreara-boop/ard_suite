from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.core.logging import get_logger
from backend.app.db.session import SessionLocal

logger = get_logger(__name__)


def check_database_connection() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        logger.warning("Database connectivity check failed")
        return False
