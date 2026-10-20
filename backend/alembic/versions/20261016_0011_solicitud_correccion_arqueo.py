"""Motivo obligatorio y una solicitud por arqueo (Sprint 9.5C-A).

Revision ID: 20261016_0011
Revises: 20261007_0010

Aplicar en ventana de mantenimiento, sin escritores de solicitudes. Nunca se
inventan motivos ni se eliminan historicos. Si hay registros, resolver su
tratamiento explicitamente antes de reintentar esta revision.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20261016_0011"
down_revision: Union[str, Sequence[str], None] = "20261007_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("Esta revision requiere prevalidacion online de datos; no admite --sql")
    bind = op.get_bind()
    if bind.dialect.name not in {"mysql", "mariadb"}:
        raise RuntimeError("Esta revision requiere MariaDB/MySQL y ventana sin escritores")
    historicas = bind.scalar(sa.text("SELECT COUNT(*) FROM solicitudes_correccion_arqueo"))
    duplicados = bind.scalar(sa.text(
        "SELECT COUNT(*) FROM (SELECT arqueo_id FROM solicitudes_correccion_arqueo "
        "GROUP BY arqueo_id HAVING COUNT(*) > 1) AS duplicados"
    ))
    # Every historical row lacks motivo in revision 0010. Validate everything
    # before the first DDL: MariaDB implicitly commits DDL.
    if historicas or duplicados:
        raise RuntimeError(
            f"Migracion detenida antes de DDL: {historicas} solicitudes historicas sin motivo, "
            f"{duplicados} arqueos duplicados. Se requiere tratamiento explicito sin borrar "
            "registros ni inventar motivos."
        )
    # One ALTER avoids a validated schema ending up with only one of the two
    # changes. The deployment must quiesce writers between preflight and DDL.
    op.execute(sa.text(
        "ALTER TABLE solicitudes_correccion_arqueo "
        "ADD COLUMN motivo TEXT NOT NULL, "
        "ADD CONSTRAINT uq_solicitudes_correccion_arqueo_arqueo_id UNIQUE (arqueo_id)"
    ))


def downgrade() -> None:
    if op.get_context().as_sql:
        raise RuntimeError("El downgrade requiere comprobar que no se pierde historial")
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM solicitudes_correccion_arqueo")):
        raise RuntimeError("Downgrade detenido: eliminaria motivos de solicitudes historicas")
    op.execute(sa.text(
        "ALTER TABLE solicitudes_correccion_arqueo "
        "DROP INDEX uq_solicitudes_correccion_arqueo_arqueo_id, DROP COLUMN motivo"
    ))
