"""Local session primitives. Tokens are random; only their digests are persisted."""
from datetime import datetime, timezone
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import DateTime
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Persist naive UTC on both supported engines; expose aware UTC in Python."""
    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        # Preserve exact acknowledgement deadlines on MariaDB as well as SQLite.
        datatype = DATETIME(fsp=6) if dialect.name in {"mysql", "mariadb"} else DateTime()
        return dialect.type_descriptor(datatype)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Se requiere una fecha UTC con zona horaria")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


def utc_now():
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def nuevo_token() -> str:
    return token_urlsafe(32)
