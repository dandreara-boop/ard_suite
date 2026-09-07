# ARD Suite Backend

Infraestructura base del backend de ARD Suite con FastAPI, SQLAlchemy 2.x, MariaDB, Alembic y Pydantic Settings.

## 1. Crear y activar entorno virtual

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 2. Instalar dependencias

```powershell
pip install -r backend\requirements.txt
```

## 3. Configurar variables de entorno

```powershell
Copy-Item backend\.env.example .env
```

Editar `.env` con los datos reales de MariaDB. No subir `.env` a Git.

## 4. Preparar MariaDB

Crear la base y el usuario configurados en `.env`. Ejemplo:

```sql
CREATE DATABASE ard_suite_dev CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'ard_suite'@'%' IDENTIFIED BY 'change_me';
GRANT ALL PRIVILEGES ON ard_suite_dev.* TO 'ard_suite'@'%';
FLUSH PRIVILEGES;
```

## 5. Ejecutar Alembic

```powershell
alembic -c backend\alembic.ini current
```

Aplicar migraciones pendientes:

```powershell
alembic -c backend\alembic.ini upgrade head
```

Generar una migración futura:

```powershell
alembic -c backend\alembic.ini revision --autogenerate -m "mensaje"
```

## 6. Iniciar FastAPI

```powershell
python -m uvicorn backend.app.main:app --reload
```

## 7. Probar health check

Abrir:

```text
http://127.0.0.1:8000/health
```

Respuesta esperada con MariaDB disponible:

```json
{
  "status": "ok",
  "database": "connected",
  "app": "ARD Suite API"
}
```

## Tests

```powershell
python -m pytest
```

## Business Rules Architecture

Las reglas importantes del negocio viven fuera de routers y repositories. La secuencia esperada es:

```text
Service
↓
Policy
↓
Rules
↓
RuleResult
```

Los routers traducen errores de dominio a HTTP. Los services coordinan la operacion y preparan el contexto tipado que necesita la policy. Las rules son codigo Python explicito, sin efectos secundarios: reciben datos y devuelven un `RuleResult` con `status`, `code`, `message` y datos opcionales.

En creacion de variantes, `VarianteService` carga el articulo, normaliza el codigo de barras, obtiene valores y combinaciones existentes, y delega la decision a `VariantCreationPolicy`. La policy evalua atributos habilitados, un valor por atributo, combinacion duplicada y codigo de barras unico. Si todo esta permitido devuelve `VARIANT_CAN_BE_CREATED`; si no, el service no persiste y levanta `BusinessRuleViolation` con un codigo estable para API, frontend, logs y tests.

## Catálogo Comercial

Sprint 2 agrega las entidades base del catálogo:

- Artículos
- Categorías
- Marcas
- Atributos
- Valores de atributo
- Variantes
- Proveedores
- Relación artículo-proveedor

Endpoints disponibles:

```text
GET    /api/categorias
POST   /api/categorias
GET    /api/categorias/{categoria_id}
PUT    /api/categorias/{categoria_id}
PATCH  /api/categorias/{categoria_id}/activar
PATCH  /api/categorias/{categoria_id}/desactivar

GET    /api/marcas
POST   /api/marcas
GET    /api/marcas/{marca_id}
PUT    /api/marcas/{marca_id}
PATCH  /api/marcas/{marca_id}/activar
PATCH  /api/marcas/{marca_id}/desactivar

GET    /api/atributos
POST   /api/atributos
GET    /api/atributos/{atributo_id}
PUT    /api/atributos/{atributo_id}
PATCH  /api/atributos/{atributo_id}/activar
PATCH  /api/atributos/{atributo_id}/desactivar
GET    /api/atributos/valores
POST   /api/atributos/valores
PUT    /api/atributos/valores/{valor_id}

GET    /api/articulos
POST   /api/articulos
GET    /api/articulos/{articulo_id}
PUT    /api/articulos/{articulo_id}
PATCH  /api/articulos/{articulo_id}/activar
PATCH  /api/articulos/{articulo_id}/desactivar
GET    /api/articulos/{articulo_id}/atributos
POST   /api/articulos/{articulo_id}/atributos
DELETE /api/articulos/{articulo_id}/atributos/{atributo_id}
POST   /api/articulos/{articulo_id}/variantes/preview
POST   /api/articulos/{articulo_id}/variantes/generate

GET    /api/familias-atributos
POST   /api/familias-atributos
GET    /api/familias-atributos/{familia_id}
PUT    /api/familias-atributos/{familia_id}
PATCH  /api/familias-atributos/{familia_id}/activar
PATCH  /api/familias-atributos/{familia_id}/desactivar
GET    /api/familias-atributos/{familia_id}/atributos
POST   /api/familias-atributos/{familia_id}/atributos
DELETE /api/familias-atributos/{familia_id}/atributos/{atributo_id}

GET    /api/variantes
POST   /api/variantes
GET    /api/variantes/{variante_id}
PUT    /api/variantes/{variante_id}
PATCH  /api/variantes/{variante_id}/activar
PATCH  /api/variantes/{variante_id}/desactivar
GET    /api/variantes/{variante_id}/valores
GET    /api/variantes/barcode/{codigo_barra}

GET    /api/proveedores
POST   /api/proveedores
GET    /api/proveedores/{proveedor_id}
PUT    /api/proveedores/{proveedor_id}
PATCH  /api/proveedores/{proveedor_id}/activar
PATCH  /api/proveedores/{proveedor_id}/desactivar
POST   /api/proveedores/articulos
GET    /api/proveedores/articulos/{articulo_id}
```

Ejemplo básico desde PowerShell:

```powershell
$categoria = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/categorias -ContentType "application/json" -Body '{"nombre":"Remeras"}'
$talle = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/atributos -ContentType "application/json" -Body '{"nombre":"Talle","codigo":"TAL"}'
$color = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/atributos -ContentType "application/json" -Body '{"nombre":"Color","codigo":"COL"}'
$talle3 = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/atributos/valores -ContentType "application/json" -Body (@{ atributo_id = $talle.id; valor = "3"; codigo = "03" } | ConvertTo-Json)
$negro = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/atributos/valores -ContentType "application/json" -Body (@{ atributo_id = $color.id; valor = "Negro"; codigo = "01" } | ConvertTo-Json)
$articulo = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/articulos -ContentType "application/json" -Body (@{ codigo = "123456"; nombre = "Remera lisa"; categoria_id = $categoria.id } | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/articulos/$($articulo.id)/atributos" -ContentType "application/json" -Body (@{ atributo_id = $talle.id } | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/articulos/$($articulo.id)/atributos" -ContentType "application/json" -Body (@{ atributo_id = $color.id } | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/variantes -ContentType "application/json" -Body (@{ articulo_id = $articulo.id; codigo_barra = "1234560301"; valor_atributo_ids = @($talle3.id, $negro.id) } | ConvertTo-Json)
```

## Familias y Asistente de Variantes

Crear una familia y usarla como plantilla:

```powershell
$familia = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/familias-atributos -ContentType "application/json" -Body '{"nombre":"Textil","descripcion":"Talle y color"}'
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/familias-atributos/$($familia.id)/atributos" -ContentType "application/json" -Body (@{ atributo_id = $talle.id; orden = 1 } | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/familias-atributos/$($familia.id)/atributos" -ContentType "application/json" -Body (@{ atributo_id = $color.id; orden = 2 } | ConvertTo-Json)
```

Vista previa de combinaciones sin guardar:

```powershell
$previewBody = @{
  atributos = @(
    @{ atributo_id = $talle.id; valor_atributo_ids = @($talle1.id, $talle2.id) },
    @{ atributo_id = $color.id; valor_atributo_ids = @($blanco.id, $negro.id) }
  )
} | ConvertTo-Json -Depth 4

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/articulos/$($articulo.id)/variantes/preview" -ContentType "application/json" -Body $previewBody
```

Generar solamente las combinaciones confirmadas:

```powershell
$generateBody = @{
  combinaciones = @(
    @{ valor_atributo_ids = @($talle1.id, $blanco.id) },
    @{ valor_atributo_ids = @($talle2.id, $negro.id); codigo_barra = "1234560202" }
  )
} | ConvertTo-Json -Depth 4

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/articulos/$($articulo.id)/variantes/generate" -ContentType "application/json" -Body $generateBody
```

La propuesta inicial de código de barras usa:

```text
codigo_articulo + codigos_de_valores_en_orden_de_atributos_del_articulo
```

## Motor de Inventario

Sprint 5 agrega el motor de inventario sin implementar venta, recepcion, logistica, sincronizacion ni workers reales. El principio central es:

```text
MovimientoStock
↓
StockActual
```

`MovimientoStock` es el libro historico: cada entrada, salida, merma, transferencia futura o ajuste queda trazado con `global_id`, variante, destino, estado, tipo, origen simple (`origen_tipo`/`origen_id`), referencia, observacion y saldo resultante. El stock no se modifica directamente.

`StockActual` es el saldo materializado para consulta rapida por `variante_id + destino_id + estado`. Se actualiza incrementalmente dentro de la misma transaccion que crea el movimiento; no se recalcula el historial en cada consulta.

Los destinos de inventario usan `DestinoInventario` y no asumen sucursal: los tipos iniciales son `LOCAL`, `WEB`, `DEPOSITO` y `OTRO`. Los estados de stock son `DISPONIBLE`, `RESERVADO`, `EN_TRANSITO` y `NO_DISPONIBLE`.

### Idempotencia

Cada movimiento tiene un `global_id` unico. Si llega dos veces el mismo evento, `InventoryService` devuelve un resultado estable con `INVENTORY_EVENT_ALREADY_PROCESSED`, no crea otro `MovimientoStock` y no vuelve a tocar `StockActual`. Esto deja preparado el camino para sincronizacion futura.

### Stock negativo

El motor permite saldos negativos por defecto. Un movimiento que lleva `StockActual.cantidad` a `-1` es valido y queda trazado. La excepcion administrativa futura puede construirse sobre el resultado/evento, pero no se implementa en este sprint.

### Ajustes

Los ajustes no hacen `stock = nuevo_valor`. `POST /api/inventario/ajustes` calcula la diferencia entre `stock_teorico` y `stock_contado` y genera un movimiento:

- `AJUSTE_NEGATIVO` cuando la diferencia es menor a cero.
- `AJUSTE_POSITIVO` cuando la diferencia es mayor a cero.

La observacion guarda motivo, saldo anterior, saldo resultante y texto opcional.

### Domain Events

`backend/app/events/` define un `DomainEvent` minimo y `InventoryMovementRequested`. No hay bus, Redis, RabbitMQ, Kafka ni worker. La interfaz permite que, en el futuro, Venta persista venta/items/pagos/evento pendiente en una transaccion rapida y que inventario procese luego el evento fuera del camino critico del POS.

### Concurrencia

Para MySQL/MariaDB, `InventoryService` actualiza el saldo con `INSERT ... ON DUPLICATE KEY UPDATE cantidad = cantidad + delta` apoyado por la restriccion unica de `StockActual`. En SQLite se usa `ON CONFLICT DO UPDATE` para tests. Esto evita el patron inseguro de leer cantidad, modificarla en Python y guardar. El movimiento y el saldo se confirman en la misma transaccion; si falla cualquiera de las dos operaciones, se hace rollback completo.

### API tecnica temporal

Endpoints disponibles para QA y administracion futura:

```text
POST /api/inventario/destinos
GET  /api/inventario/destinos
POST /api/inventario/movimientos
GET  /api/inventario/stock
GET  /api/inventario/stock/{variante_id}
GET  /api/inventario/movimientos
POST /api/inventario/ajustes
```

Filtros de stock: `variante_id`, `destino_id`, `estado`.

Filtros de historial: `variante_id`, `destino_id`, `tipo`, `fecha_desde`, `fecha_hasta`.

Existe `InventoryService.rebuild_stock_actual()` como funcion interna preparada para reconstruccion desde historial. No se expone como operacion normal ni se ejecuta automaticamente.
