import os
from alembic import context
from sqlalchemy import create_engine
from wemeet_ai.persistence.schema import metadata
from wemeet_ai.persistence import journal_schema

url=os.environ.get('WEMEET_GOAL_ADMIN_DATABASE_URL') or os.environ.get('WEMEET_GOAL_DATABASE_URL')
if not url:raise RuntimeError('WEMEET_GOAL_DATABASE_URL is required')
if context.is_offline_mode():
    context.configure(url=url,target_metadata=metadata,literal_binds=True,include_schemas=True)
    with context.begin_transaction():context.run_migrations()
else:
    engine=create_engine(url,hide_parameters=True)
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=metadata,include_schemas=True)
        with context.begin_transaction():context.run_migrations()
