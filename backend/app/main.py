from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.api.articulos import router as articulos_router
from backend.app.api.atributos import router as atributos_router
from backend.app.api.categorias import router as categorias_router
from backend.app.api.familias_atributos import router as familias_atributos_router
from backend.app.api.health import router as health_router
from backend.app.api.inventario import router as inventario_router
from backend.app.api.marcas import router as marcas_router
from backend.app.api.precios import router as precios_router
from backend.app.api.proveedores import router as proveedores_router
from backend.app.api.variantes import router as variantes_router
from backend.app.api.ventas import router as ventas_router
from backend.app.core.config import settings
from backend.app.core.logging import configure_logging, get_logger


configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting %s in %s mode", settings.app_name, settings.app_env)
    yield


app = FastAPI(title=settings.app_name, debug=settings.app_debug, lifespan=lifespan)
app.include_router(health_router)
app.include_router(categorias_router)
app.include_router(familias_atributos_router)
app.include_router(marcas_router)
app.include_router(atributos_router)
app.include_router(articulos_router)
app.include_router(variantes_router)
app.include_router(proveedores_router)
app.include_router(inventario_router)
app.include_router(ventas_router)
app.include_router(precios_router)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": settings.app_name, "status": "running"}
