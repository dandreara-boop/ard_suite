from backend.app.repositories.articulo_repository import ArticuloRepository
from backend.app.repositories.atributo_repository import AtributoRepository
from backend.app.repositories.categoria_repository import CategoriaRepository
from backend.app.repositories.familia_atributos_repository import FamiliaAtributosRepository
from backend.app.repositories.inventory_repository import (
    DestinoInventarioRepository,
    MovimientoStockRepository,
    StockActualRepository,
)
from backend.app.repositories.marca_repository import MarcaRepository
from backend.app.repositories.pricing_repository import (
    AuditoriaPrecioArticuloRepository,
    CondicionComercialPrecioRepository,
    PrecioArticuloRepository,
)
from backend.app.repositories.proveedor_repository import ProveedorRepository
from backend.app.repositories.variante_repository import VarianteRepository
from backend.app.repositories.venta_repository import (
    DetalleVentaRepository,
    EventoPendienteRepository,
    PagoVentaRepository,
    VentaRepository,
)

__all__ = [
    "ArticuloRepository",
    "AuditoriaPrecioArticuloRepository",
    "AtributoRepository",
    "CategoriaRepository",
    "CondicionComercialPrecioRepository",
    "DetalleVentaRepository",
    "EventoPendienteRepository",
    "FamiliaAtributosRepository",
    "DestinoInventarioRepository",
    "MarcaRepository",
    "MovimientoStockRepository",
    "PagoVentaRepository",
    "PrecioArticuloRepository",
    "ProveedorRepository",
    "StockActualRepository",
    "VarianteRepository",
    "VentaRepository",
]
