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

Para validacion local sin MariaDB, excluir los casos reales y ejecutar las
protecciones simuladas por separado (sin configurar `ARD_TEST_DB_*`):

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider backend/tests --ignore=backend/tests/test_sprint_9_5c_c2_mariadb.py -k 'not mariadb' -q
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider backend/tests/test_mariadb_test_safety.py -q
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
dedicada `ard_suite_pytest` mediante `ARD_TEST_DB_*`, como se detalla mas abajo.
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

Las bases tienen usos separados y no son intercambiables:

| Base | Uso |
| --- | --- |
| `ard_suite_dev` | Desarrollo; la fixture de integracion la rechaza |
| `ard_suite_test` | Validacion de migraciones; conservar su esquema y datos; la fixture la rechaza |
| `ard_suite_pytest` | Integracion automatizada; debe estar vacia al comenzar cada caso |

`backend/tests/mariadb_test_support.py` configura una conexion independiente
`mysql+pymysql`, con aislamiento `REPEATABLE READ`, a `127.0.0.1:3307` y
exclusivamente `ard_suite_pytest`. No lee `.env`, `DATABASE_URL`, `DB_*` ni
`settings.sqlalchemy_database_uri` para seleccionar la conexion de pruebas.
La aplicacion conserva su configuracion habitual; la fixture la sustituye
por sesiones vinculadas al engine de pruebas.

Se requieren todas estas variables del proceso, sin guardarlas en el repositorio:

| Variable | Valor requerido |
| --- | --- |
| `ARD_TEST_DB_HOST` | `127.0.0.1` |
| `ARD_TEST_DB_PORT` | `3307` |
| `ARD_TEST_DB_NAME` | `ard_suite_pytest` (coincidencia exacta) |
| `ARD_TEST_DB_USER` | Usuario con privilegios sobre esa base |
| `ARD_TEST_DB_PASSWORD` | Password explicito del usuario; no se hereda de desarrollo |
| `ARD_TEST_DB_RESET` | `1`: autoriza crear y eliminar las tablas propias de las pruebas |

La base debe existir y estar vacia al comenzar cada caso. La fixture rechaza
tablas o vistas preexistentes, incluidos restos de una ejecucion interrumpida;
no intenta eliminarlos automaticamente. Cada caso crea las tablas de metadata
y elimina solo esas tablas al finalizar, tambien ante errores. No ejecuta
Alembic, `CREATE DATABASE` ni `DROP DATABASE`; los privilegios sobre
`ard_suite_pytest` son suficientes, sin permisos globales para crear bases.

Crear la base manualmente con una cuenta administradora, fuera de pytest,
solo despues de comprobar el servidor y que el nombre no este ocupado.
No recrear una base existente ni usar creacion condicional para omitir esa
revision. Usar una cuenta dedicada con permisos solo sobre `ard_suite_pytest`,
sin acceso a desarrollo ni a la base de migraciones. No aplicar Alembic a esta
base: la fixture crea las estructuras desde metadata.

El DDL de MariaDB puede confirmarse automaticamente. Una interrupcion puede
dejar estructuras residuales que un rollback no revierte. La siguiente
ejecucion las rechazara; inspeccionarlas manualmente antes de continuar,
sin limpieza indiscriminada ni reintentos automaticos.

Antes del DDL y en cada conexion entregada a un worker valida `SELECT DATABASE(),
@@port, VERSION()`: exige la base exacta, puerto 3307 y servidor MariaDB. Vuelve
a validar antes de limpiar y rechaza metadata con esquemas explicitos o claves
foraneas entre bases. Un `GET_LOCK` exclusivo durante toda la fixture impide
dos ejecuciones simultaneas sobre este esquema; una segunda ejecucion falla
en lugar de borrar tablas ajenas. Reservar `ard_suite_pytest` exclusivamente
para estas pruebas, sin otros procesos que escriban en ella.

El driver PyMySQL y el dialecto SQLAlchemy existentes utilizan el puerto
configurado y SQL MariaDB (`FOR UPDATE`, `GET_LOCK`, `REPEATABLE READ`). Las
validaciones simuladas cubren una identidad `12.2.2-MariaDB`; la compatibilidad
y concurrencia reales requieren ejecutar expresamente los siete casos.

Desde la raiz del repositorio, este bloque PowerShell prepara variables solo
para el proceso hijo, solicita credenciales sin imprimirlas y ejecuta exactamente
los siete casos MariaDB. **Crea y limpia tablas en la base de pruebas**; usarlo
solo al autorizar su ejecucion. No es necesario habilitar el endpoint HTTP.

```powershell
$testCredential = $null
$testStartInfo = $null
$testProcess = $null
try {
    $testCredential = Get-Credential -Message 'MariaDB: base exclusiva ard_suite_pytest'
    if ($null -eq $testCredential) {
        throw 'Solicitud de credenciales cancelada.'
    }
    $testStartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $testStartInfo.FileName = (Resolve-Path -LiteralPath '.\.venv\Scripts\python.exe').Path
    $testStartInfo.WorkingDirectory = (Get-Location).Path
    $testStartInfo.Arguments = '-B -m pytest -p no:cacheprovider backend/tests/test_sprint_9_5c_a_solicitud_correccion.py backend/tests/test_sprint_9_5c_b_resolucion_correccion.py -k mariadb -q -rs'
    $testStartInfo.UseShellExecute = $false
    $testStartInfo.EnvironmentVariables['ARD_TEST_DB_HOST'] = '127.0.0.1'
    $testStartInfo.EnvironmentVariables['ARD_TEST_DB_PORT'] = '3307'
    $testStartInfo.EnvironmentVariables['ARD_TEST_DB_NAME'] = 'ard_suite_pytest'
    $testStartInfo.EnvironmentVariables['ARD_TEST_DB_USER'] = $testCredential.UserName
    $testStartInfo.EnvironmentVariables['ARD_TEST_DB_PASSWORD'] = $testCredential.GetNetworkCredential().Password
    $testStartInfo.EnvironmentVariables['ARD_TEST_DB_RESET'] = '1'
    $testProcess = New-Object System.Diagnostics.Process
    $testProcess.StartInfo = $testStartInfo
    if (-not $testProcess.Start()) {
        throw 'No se pudo iniciar pytest.'
    }
    $testProcess.WaitForExit()
    if ($testProcess.ExitCode -ne 0) {
        throw 'pytest fallo. Revisar la salida y cualquier estructura residual antes de continuar.'
    }
} finally {
    if ($null -ne $testStartInfo) {
        $testStartInfo.EnvironmentVariables.Remove('ARD_TEST_DB_PASSWORD')
        $testStartInfo.EnvironmentVariables.Remove('ARD_TEST_DB_USER')
    }
    if ($null -ne $testProcess) {
        $testProcess.Dispose()
    }
    $testCredential = $null
}
```

Para listar esos casos sin abrir conexiones ni crear tablas, ejecutar el mismo
comando Python con `--collect-only`. Las protecciones se verifican sin MariaDB:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider backend/tests/test_mariadb_test_safety.py -q
```

## Sprint 9.5C-C1 — Base de identidad y primer administrador

Esta etapa implementa DEC-211, DEC-212 y DEC-213 como base interna: usuarios
individuales, roles, hashes Argon2id y un comando local para la primera cuenta.
No incorpora login/logout, tokens, cookies, pantallas, autenticacion global ni
sincronizacion. Los endpoints comerciales y sus identificadores declarados
siguen funcionando como antes. Crear una cuenta no autentica sus llamadas HTTP.
El endpoint de resolucion de correcciones conserva su bloqueo de desarrollo;
este sprint no activa su configuracion ni lo habilita para produccion.

### Modelo y compatibilidad

`Usuario` (`usuarios`) tiene ID local autoincremental, `global_id` UUID estable,
`nombre_usuario` unico, `nombre_visible`, `password_hash`, `activo`, `rol`,
`created_at`, `updated_at` y `version`. La version empieza en 1 y SQLAlchemy la
incrementa y comprueba en actualizaciones ORM para detectar escrituras obsoletas.
El UUID de una identidad persistida no se reasigna mediante el modelo.

El acceso se normaliza con Unicode NFKC, espacios externos eliminados y
`casefold`; luego se admiten 3-64 caracteres ASCII `[a-z0-9._-]`, empezando por
letra o numero. Se evita asi depender de reglas distintas de acentos/case en
las collations MariaDB y SQLite. El nombre visible admite Unicode y hasta 120
caracteres. La normalizacion tambien se aplica en el modelo ORM.

`inicializacion_identidad` es un marcador persistente con una unica fila (ID 1)
y fecha de inicializacion. Se confirma junto con la primera identidad y se
conserva aunque se desactive, cambie de rol o elimine una cuenta. No borrarlo
para volver a instalar administradores: una recuperacion futura necesita un
procedimiento administrativo separado y autorizado.

La migracion aditiva `20261021_0012` desciende de `20261016_0011`, crea solo
estas dos tablas y sus restricciones/indices. No modifica tablas comerciales,
no agrega referencias a los IDs historicos y no inventa asociaciones. Un ID
local nuevo puede coincidir numericamente con un ID historico declarado:
eso no prueba que representen la misma persona. Una futura conciliacion debe
ser explicita, sin reescribir el historial ni adjudicarle autenticacion pasada.
El downgrade se rechaza si hay cuentas o marcador de inicializacion.

### Roles y permisos internos

El mapa inmutable de `backend/app/permissions/usuarios.py` define permisos por
operacion, sin tablas de administracion dinamica ni jerarquia implicita:

| Rol | Permiso inicial |
| --- | --- |
| `ADMINISTRADOR` | `usuarios.administrar` |
| `SUPERVISOR` | `caja.correcciones.resolver` |
| `CAJERO` | `caja.operar` |

Administrar identidades no concede automaticamente facultades financieras.
Una cuenta inactiva no tiene permisos. La comprobacion recibe una entidad del
backend, no un diccionario de roles enviado por el cliente. Aun no existe una
dependencia HTTP de usuario autenticado: esta politica NO protege por si sola
los endpoints actuales. Integrarla con identidad confiable corresponde a otra
etapa antes de habilitar supervision en produccion.

`UsuarioService.crear_usuario()` es una primitiva interna para cuentas CAJERO o
SUPERVISOR, sin endpoint publico. Rechaza crear ADMINISTRADOR por esa via.
`crear_primer_administrador()` solo permite inicializar una base sin identidades
ni marcador. Tampoco existe una via para agregar administradores posteriores.
`verificar_credenciales()` devuelve una lectura sin secretos o `None`, sin
crear sesiones de acceso y sin actualizar automaticamente hashes. No es un login.

### Credenciales y comando de instalacion

Dependencia directa nueva: `argon2-cffi==25.1.0` (con sus bindings CFFI).
Los hashes son Argon2id con salt aleatoria, memoria 64 MiB, tiempo 3 y paralelismo
4. Las contrasenas admiten 15-1024 caracteres, espacios y Unicode, y no pueden
ser solo espacios. No se recortan ni normalizan. No hay contrasenas predeterminadas,
requisitos arbitrarios de clases de caracteres ni almacenamiento reversible.
El esquema de entrada oculta/excluye la contrasena de representaciones y dumps;
las respuestas de usuario nunca incluyen el hash.

Una vez revisada y aplicada por separado la migracion 0012, desde la raiz del
repositorio, en una terminal **elevada como administrador de Windows** (o root
en Unix), ejecutar manualmente:

```powershell
.\.venv\Scripts\python.exe -m backend.app.cli.crear_primer_administrador
```

El comando usa la conexion configurada del backend; el operador debe comprobar
la instancia objetivo y sus permisos antes de instalar. Solicita nombre de
usuario, nombre visible y dos veces la contrasena mediante entrada oculta.
No acepta argumentos de credenciales, variables de contrasena inicial ni
archivos de contrasenas. Rechaza terminales no interactivas y el fallback de
`getpass` a entrada visible. La salida no imprime usuario elegido, contrasena,
hash, parametros SQL ni cadenas de conexion.

La reserva del marcador y la cuenta se confirman en una unica transaccion;
si falla, ambos se revierten. Dos instaladores compiten por la misma clave
primaria: solo uno puede reservar la inicializacion. SQLite toma tambien
`BEGIN IMMEDIATE`; MariaDB conserva su aislamiento y usa la unicidad del
marcador. Cualquier identidad previa, incluso importada sin marcador, impide
el primer administrador. Los errores tecnicos requieren verificar el resultado
antes de reintentar si el fallo ocurrio durante la confirmacion.

El limite de acceso del comando es el sistema operativo local y los permisos
de base de datos; no crea un mecanismo de autenticacion web. Alguien que ya
disponga de acceso administrativo directo a MariaDB puede alterar sus tablas.
No usar este comando en las sucursales como sustituto de la provision central
cuando se implemente esa arquitectura.

### Preparacion central/local y limites pendientes

El ID local sirve para relaciones dentro de una instancia; el UUID identifica
la misma cuenta entre central y sucursales. La arquitectura objetivo conserva
al central como autoridad para altas, cambios de rol, credenciales y bajas.
La copia local debera aplicar cambios por UUID y version, incluyendo `activo=false`
en lugar de eliminar historiales. Las fechas no sustituyen a la version.

El contador ORM solo detecta concurrencia local: por si solo no resuelve
escrituras divergentes entre instalaciones. Quedan pendientes el protocolo
de distribucion, procedencia de cambios, conflictos de nombres, revocaciones,
recuperacion de administracion y distribucion segura de credenciales. No se
replican hashes, contrasenas ni sesiones en C1. Autenticar localmente sin
Internet sera posible cuando se agreguen sesiones sobre la copia local; C1
no promete operacion con el terminal desconectado del backend.

Las pruebas de C1 usan SQLite en memoria/archivos temporales y simulan la CLI.
La migracion se verifica exclusivamente sobre una base SQLite en memoria.
No se ejecuta el comando de instalacion contra una base real. La concurrencia
y aplicacion real de esta nueva migracion en MariaDB quedan pendientes de
validacion autorizada. La fixture protegida de `ard_suite_pytest` valida
integracion desde metadata; la validacion de migraciones usa `ard_suite_test`
por separado y no utiliza esa fixture.

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

## Sprint 9.5C-C2 — Autenticacion local y sesiones individuales

C2 agrega un contrato backend de autenticacion sobre las identidades de C1.
No instala autenticacion global ni modifica autorizaciones comerciales: C3
protegera rutas y C4 reemplazara las autorizaciones provisionales de supervisores.
No implementa sincronizacion, recuperacion offline central ni otros mecanismos C5.
Las notas anteriores que describen la ausencia de login corresponden a C1.

**DEC-214 no esta completamente operativa hasta integrar la aplicacion cliente.**
El cliente debera solicitar usuario y contrasena en cada apertura, mantener los
tokens y el localizador solo en memoria, serializar renovaciones y llamar a logout
antes de cerrar normalmente. No persistir tokens en localStorage ni en archivos.
Un cierre abrupto no puede notificarse de forma garantizada: las credenciales
expiran y la sesion abandonada queda sujeta al plazo de recuperacion. Si las
credenciales ya expiraron, el cierre remoto exige reautenticacion; descartar el
estado local no equivale a confirmar un logout en el servidor.

### Modelo y estados

La migracion aditiva `20261110_0013` depende de `20261021_0012`. Crea solamente:

- `sesiones_usuario`: identidad, version del usuario, contexto local, actividad,
  bloqueo y finalizacion. Su UUID publico es un localizador sin privilegios.
- `credenciales_sesion`: parejas de verificadores de acceso/renovacion, generaciones,
  candidaturas, activacion, expiraciones e invalidacion. No almacena tokens recuperables.
- `configuracion_seguridad`: sobrescrituras por parametro y ambito, con version,
  claves no nulas y unicidad `(ambito, clave, parametro)`.
- `limites_autenticacion`: reservas y contadores persistentes por cuenta/origen
  y por origen, con generaciones para no descontar reservas de una ventana nueva.
- `auditoria_autenticacion`: eventos estructurados y referencias internas a usuario
  y sesion cuando corresponda, sin UUID publicos, nombres intentados, tokens,
  verificadores, contrasenas, cuerpos HTTP ni mensajes de excepciones originales.

Las nuevas tablas usan InnoDB. Sus fechas se escriben expresamente en UTC;
`DATETIME(6)` preserva microsegundos en MariaDB y las respuestas incluyen UTC.
No se depende de `NOW()` ni de la zona horaria de Windows o de la conexion.
No se reinterpretan fechas historicas. El reloj local del servidor debe ser confiable.

Los estados logicos son `ACTIVA`, `BLOQUEADA`, `CERRADA` y `REVOCADA`. La
expiracion de tokens no cierra la sesion. La inactividad bloquea sin cerrar
turnos de caja, cancelar ventas ni modificar operaciones pendientes.
Una cuenta desactivada o con version distinta a la que inicio la sesion
provoca su revocacion en la siguiente validacion; este comportamiento es
conservador y tambien se aplica a cambios de rol/version de C1.

Una sesion bloqueada puede desbloquearse tras mas de 30 minutos usando su
localizador y la contrasena de su propietario, aun sin tokens vigentes. Se
revalidan propietario, version, estado y contexto bajo bloqueo antes de emitir
credenciales nuevas. Una sesion cerrada, revocada o abandonada fuera del plazo
de recuperacion nunca se reabre. Si se perdio el localizador, corresponde login.
El localizador por si solo no permite consultar, renovar, cerrar ni desbloquear.

### Politica de seguridad

La prioridad por parametro es puesto -> sucursal -> general -> valor inicial.
GENERAL usa clave vacia; SUCURSAL/PUESTO requieren clave no vacia. Los parametros
se validan al leerlos y una politica desconocida, fuera de rango o incoherente
falla cerrada. No hay interfaz ni endpoint publico de administracion en C2.

| Parametro | Inicial | Limites admitidos |
| --- | --- | --- |
| `inactividad` | 900 s | 60–7200 s |
| `intentos_cuenta` | 5 | 3–10 |
| `bloqueo_intentos` | 900 s | 60–3600 s |
| `reinicio_fallos` | 1800 s sin fallos | 300–7200 s |
| `intentos_origen` | 30 admisiones | 10–120 |
| `ventana_origen` | 60 s | 30–900 s |
| `acceso` | 300 s | 60–600 s |
| `renovacion` | 1800 s | 900–14400 s |
| `preparacion` | 60 s | 15–120 s |
| `recuperacion` | 86400 s desde ultima actividad | 3600–2592000 s |

Recuperacion debe superar inactividad; renovacion debe superar acceso y ser al
menos igual a inactividad. **No hay vencimiento fijo desde la creacion de la
sesion:** la actividad interactiva desplaza el horizonte de recuperacion, por
lo que una jornada activa puede continuar. Consultar, preparar y confirmar
renovaciones no actualiza actividad ni impide el bloqueo. El bloqueo se
materializa y audita en la siguiente comprobacion; no hay un proceso de barrido.

`AUTH_SUCURSAL` y `AUTH_PUESTO` son identificadores opcionales de instalacion
controlados por el backend. No se obtiene contexto de JSON ni de cabeceras del
cliente. El repositorio aun no tiene modelos de sucursal/puesto; no se equiparan
con destinos de inventario o cajas. Varios puestos detras de un mismo backend
necesitaran provision confiable antes de poder distinguirlos y autorizarlos.
No se modifica `.env` para habilitar esta configuracion.

Los intentos se reservan atomicamente antes de ejecutar Argon2. Un exito devuelve
solo su reserva de cuenta/origen; los fallos conservan la reserva y actualizan
la fecha del ultimo fallo. Una peticion interrumpida puede consumir cuota hasta
su recuperacion temporal. Los intentos denegados no prolongan el bloqueo.
La cuota por origen incluye las admisiones exitosas y los rechazos por cuota
de cuenta, para
limitar consumo de Argon2 y ataques entre nombres diferentes. Los limites no
desactivan cuentas ni cierran sesiones en otros origenes. Una IP compartida
puede afectar a varios clientes; el esquema no elimina ataques distribuidos.
Se utiliza el peer HTTP directo y se ignora `X-Forwarded-For`: un proxy requiere
un diseno posterior de origen confiable, no aceptar cabeceras arbitrarias.

### Contrato HTTP

| Metodo y ruta | Entrada y efecto |
| --- | --- |
| `POST /api/auth/login` | `nombre_usuario`, `password`; crea sesion y pareja activa |
| `GET /api/auth/sesion` | `Authorization: Bearer <token_acceso>`; consulta sin registrar actividad |
| `POST /api/auth/actividad` | Bearer de acceso; registra interaccion si aun esta activa |
| `POST /api/auth/renovar` | `token_renovacion`; prepara pareja candidata |
| `POST /api/auth/renovar/confirmar` | `rotacion_id`, `token_renovacion` candidato; confirma sin devolver tokens |
| `POST /api/auth/desbloquear` | `sesion_id`, `nombre_usuario`, `password`; autentica al propietario sin token vigente |
| `POST /api/auth/logout` | `token_renovacion` vigente; cierra solo esa sesion |

Los localizadores se reciben en cuerpos JSON, nunca como parametros de URL.
Login, preparacion y desbloqueo devuelven `sesion_id`, `rotacion_id`, ambos
tokens y sus fechas. En preparacion las expiraciones de uso son nulas y se
devuelve `confirmar_hasta`; los tokens pendientes no autentican solicitudes.
Los tokens solo aparecen en respuestas exitosas que deben entregarlos al cliente.
Usar TLS al transportar credenciales por una red, incluso en una LAN sin Internet.
No configurar proxies o herramientas para registrar cuerpos o cabeceras de autenticacion.

Los rechazos de credenciales usan 401 generico; los limites por origen usan 429
sin revelar cuentas. Una credencial vigente de sesion bloqueada recibe 423
en operaciones que requieren sesion activa. Entradas invalidas usan 422 sin
reflejar valores; errores internos usan 503 sin SQL ni excepciones originales.
Las respuestas llevan `Cache-Control: no-store`. C2 no crea cookies.

El cliente debe llamar a actividad solo por interacciones reales. El servidor
no puede distinguir una accion fisica de una llamada fabricada por un cliente
que posee un bearer: este endpoint no demuestra presencia humana. Renovacion
y consulta de sesion nunca llaman a actividad de manera implicita.

### Renovacion, concurrencia y respuestas perdidas

Cada token tiene 256 bits aleatorios; solo se persiste SHA-256 del token.
Las contrasenas siguen usando exclusivamente Argon2id de C1.

1. Preparar valida la renovacion actual sin extender ninguna expiracion anterior.
   Guarda una candidata con plazo corto y sustituye cualquier pendiente previa.
2. El cliente conserva la pareja candidata en memoria y confirma presentando
   su token de renovacion. Se revalidan la generacion de origen, su expiracion,
   el plazo de candidatura y la sesion bajo bloqueo.
3. La confirmacion activa la candidata e invalida la pareja anterior y las
   candidaturas pendientes en la misma transaccion. Las expiraciones nuevas
   comienzan al activar; no existe gracia para credenciales invalidadas.
4. Repetir la confirmacion solo reconoce esa misma generacion aun vigente.
   Devuelve las mismas expiraciones, sin prolongarlas, emitir tokens ni duplicar
   el evento de renovacion. Una generacion sustituida o finalizada se rechaza.

Si se pierde la respuesta de preparacion, puede prepararse otra pareja usando
la credencial anterior aun vigente. Si se pierde la respuesta de confirmacion,
puede repetirse con la candidata ya recibida. Si se pierde todo el estado en
memoria, se agotan los plazos o la sesion se bloquea, corresponde autenticar
mediante contrasena. No se guardan secretos recuperables para reenviar respuestas.

Las operaciones usan lecturas actuales con `FOR UPDATE` en MariaDB; SQLite usa
`BEGIN IMMEDIATE` en las pruebas. La verificacion Argon2 ocurre fuera de bloqueos
de filas y luego se comprueba nuevamente version/estado de identidad. El cierre,
desbloqueo y revocacion coordinan sus cambios mediante la misma fila de sesion.
Un cierre confirmado invalida todas sus credenciales y no puede ser revertido
por una confirmacion posterior. Los conflictos transitorios anteriores al commit
se recuperan por etapa con rollback completo y hasta tres intentos totales,
segun se detalla mas abajo. Nunca se repite una etapa despues de intentar commit
con resultado potencialmente desconocido.

`AutenticacionService.revocar()` es un puerto interno para llamadas confiables;
no hay un endpoint publico para revocar otra sesion ni sincronizacion C5.
Las referencias de auditoria son IDs internos, no los localizadores que usa el
cliente. No hay purga automatica de auditorias o credenciales historicas en C2;
se requiere planificar retencion/capacidad y una politica administrativa posterior.

### Validacion posterior autorizada

Las pruebas locales estan en `test_sprint_9_5c_c2_autenticacion.py`. Incluyen una
migracion aplicada solamente en SQLite nueva en memoria, paridad con metadata,
proteccion de tablas anteriores, expiraciones, cuotas, auditoria, errores HTTP
y carreras de rotacion/cierre/desbloqueo. La compilacion del DDL MariaDB se
verifica sin conectarse. SQLite no certifica los bloqueos reales de MariaDB.

Antes de validar la migracion real, obtener autorizacion expresa y respaldo
verificado. Mantener `ard_suite_dev` intacta; no aplicar migraciones a
`ard_suite_pytest`. Para una validacion autorizada en `ard_suite_test`:

1. Inspeccionar la revision 0012 y la ausencia de las cinco tablas nuevas mediante
   consultas de solo lectura; verificar host, puerto y `DATABASE()` efectivos.
2. Preparar un proceso hijo con `DB_HOST=127.0.0.1`, `DB_PORT=3307` y
   `DB_NAME=ard_suite_test`, credenciales solicitadas de forma segura y sin tocar
   `.env`. Revisar `env.py` y confirmar que usa esa misma configuracion.
3. Cerrar la conexion de prevalidacion y aplicar exclusivamente `20261110_0013`,
   nunca `head` ni un comando sin destino explicito.
4. En otra conexion verificar revision, tablas vacias, columnas, claves,
   indices, CHECK e InnoDB. Mostrar definiciones reales de CHECK para revision.
   No crear administradores ni ejecutar pruebas destructivas en esta base.

MariaDB puede confirmar DDL parcialmente. Ante errores detenerse e inspeccionar;
no asumir rollback completo, reintentar, ejecutar stamp o downgrade automaticamente.
El downgrade de 0013 comprueba todas sus tablas antes de cualquier DDL y rechaza
perdida de configuracion, sesiones, limites o auditoria.

Las ocho pruebas reales de C2 estan en `test_sprint_9_5c_c2_mariadb.py` y quedan
pendientes de autorizacion. Usan sin modificaciones `mariadb_test_sessions()`:
opt-in completo `ARD_TEST_DB_*`, base `ard_suite_pytest` vacia, identidad real,
lock exclusivo, rechazo de tablas/vistas previas y limpieza solo de metadata
propia. No crean bases ni usuarios ni ejecutan Alembic. Para ejecutarlas despues
de autorizarlas, usar el procedimiento PowerShell de proceso hijo documentado
mas arriba, cambiando unicamente sus argumentos pytest por:

```text
-B -m pytest -p no:cacheprovider backend/tests/test_sprint_9_5c_c2_mariadb.py -q -rs
```

Una interrupcion puede dejar tablas residuales; la fixture las rechazara en la
siguiente ejecucion. Inspeccionarlas manualmente, sin borrado indiscriminado ni
reintentos automaticos. No seleccionar `ard_suite_test` para estas pruebas.

### C2: conflictos de snapshot y repeticion controlada de MariaDB

El error 1020 (`ER_CHECKREAD`) no identifica un deadlock: bajo `REPEATABLE READ`
con `innodb_snapshot_isolation=ON`, MariaDB puede rechazar un bloqueo o una
escritura sobre filas que cambiaron respecto de la vista de lectura. Ver la
[documentacion de MariaDB](https://mariadb.com/docs/server/server-usage/storage-engines/innodb/innodb-system-variables#innodb_snapshot_isolation).
Una busqueda previa por token/localizador puede crear esa vista antes de esperar
`FOR UPDATE`. `populate_existing` refresca el objeto ORM, no la vista del servidor.
Asimismo, el `SELECT` de politica anterior a los upserts podia dejar fuera de
la vista las cuotas insertadas/modificadas por otros trabajadores: un
`INSERT ... ON DUPLICATE KEY UPDATE`, incluso sin cambiar la clave, no es una
lectura inocua ni restablece el snapshot. La politica ahora se lee despues de
los upserts y bloqueos de cuotas, manteniendo su orden por clave.

Se conservan aislamiento, bloqueos, revalidaciones y esquema. No se cambia
`innodb_snapshot_isolation`. Cada etapa admite como maximo tres intentos totales
solo para `OperationalError` MySQL/MariaDB con codigo entero 1020, 1205 o 1213 y
conexion no invalidada. El rollback de la transaccion raiz es obligatorio antes
de volver a descubrir filas, bloquearlas y comprobar estado, generacion y plazos.
No hay reintento de un SELECT aislado ni uso de savepoints para reutilizar una
vista antigua. Agotar los intentos sigue siendo error tecnico, no exito ni 401.

La admision de cuota, Argon2 y finalizacion del login/desbloqueo son etapas
separadas. Repetir la finalizacion no vuelve a reservar cuota ni a ejecutar Argon2;
se conserva la reserva y se comprueba otra vez la identidad/version bajo bloqueo.
Las reservas, auditorias y credenciales de un intento abortado se revierten juntas.
La finalizacion de una cuenta conocida bloquea primero el usuario incluso cuando
la contrasena falla: la FK de auditoria no debe adquirir implicitamente ese
bloqueo despues de bloquear la cuota, invirtiendo el orden de una autenticacion
exitosa. Las busquedas y bloqueos de sesion/credenciales mantienen el orden existente.
Las denegaciones esperadas siguen confirmando su auditoria/contadores una sola vez.
El servicio ejecuta `flush()` explicitamente antes de entrar en el commit para
recuperar conflictos de escrituras diferidas. Cualquier error despues de intentar
commit, incluso 1020, se propaga sin repetir la etapa: su resultado puede ser incierto.

`test_auth_transaction_retry.py` reproduce los escenarios con SQLite y errores
del driver inyectados, incluyendo SQL durante flush, resultado competidor,
expiracion y desactivacion durante el reintento. No certifica MariaDB real.
Los ocho casos reales conservan la fixture protegida y comprueban tambien las
credenciales invalidadas, la no reactivacion tras revocacion/cierre y las cuotas.

Para repetirlos tras autorizacion expresa, comprobar que `ard_suite_pytest`
este vacia. Usar el bloque PowerShell de proceso hijo de esta documentacion con
los seis `ARD_TEST_DB_*` explicitos y estos argumentos exactos, sin paralelismo
de pytest ni repeticion automatica de la suite:

```text
-B -m pytest -p no:cacheprovider backend/tests/test_sprint_9_5c_c2_mariadb.py -q -ra
```

`-p no:cacheprovider` evita los `PytestCacheWarning` de permisos en `.pytest_cache`;
esos warnings no son errores de concurrencia. No borrar caches ni estructuras
residuales automaticamente para intentar que las pruebas pasen. Este procedimiento
no modifica `.env`, no aplica Alembic y no crea bases ni usuarios.
