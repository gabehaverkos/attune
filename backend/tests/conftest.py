"""A throwaway database for the tests, and no way to reach a model.

The database is made on the Postgres the development stack (or the workflow)
runs at 127.0.0.1:5432, built from nothing by the migrations as a server's is,
and dropped afterwards. It is named here, before anything else of the app is
imported, because the app connects to DATABASE_URL as it loads.
"""

import os
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import psycopg
import pytest

from app.config import load_backend_env

load_backend_env()
SERVER = f"postgresql://attune:{os.environ['POSTGRES_PASSWORD']}@127.0.0.1:5432"
NAME = f"attune_test_{uuid.uuid4().hex[:8]}"
os.environ["DATABASE_URL"] = f"{SERVER}/{NAME}"
# Nothing here should call a model, and without keys nothing can.
os.environ["OPENROUTER_API_KEY"] = os.environ["VOYAGE_API_KEY"] = ""


@pytest.fixture(scope="session", autouse=True)
def migrations():
    """The migrated database, for the whole run; gives the migrations' configuration."""
    with psycopg.connect(f"{SERVER}/postgres", autocommit=True) as server:
        server.execute(f'CREATE DATABASE "{NAME}"')
    try:
        config = Config(Path(__file__).parents[1] / "alembic.ini")
        command.upgrade(config, "head")
        yield config
    finally:
        from app.core.database import engine

        engine.dispose()
        with psycopg.connect(f"{SERVER}/postgres", autocommit=True) as server:
            server.execute(f'DROP DATABASE "{NAME}" WITH (FORCE)')
