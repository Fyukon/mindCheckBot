from __future__ import annotations
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from src.models import Base  # ensure models are imported
from src.config import settings

config = context.config

# Inject DB URL if not set
if not config.get_main_option("sqlalchemy.url"):
    # Alembic needs sync driver
    # Accept postgres:// and postgresql://
    url = settings.database_url
    if url.startswith("postgres+asyncpg://"):
        sync_url = url.replace("+asyncpg", "")
    elif url.startswith("postgres://"):
        sync_url = "postgresql://" + url[len("postgres://"):]
    elif url.startswith("postgresql+asyncpg://"):
        sync_url = "postgresql://" + url[len("postgresql+asyncpg://"):]
    else:
        sync_url = url
    if "PORT" in sync_url:
        raise RuntimeError("Invalid DATABASE_URL in env: contains 'PORT' placeholder. Replace with numeric port.")
    config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
