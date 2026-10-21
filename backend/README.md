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

## Sprint 9.5C-A — Solicitud de correccion de arqueo

`POST /api/cajas/arqueos/{arqueo_id}/solicitar-correccion` recibe `usuario_id`,
`importe_corregido` (decimal no negativo, hasta Numeric(12,2)) y `motivo`
obligatorio. Devuelve HTTP 201 con `solicitud_id`, `arqueo_id`, `estado`
PENDIENTE, `conteo_original`, `conteo_corregido_propuesto` y `solicitada_at`.
Solo el responsable de una sesion CERRADA puede solicitar sobre su arqueo
CIERRE. Se conserva integro el arqueo y la sesion; no se generan movimientos,
pagos ni cambios de ventas. La resolucion administrativa se describe en 9.5C-B.

Se permite una sola solicitud por arqueo incluso si fue rechazada. Solicitud
y apertura bloquean la misma fila Caja hasta commit/rollback. Las lecturas
posteriores del plazo son bloqueantes para ver datos actuales bajo MariaDB
REPEATABLE READ. El orden es Caja, SesionCaja, ArqueoCaja, solicitud; los
movimientos de caja tambien toman Caja antes de SesionCaja y evitan joins
bloqueantes. Cierre, captura y confirmacion bloquean sesion sin adquirir luego
un lock exclusivo de Caja. El pendiente no impide abrir otro turno.

El plazo vence con cualquier sesion de la misma Caja cuyo `id` sea mayor al
de la sesion original, ABIERTA o CERRADA. Esto depende de IDs autoincrementales
locales y de crear sesiones mediante `abrir_sesion()` bajo el lock de Caja.
No eliminar/renumerar ese historial ni importar IDs manuales; una futura
sincronizacion debe revisar este criterio. Las fechas pueden empatar y los
UUID no ordenan turnos. `usuario_id` sigue siendo declarado por el cliente:
la comparacion con el responsable no sustituye autenticacion.

La revision `20261016_0011` desciende de `20261007_0010` y agrega
`motivo TEXT NOT NULL` y `UNIQUE(arqueo_id)`.
Requiere ventana de mantenimiento sin escritores de solicitudes. Antes de
cualquier DDL cuenta historicos y duplicados y se detiene si encuentra
cualquiera: no inventa motivos, no rellena valores vacios ni borra registros.
Si hay historicos, su tratamiento requiere una estrategia explicita antes
de desplegar. Ambas modificaciones usan un solo ALTER TABLE porque el DDL
MariaDB no es transaccional; no se admite modo offline sin prevalidacion.
El downgrade tambien se detiene si eliminaria motivos de solicitudes.

Las pruebas funcionales usan bases SQLite aisladas creadas desde metadata.
Las pruebas MariaDB de concurrencia requieren seleccion explicita de la base
dedicada `ard_suite_test` mediante `ARD_TEST_DB_*`, como se detalla mas abajo.
No usan la conexion de desarrollo ni crean o eliminan bases de datos. Estas
pruebas no aplican migraciones: la revision se verifica con operaciones
simuladas y no se certifica el estado del esquema de cada entorno operativo.

## Sprint 9.5C-B — Resolucion administrativa de correcciones

`POST /api/cajas/solicitudes-correccion/{solicitud_id}/resolver` recibe
`supervisor_id` (entero positivo, hasta 2147483647), `decision` (`APROBADA` o
`RECHAZADA`) y `observacion` obligatoria, normalizada sin espacios externos.
Solo resuelve solicitudes PENDIENTES, una unica vez. Registra estado,
supervisor, fecha de resolucion y observacion. La fecha sigue la convencion
de fecha local del backend, con precision de segundos compatible con el
DATETIME existente en MariaDB.

Devuelve HTTP 200 con `solicitud_id`, `arqueo_id`, `estado`, `supervisor_id`,
`resuelta_at`, `observacion`, `conteo_original`, `conteo_corregido_propuesto`,
`importe_reconocido`, `diferencia_original` y `diferencia_corregida`. Los importes
se serializan como decimales con dos posiciones; la diferencia corregida es
`null` cuando se rechaza. Solicitud inexistente: 404; ya resuelta o historial
incompleto/inconsistente: 409; entrada invalida: 422.

Al aprobar, el importe reconocido es el conteo corregido propuesto y la
diferencia corregida es `conteo_corregido_propuesto - arqueo.efectivo_esperado`.
Se admite diferencia residual positiva, negativa o cero. Al rechazar, el
importe reconocido sigue siendo `arqueo.primer_conteo`. En ambos casos se
conserva y devuelve la diferencia original. Aprobar reconoce un conteo:
no justifica faltantes ni atribuye responsabilidad al cajero.

Se utiliza exclusivamente el efectivo esperado historico del arqueo, sin
recalcular ventas, pagos o movimientos actuales. Si faltan importes historicos,
el conteo original no coincide o la diferencia original es inconsistente,
la resolucion se rechaza y la solicitud permanece pendiente. No se modifica
ArqueoCaja (tampoco su estado), SesionCaja, ventas ni pagos; no se generan
movimientos automaticos ni se reabre una sesion. Los campos existentes cubren
el alcance: este sprint no agrega migraciones ni columnas derivadas.

La transaccion MariaDB toma `SELECT FOR UPDATE` exclusivamente sobre la fila
de la solicitud y refresca su identidad ORM despues de adquirir el bloqueo.
Lee el arqueo sin bloqueo, actualiza solo los cuatro campos de resolucion y
ejecuta un unico commit; ante errores hace rollback. Un segundo supervisor
encuentra el estado final y recibe conflicto. No toma bloqueos sobre Caja,
SesionCaja ni ArqueoCaja, ni invierte el orden de bloqueos de la solicitud
original. SQLite utiliza el `BEGIN IMMEDIATE` existente: su bloqueo de escritura
es mas amplio y sus pruebas no sustituyen concurrencia real MariaDB.

### Recuperacion de conflictos de snapshot en MariaDB

Con `REPEATABLE READ` e `innodb_snapshot_isolation=ON` (valor predeterminado
desde MariaDB 11.6.2), una fila modificada o insertada por otra transaccion
puede quedar fuera de la vista de lectura previa. Al intentar bloquearla,
MariaDB puede devolver 1020 (`ER_CHECKREAD`). Esto afecta tanto la deteccion
de solicitudes/sesiones nuevas como la resolucion de solicitudes que otro
supervisor ya modifico. `populate_existing` refresca la identidad ORM, pero
no renueva el snapshot del servidor. Ver la documentacion de
[innodb_snapshot_isolation](https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-system-variables#innodb_snapshot_isolation).

Apertura, creacion de solicitud y resolucion comparten un limite de tres
intentos totales. Solo reintentan `OperationalError` de los dialectos
MySQL/MariaDB con codigo numerico 1020, 1205 (lock timeout) o 1213 (deadlock),
sin conexion invalidada y antes de intentar el commit. Siempre ejecutan
rollback completo antes de repetir: la siguiente consulta inicia una nueva
transaccion, vuelve a adquirir los bloqueos y valida otra vez el negocio.
No se repite un SELECT aislado dentro del snapshot fallido ni se usan savepoints.

Se conserva `SELECT FOR UPDATE` y el orden Caja -> Sesion -> Arqueo -> solicitud
en la creacion; la resolucion bloquea solo la solicitud y lee el arqueo sin
bloquear sus padres. Las lecturas ORM bloqueantes refrescan las identidades.
Las operaciones repetibles solo preparan cambios transaccionales, sin efectos
externos. La respuesta se prepara antes del unico commit; en apertura tampoco
hay una lectura posterior al commit que pueda disparar la repeticion.

Si la nueva lectura confirma solicitud duplicada, resolucion previa o plazo
vencido, devuelve `ConflictError` / HTTP 409. El 1020 por si solo no equivale
a un conflicto de negocio: si persiste tras tres intentos, sigue siendo error
tecnico. Los errores SQL restantes se propagan sin reintento ni traduccion a
409. Un error durante el commit tampoco se reintenta automaticamente, para
evitar duplicaciones cuando no puede determinarse si llego a confirmarse.
El consumidor debe verificar el resultado antes de repetir una operacion
cuyo commit haya tenido un desenlace incierto.

No se cambia el aislamiento ni `innodb_snapshot_isolation`, no se alteran
importes historicos y no se generan movimientos automaticos. Las pruebas de
recuperacion inyectan errores del driver sobre SQLite y verifican rollback,
transacciones distintas, limites y relectura del ganador. No reemplazan los
siete escenarios reales MariaDB, que conservan sus snapshots previos.

La apertura de otro turno vence el plazo para CREAR una solicitud, pero no
impide RESOLVER una ya registrada. Las solicitudes pendientes, aprobadas o
rechazadas no condicionan aperturas, movimientos, ventas ni cobros.

### Acceso restringido a desarrollo y pruebas

No existe autenticacion ni autorizacion de supervisores en este backend.
`supervisor_id` es declarado por el cliente: validar su formato no acredita
identidad, existencia ni permisos. Este endpoint **no debe habilitarse para
usuarios operativos sin autorizacion confiable del lado servidor**. Antes de
produccion se debe integrar esa autorizacion; este sprint no la implementa.

El endpoint permanece deshabilitado por defecto (HTTP 404, antes de acceder
a la base). Solo admite habilitacion explicita con
`CAJA_CORRECCIONES_RESOLUCION_DEV_ENABLED=true` y `APP_ENV` igual a `development`,
`test` o `testing`. En otros entornos permanece cerrado incluso con el flag
activo. El flag no es un permiso ni una credencial y no sustituye autorizacion.
No se activa automaticamente ni afecta los demas endpoints. Las pruebas lo
habilitan temporalmente en memoria; no cambian `.env`.

Las pruebas de 9.5C-B reutilizan las bases SQLite aisladas y la fixture MariaDB
protegida de 9.5C-A. Cubren importes, auditoria, rollback, inmutabilidad,
continuidad comercial y contrato HTTP, ademas de carreras entre supervisores
con identidades precargadas. Una prueba exclusiva MariaDB comprueba operacion
comercial mientras la resolucion mantiene su bloqueo. Sin seleccion explicita
de MariaDB se omiten esos casos; con configuracion parcial/insegura o errores
de conexion/permisos fallan, sin recurrir a otra base.

### MariaDB real para las siete pruebas de concurrencia

`backend/tests/mariadb_test_support.py` configura una conexion independiente
`mysql+pymysql`, con aislamiento `REPEATABLE READ`, a `127.0.0.1:3307` y
exclusivamente `ard_suite_test`. No lee `.env`, `DATABASE_URL`, `DB_*` ni
`settings.sqlalchemy_database_uri` para seleccionar la conexion de pruebas.
La aplicacion conserva su configuracion habitual; la fixture la sustituye
por sesiones vinculadas al engine de pruebas.

Se requieren todas estas variables del proceso, sin guardarlas en el repositorio:

| Variable | Valor requerido |
| --- | --- |
| `ARD_TEST_DB_HOST` | `127.0.0.1` |
| `ARD_TEST_DB_PORT` | `3307` |
| `ARD_TEST_DB_NAME` | `ard_suite_test` (coincidencia exacta) |
| `ARD_TEST_DB_USER` | Usuario con privilegios sobre esa base |
| `ARD_TEST_DB_PASSWORD` | Password explicito del usuario; no se hereda de desarrollo |
| `ARD_TEST_DB_RESET` | `1`: autoriza crear y eliminar las tablas propias de las pruebas |

La base debe existir y estar vacia al comenzar cada caso. La fixture rechaza
tablas o vistas preexistentes, incluidos restos de una ejecucion interrumpida;
no intenta eliminarlos automaticamente. Cada caso crea las tablas de metadata
y elimina solo esas tablas al finalizar, tambien ante errores. No ejecuta
Alembic, `CREATE DATABASE` ni `DROP DATABASE`; los privilegios sobre
`ard_suite_test` son suficientes, sin permisos globales para crear bases.

Antes del DDL y en cada conexion entregada a un worker valida `SELECT DATABASE(),
@@port, VERSION()`: exige la base exacta, puerto 3307 y servidor MariaDB. Vuelve
a validar antes de limpiar y rechaza metadata con esquemas explicitos o claves
foraneas entre bases. Un `GET_LOCK` exclusivo durante toda la fixture impide
dos ejecuciones simultaneas sobre este esquema; una segunda ejecucion falla
en lugar de borrar tablas ajenas. Reservar `ard_suite_test` exclusivamente
para estas pruebas, sin otros procesos que escriban en ella.

El driver PyMySQL y el dialecto SQLAlchemy existentes utilizan el puerto
configurado y SQL MariaDB (`FOR UPDATE`, `GET_LOCK`, `REPEATABLE READ`). Las
validaciones simuladas cubren una identidad `12.2.2-MariaDB`; la compatibilidad
y concurrencia reales requieren ejecutar expresamente los siete casos.

Desde la raiz del repositorio, este bloque PowerShell prepara variables solo
para el proceso, solicita credenciales sin imprimirlas y ejecuta exactamente
los siete casos MariaDB. **Crea y limpia tablas en la base de pruebas**; usarlo
solo al autorizar su ejecucion. No es necesario habilitar el endpoint HTTP.

```powershell
$testCredential = Get-Credential -UserName 'ard_suite' -Message 'MariaDB: base ard_suite_test'
try {
    $env:ARD_TEST_DB_HOST = '127.0.0.1'
    $env:ARD_TEST_DB_PORT = '3307'
    $env:ARD_TEST_DB_NAME = 'ard_suite_test'
    $env:ARD_TEST_DB_USER = $testCredential.UserName
    $env:ARD_TEST_DB_PASSWORD = $testCredential.GetNetworkCredential().Password
    $env:ARD_TEST_DB_RESET = '1'
    .\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider backend/tests/test_sprint_9_5c_a_solicitud_correccion.py backend/tests/test_sprint_9_5c_b_resolucion_correccion.py -k mariadb -q -rs
} finally {
    'ARD_TEST_DB_HOST', 'ARD_TEST_DB_PORT', 'ARD_TEST_DB_NAME', 'ARD_TEST_DB_USER', 'ARD_TEST_DB_PASSWORD', 'ARD_TEST_DB_RESET' | ForEach-Object {
        Remove-Item -LiteralPath "Env:$_" -ErrorAction SilentlyContinue
    }
    $testCredential = $null
}
```

Para listar esos casos sin abrir conexiones ni crear tablas, ejecutar el mismo
comando Python con `--collect-only`. Las protecciones se verifican sin MariaDB:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider backend/tests/test_mariadb_test_safety.py -q
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
