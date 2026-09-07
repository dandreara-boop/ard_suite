from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CategoriaBase(BaseModel):
    nombre: str
    parent_id: int | None = None
    activo: bool = True


class CategoriaCreate(CategoriaBase):
    pass


class CategoriaUpdate(BaseModel):
    nombre: str | None = None
    parent_id: int | None = None
    activo: bool | None = None


class CategoriaRead(CategoriaBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class MarcaBase(BaseModel):
    nombre: str
    activo: bool = True


class MarcaCreate(MarcaBase):
    pass


class MarcaUpdate(BaseModel):
    nombre: str | None = None
    activo: bool | None = None


class MarcaRead(MarcaBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class AtributoBase(BaseModel):
    nombre: str
    codigo: str | None = None
    activo: bool = True
    orden: int = 0


class AtributoCreate(AtributoBase):
    pass


class AtributoUpdate(BaseModel):
    nombre: str | None = None
    codigo: str | None = None
    activo: bool | None = None
    orden: int | None = None


class AtributoRead(AtributoBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class ValorAtributoBase(BaseModel):
    atributo_id: int
    valor: str
    codigo: str | None = None
    activo: bool = True
    orden: int = 0


class ValorAtributoCreate(ValorAtributoBase):
    pass


class ValorAtributoUpdate(BaseModel):
    valor: str | None = None
    codigo: str | None = None
    activo: bool | None = None
    orden: int | None = None


class ValorAtributoRead(ValorAtributoBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class ArticuloBase(BaseModel):
    codigo: str
    nombre: str
    descripcion: str | None = None
    categoria_id: int
    marca_id: int | None = None
    familia_atributos_id: int | None = None
    activo: bool = True


class ArticuloCreate(ArticuloBase):
    pass


class ArticuloUpdate(BaseModel):
    codigo: str | None = None
    nombre: str | None = None
    descripcion: str | None = None
    categoria_id: int | None = None
    marca_id: int | None = None
    familia_atributos_id: int | None = None
    activo: bool | None = None


class ArticuloRead(ArticuloBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class ArticuloAtributoCreate(BaseModel):
    atributo_id: int
    orden: int = 0
    obligatorio: bool = True


class ArticuloAtributoRead(ORMModel):
    id: int
    articulo_id: int
    atributo_id: int
    orden: int
    obligatorio: bool
    created_at: datetime
    updated_at: datetime


class FamiliaAtributosBase(BaseModel):
    nombre: str
    descripcion: str | None = None
    activo: bool = True


class FamiliaAtributosCreate(FamiliaAtributosBase):
    pass


class FamiliaAtributosUpdate(BaseModel):
    nombre: str | None = None
    descripcion: str | None = None
    activo: bool | None = None


class FamiliaAtributosRead(FamiliaAtributosBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class FamiliaAtributoCreate(BaseModel):
    atributo_id: int
    orden: int = 0
    obligatorio: bool = True


class FamiliaAtributoRead(ORMModel):
    id: int
    familia_id: int
    atributo_id: int
    orden: int
    obligatorio: bool
    created_at: datetime
    updated_at: datetime


class VarianteBase(BaseModel):
    articulo_id: int
    codigo_barra: str
    activo: bool = True


class VarianteCreate(VarianteBase):
    valor_atributo_ids: list[int] = Field(default_factory=list)


class VarianteUpdate(BaseModel):
    codigo_barra: str | None = None
    activo: bool | None = None


class VarianteValorAtributoRead(ORMModel):
    id: int
    variante_id: int
    valor_atributo_id: int
    atributo_id: int
    created_at: datetime
    updated_at: datetime


class VarianteRead(VarianteBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class VarianteDetailRead(VarianteRead):
    articulo: ArticuloRead
    valores: list[VarianteValorAtributoRead] = Field(default_factory=list)


class VariantePreviewSelection(BaseModel):
    atributo_id: int
    valor_atributo_ids: list[int]


class VariantePreviewRequest(BaseModel):
    atributos: list[VariantePreviewSelection] = Field(default_factory=list)


class VariantePreviewValueRead(BaseModel):
    atributo_id: int
    atributo_nombre: str
    valor_atributo_id: int
    valor: str
    codigo: str | None = None


class VariantePreviewItemRead(BaseModel):
    valores: list[VariantePreviewValueRead]
    valor_atributo_ids: list[int]
    codigo_barra_propuesto: str
    existe_variante: bool
    existe_codigo_barra: bool
    estado: str


class VarianteGenerateCombination(BaseModel):
    valor_atributo_ids: list[int] = Field(default_factory=list)
    codigo_barra: str | None = None


class VarianteGenerateRequest(BaseModel):
    combinaciones: list[VarianteGenerateCombination]


class ProveedorBase(BaseModel):
    codigo: str
    nombre: str
    razon_social: str | None = None
    cuit: str | None = None
    telefono: str | None = None
    email: str | None = None
    activo: bool = True


class ProveedorCreate(ProveedorBase):
    pass


class ProveedorUpdate(BaseModel):
    codigo: str | None = None
    nombre: str | None = None
    razon_social: str | None = None
    cuit: str | None = None
    telefono: str | None = None
    email: str | None = None
    activo: bool | None = None


class ProveedorRead(ProveedorBase, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime


class ArticuloProveedorCreate(BaseModel):
    articulo_id: int
    proveedor_id: int
    codigo_proveedor: str | None = None
    proveedor_preferido: bool = False
    activo: bool = True


class ArticuloProveedorRead(ArticuloProveedorCreate, ORMModel):
    id: int
    created_at: datetime
    updated_at: datetime
