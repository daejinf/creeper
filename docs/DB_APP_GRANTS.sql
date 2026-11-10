-- Administrator only, AFTER alembic upgrade head. No stored password here.
-- Set the login password interactively using psql: \password wemeet_app
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='wemeet_app') THEN
    CREATE ROLE wemeet_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
END $$;
GRANT CONNECT ON DATABASE wemeet_goals TO wemeet_app;
GRANT USAGE ON SCHEMA wemeet_goal TO wemeet_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA wemeet_goal TO wemeet_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA wemeet_goal TO wemeet_app;
GRANT UPDATE ON wemeet_goal.groups, wemeet_goal.members,
  wemeet_goal.record_heads, wemeet_goal.sync_attempts, wemeet_goal.records,
  wemeet_goal.module_inputs, wemeet_goal.module_operations TO wemeet_app;
-- Deliberately no DELETE, schema CREATE, role management, or snapshot UPDATE.
