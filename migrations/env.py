from alembic import context
from sqlalchemy import create_engine

from moim import models  # noqa: F401  (register tables)
from moim.config import load_settings
from moim.db import metadata

settings = load_settings()
if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(settings.database_url, hide_parameters=True, connect_args={"options": "-c timezone=UTC"})
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
