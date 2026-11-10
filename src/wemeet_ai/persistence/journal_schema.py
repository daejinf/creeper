"""Canonical PostgreSQL conversation journal and durable execution ownership."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from .schema import metadata, col, created, S

records=sa.Table('records',metadata,
    col('record_id',primary_key=True),col('namespace',nullable=False,server_default='default'),
    col('title',nullable=False),col('revision',sa.Integer,nullable=False),
    col('metadata',JSONB,nullable=False),col('quarantine_reason'),created(),
    col('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
    sa.ForeignKeyConstraint(['record_id'],[S+'groups.group_id']),sa.CheckConstraint('revision >= 0'))
messages=sa.Table('conversation_messages',metadata,
    col('record_id',primary_key=True),col('message_id',primary_key=True),
    sa.Column('seq',sa.BigInteger,sa.Identity(),unique=True),
    col('scope',nullable=False),col('owner',nullable=False,server_default=''),
    col('body',JSONB,nullable=False),created(),
    sa.ForeignKeyConstraint(['record_id'],[S+'records.record_id']),
    sa.CheckConstraint("(scope='PUBLIC' AND owner='') OR (scope='PRIVATE' AND owner<>'')"))
drafts=sa.Table('module_inputs',metadata,
    col('record_id',primary_key=True),col('module',primary_key=True),col('scope',primary_key=True),col('owner',primary_key=True),
    col('body',JSONB,nullable=False),col('dependencies',JSONB,nullable=False),col('pending',nullable=False,server_default=''),
    sa.ForeignKeyConstraint(['record_id'],[S+'records.record_id']),
    sa.CheckConstraint("(scope='PUBLIC' AND owner='') OR (scope='SERVICE' AND owner='') OR (scope='PRIVATE' AND owner<>'')"))
artifacts=sa.Table('execution_artifacts',metadata,
    col('record_id',primary_key=True),col('action_id',primary_key=True),col('artifact_id',nullable=False),
    sa.Column('seq',sa.BigInteger,sa.Identity(),unique=True),col('module',nullable=False),col('scope',nullable=False),
    col('owner',nullable=False,server_default=''),col('body',JSONB,nullable=False),created(),
    sa.ForeignKeyConstraint(['record_id'],[S+'records.record_id']),
    sa.UniqueConstraint('record_id','artifact_id'),
    sa.CheckConstraint("(scope='PUBLIC' AND owner='') OR (scope='SERVICE' AND owner='') OR (scope='PRIVATE' AND owner<>'')"))
operations=sa.Table('module_operations',metadata,
    col('record_id',primary_key=True),col('action_id',primary_key=True),col('operation_id',nullable=False,unique=True),
    col('module',nullable=False),col('owner',nullable=False),col('input_hash',nullable=False),col('payload',JSONB,nullable=False),
    col('dependencies',JSONB,nullable=False),col('started_revision',sa.Integer,nullable=False),col('claim_token',nullable=False),
    col('state',nullable=False),col('result',JSONB),col('error_code'),created(),
    col('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
    sa.ForeignKeyConstraint(['record_id'],[S+'records.record_id']),
    sa.CheckConstraint("state IN ('RUNNING','PERSISTED','STALE','PERSIST_FAILED','UNKNOWN')"))
sa.Index('ix_records_namespace',records.c.namespace,records.c.updated_at)
sa.Index('ix_operations_state',operations.c.state,operations.c.updated_at)
for table in (messages,drafts,artifacts,operations):
    table.append_column(sa.Column('private_owner_id',sa.Text,sa.Computed("NULLIF(owner,'')",persisted=True)))
    table.append_constraint(sa.ForeignKeyConstraint(['record_id','private_owner_id'],[S+'members.group_id',S+'members.member_id'],name='fk_'+table.name+'_owner'))
