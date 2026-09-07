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
from backend.app.repositories.proveedor_repository import ProveedorRepository
from backend.app.repositories.variante_repository import VarianteRepository

__all__ = [
    "ArticuloRepository",
    "AtributoRepository",
    "CategoriaRepository",
    "FamiliaAtributosRepository",
    "DestinoInventarioRepository",
    "MarcaRepository",
    "MovimientoStockRepository",
    "ProveedorRepository",
    "StockActualRepository",
    "VarianteRepository",
]
