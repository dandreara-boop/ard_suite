"""Backend-only contract. No authentication middleware on commercial routes."""
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.schemas.autenticacion import (
    ConfirmacionResponse, ConfirmarRequest, CredencialesResponse, DesbloquearRequest,
    LoginRequest, RenovarRequest, SesionResponse,
)
from backend.app.services.autenticacion_service import AutenticacionService, ErrorAutenticacion
from backend.app.services.seguridad_service import ContextoSeguridad


class RutaAutenticacion(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request):
            try:
                response = await handler(request)
            except RequestValidationError:
                # FastAPI's default validation response may include raw inputs.
                raise HTTPException(422, "Solicitud de autenticacion invalida",
                                    headers={"Cache-Control": "no-store"}) from None
            except ErrorAutenticacion as error:
                messages = {"SESION_BLOQUEADA": "Sesion bloqueada; se requiere contrasena",
                            "LIMITE_ORIGEN": "Demasiados intentos; reintentar mas tarde"}
                raise HTTPException(error.status, messages.get(error.code, "Credenciales invalidas"),
                                    headers={"Cache-Control": "no-store"}) from None
            except HTTPException:
                raise
            except Exception:
                # Never expose SQL, credential digests, request data or raw exceptions.
                raise HTTPException(503, "Autenticacion temporalmente no disponible",
                                    headers={"Cache-Control": "no-store"}) from None
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            return response

        return safe_handler


router = APIRouter(prefix="/api/auth", tags=["autenticacion"], route_class=RutaAutenticacion)


def servicio(request: Request, db: Annotated[Session, Depends(get_db)]):
    # Ignore forwarded headers and all client-supplied installation identifiers.
    origin = request.client.host if request.client else "sin-origen"
    return AutenticacionService(db, origen=origin, contexto=ContextoSeguridad(
        sucursal=settings.auth_sucursal, puesto=settings.auth_puesto,
    ))


Servicio = Annotated[AutenticacionService, Depends(servicio)]


def token_acceso(authorization: Annotated[str | None, Header()] = None):
    if authorization is None:
        raise ErrorAutenticacion()
    kind, separator, token = authorization.partition(" ")
    if not separator or kind.lower() != "bearer" or not 40 <= len(token) <= 128:
        raise ErrorAutenticacion()
    return token


Token = Annotated[str, Depends(token_acceso)]


def sesion_actual(auth: Servicio, token: Token):
    """Reusable only by opt-in routes; C3 will protect commercial routes later."""
    return auth.actual(token)


@router.post("/login", response_model=CredencialesResponse, status_code=201)
def login(data: LoginRequest, auth: Servicio):
    return auth.login(data.nombre_usuario, data.password.get_secret_value())


@router.get("/sesion", response_model=SesionResponse)
def actual(session: Annotated[dict, Depends(sesion_actual)]):
    return session


@router.post("/actividad", response_model=SesionResponse)
def actividad(auth: Servicio, token: Token):
    return auth.actual(token, actividad=True)


@router.post("/logout", status_code=204)
def logout(data: RenovarRequest, auth: Servicio):
    auth.logout(data.token_renovacion.get_secret_value())
    return Response(status_code=204)


@router.post("/renovar", response_model=CredencialesResponse)
def renovar(data: RenovarRequest, auth: Servicio):
    return auth.preparar(data.token_renovacion.get_secret_value())


@router.post("/renovar/confirmar", response_model=ConfirmacionResponse)
def confirmar(data: ConfirmarRequest, auth: Servicio):
    # Keep locators out of URLs/access logs.
    return auth.confirmar(str(data.rotacion_id), data.token_renovacion.get_secret_value())


@router.post("/desbloquear", response_model=CredencialesResponse)
def desbloquear(data: DesbloquearRequest, auth: Servicio):
    return auth.desbloquear(str(data.sesion_id), data.nombre_usuario, data.password.get_secret_value())
