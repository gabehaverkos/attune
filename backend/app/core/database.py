"""The database connection, the declarative base, and loading every table.

Tables live beside the code that owns them, so this module imports none of them
up front: several of those modules import half the application. `load_models`
imports them all, and runs by itself the first time SQLAlchemy configures its
mappers (the first query or flush), so relationships and foreign keys across
modules always resolve. Anything that needs the whole schema without a query,
such as migrations or `create_all`, calls it first.
"""

import importlib
from pathlib import Path
import re

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapper, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# A class declared directly on the base: the one mark of a module that defines a table.
TABLE = re.compile(r"^class \w+\(Base\):", re.MULTILINE)


def load_models() -> None:
    """Import every module that defines tables, completing `Base.metadata`.

    Found by reading the source rather than listed, so a module that gains,
    loses or moves a table needs nothing changed here. `__main__` modules are
    commands, not definitions; importing one would run it.
    """
    root = Path(__file__).resolve().parents[1]
    for path in sorted(root.rglob("*.py")):
        if path.name == "__main__.py" or not TABLE.search(path.read_text(encoding="utf-8")):
            continue
        parts = path.relative_to(root.parent).with_suffix("").parts
        importlib.import_module(".".join(parts[:-1] if parts[-1] == "__init__" else parts))


event.listen(Mapper, "before_configured", load_models, once=True)
