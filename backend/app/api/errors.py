from fastapi import HTTPException, status

from backend.app.business.catalog.variant_rules import (
    BARCODE_ALREADY_EXISTS,
    VARIANT_ALREADY_EXISTS,
)
from backend.app.services.exceptions import (
    BusinessRuleViolation,
    ConflictError,
    NotFoundError,
    ValidationError,
)


def map_catalog_error(error: Exception) -> HTTPException:
    if isinstance(error, BusinessRuleViolation):
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        if error.code in {BARCODE_ALREADY_EXISTS, VARIANT_ALREADY_EXISTS}:
            status_code = status.HTTP_409_CONFLICT
        return HTTPException(
            status_code=status_code,
            detail={
                "code": error.code,
                "message": str(error),
                "status": error.status.value,
                "data": error.data,
            },
        )
    if isinstance(error, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, ConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, ValidationError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error interno")
