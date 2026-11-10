import os
from alembic import context
from sqlalchemy import create_engine
from wemeet_coach.persistence.schema import metadata
from sqlalchemy.engine import make_url
url=os.environ.get('WEMEET_COACH_ADMIN_DATABASE_URL')
expected=os.environ.get('WEMEET_COACH_EXPECTED_DATABASE')
if not url or not expected or make_url(url).database!=expected:
    raise RuntimeError('Explicit administrator URL and matching WEMEET_COACH_EXPECTED_DATABASE required')
if expected=='wemeet_goals':raise RuntimeError('Original project database is not a migration target')
with create_engine(url,hide_parameters=True).connect() as connection:
    context.configure(connection=connection,target_metadata=metadata,include_schemas=True)
    with context.begin_transaction():context.run_migrations()
