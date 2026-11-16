from alembic import context
from tenderflow.config import Settings
from tenderflow.infrastructure.database.models import Base
from tenderflow.infrastructure.database.sessions import create_database_engine

config = context.config


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name.startswith("procrastinate_"))


def migrate(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("TENDERFLOW_DATABASE_URL is required for migrations")
    context.configure(
        url=settings.database_url.get_secret_value(),
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
elif connection := config.attributes.get("connection"):
    migrate(connection)
else:
    engine = create_database_engine(Settings())
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()
