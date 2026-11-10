"""Explicit, administrator-only backup/migration/provisioning. Never runs at startup."""
import os,json,hashlib,secrets,sqlite3,subprocess,winreg
from pathlib import Path
from datetime import datetime
from sqlalchemy.engine import make_url
import psycopg
from psycopg import sql
ROOT=Path(__file__).resolve().parents[2]

def user_get(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as k:return winreg.QueryValueEx(k,name)[0]
    except FileNotFoundError:return None

def user_set(name,value):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,'Environment') as k:winreg.SetValueEx(k,name,0,winreg.REG_SZ,value)
    os.environ[name]=value

def main():
    admin=user_get('WEMEET_GOAL_ADMIN_DATABASE_URL') or user_get('WEMEET_GOAL_DATABASE_URL')
    u=make_url(admin)
    if u.username=='wemeet_app':raise RuntimeError('Administrator URL required')
    folder=Path((ROOT/'.backup-location.txt').read_text().strip())/('unified-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    folder.mkdir(parents=True)
    env={**os.environ,'PGPASSWORD':u.password}
    subprocess.run(['C:/Program Files/PostgreSQL/16/bin/pg_dump.exe','-h',u.host,'-p',str(u.port),'-U',u.username,'-d',u.database,'-Fc','-f',str(folder/'before.dump')],env=env,check=True)
    source=ROOT/'runtime/records.sqlite3'
    if source.exists():
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as old,sqlite3.connect(folder/'records.sqlite3') as backup:old.backup(backup)
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()}
    (folder/'hashes.json').write_text(json.dumps(manifest,indent=2))
    os.environ['WEMEET_GOAL_ADMIN_DATABASE_URL']=admin
    subprocess.run([os.sys.executable,'-m','alembic','upgrade','head'],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'src')},check=True)
    existing=user_get('WEMEET_GOAL_DATABASE_URL')
    password=make_url(existing).password if existing and make_url(existing).username=='wemeet_app' else secrets.token_urlsafe(40)
    with psycopg.connect(host=u.host,port=u.port,user=u.username,password=u.password,dbname=u.database) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='wemeet_app'").fetchone():
            c.execute(sql.SQL('CREATE ROLE wemeet_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD {}').format(sql.Literal(password)))
        c.execute('GRANT CONNECT ON DATABASE wemeet_goals TO wemeet_app')
        c.execute('GRANT USAGE ON SCHEMA wemeet_goal TO wemeet_app')
        c.execute('GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA wemeet_goal TO wemeet_app')
        c.execute('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA wemeet_goal TO wemeet_app')
        c.execute('GRANT UPDATE ON wemeet_goal.groups,wemeet_goal.members,wemeet_goal.record_heads,wemeet_goal.sync_attempts,wemeet_goal.records,wemeet_goal.module_inputs,wemeet_goal.module_operations TO wemeet_app')
    user_set('WEMEET_GOAL_ADMIN_DATABASE_URL',admin)
    user_set('WEMEET_GOAL_DATABASE_URL',u.set(username='wemeet_app',password=password).render_as_string(hide_password=False))
    if not user_get('WEMEET_RECOVERY_KEY'):
        from cryptography.fernet import Fernet
        user_set('WEMEET_RECOVERY_KEY',Fernet.generate_key().decode())
    (ROOT/'runtime/cutover-backup.json').write_text(json.dumps({'backup':str(folder),'sha256':manifest,'migration':'0003_unified_journal','app_role':'wemeet_app'},indent=2))
    print('Backup verified by SHA256; migration applied; restricted app role and recovery key configured. No credentials printed.')
if __name__=='__main__':main()
