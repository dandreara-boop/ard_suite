from dataclasses import dataclass

from sqlalchemy import select

from backend.app.models.autenticacion import ConfiguracionSeguridad


# Default, minimum and maximum; units are seconds except attempt counts.
PARAMETROS = {
    "inactividad": (900, 60, 7200),
    "intentos_cuenta": (5, 3, 10),
    "bloqueo_intentos": (900, 60, 3600),
    "reinicio_fallos": (1800, 300, 7200),
    "intentos_origen": (30, 10, 120),
    "ventana_origen": (60, 30, 900),
    "acceso": (300, 60, 600),
    "renovacion": (1800, 900, 14400),
    "preparacion": (60, 15, 120),
    "recuperacion": (86400, 3600, 2592000),
}


@dataclass(frozen=True)
class ContextoSeguridad:
    # Trusted installation context, never taken from request JSON/headers.
    sucursal: str | None = None
    puesto: str | None = None

    def __post_init__(self):
        if any(value is not None and (not value or len(value) > 64)
               for value in (self.sucursal, self.puesto)):
            raise ValueError("Contexto local invalido")


def politica(db, contexto: ContextoSeguridad) -> dict[str, int]:
    result = {key: spec[0] for key, spec in PARAMETROS.items()}
    for ambito, clave in (("GENERAL", ""), ("SUCURSAL", contexto.sucursal), ("PUESTO", contexto.puesto)):
        if clave is None:
            continue
        for row in db.scalars(select(ConfiguracionSeguridad).where(
            ConfiguracionSeguridad.ambito == ambito,
            ConfiguracionSeguridad.clave == clave,
        )).all():
            if row.parametro not in PARAMETROS:
                raise ValueError("Politica de seguridad invalida")
            _, minimum, maximum = PARAMETROS[row.parametro]
            if not minimum <= row.valor <= maximum:
                raise ValueError("Politica de seguridad fuera de limites")
            result[row.parametro] = row.valor
    if not (result["recuperacion"] > result["inactividad"]
            and result["renovacion"] > result["acceso"]
            and result["renovacion"] >= result["inactividad"]):
        raise ValueError("Politica de seguridad incoherente")
    return result
