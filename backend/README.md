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

Cuando existan modelos, se podrá generar una migración con:

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
