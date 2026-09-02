import sys
from pathlib import Path
from logging.config import fileConfig

from sqlalchemy import create_engine, pool, text
from alembic import context
from sqlmodel import SQLModel

# Make the backend package importable from the alembic env.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import all table models so SQLModel.metadata is fully populated.
import app.models.documents  # noqa: F401
import app.models.retrieval  # noqa: F401

from app.config import settings

config = context.config
# Keep an explicitly supplied URL (the CLI and tests commonly inject one).
# The template placeholder is the only value that should fall back to the
# application settings loaded from .env.
configured_url = config.get_main_option("sqlalchemy.url")
if not configured_url or configured_url == "driver://user:pass@localhost/dbname":
    config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # Required for SQLite ALTER TABLE support.
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database connection."""

    url = config.get_main_option("sqlalchemy.url")
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}

    connectable = create_engine(url, connect_args=connect_args, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # Required for SQLite ALTER TABLE support.
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
