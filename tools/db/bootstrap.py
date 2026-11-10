"""Create an isolated loopback PostgreSQL cluster; never touch an existing service.

Credentials live only in the Windows user environment. The short-lived initdb
password file is ACL-restricted and removed even when setup fails.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'runtime'/'postgres-goals'
BIN=Path(os.getenv('WEMEET_POSTGRES_BIN','C:/Program Files/PostgreSQL/16/bin'))
PORT='55432'

def run(args,**kw):
    # A background postgres process can inherit captured pipes on Windows and
    # keep communicate() waiting forever. Only whoami needs captured output.
    capture=str(args[0]).lower()=='whoami.exe'
    result=subprocess.run([str(a) for a in args],stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,**kw)
    if result.returncode:raise RuntimeError('PostgreSQL setup command failed: '+Path(str(args[0])).name+' (see runtime/postgres-goals.log)')
    return result

def start():
    if not DATA.joinpath('PG_VERSION').exists():raise RuntimeError('Run python -m tools.db.bootstrap once first')
    status=subprocess.run([str(BIN/'pg_ctl.exe'),'status','-D',str(DATA)],capture_output=True)
    if status.returncode:
        run([BIN/'pg_ctl.exe','start','-D',DATA,'-l',ROOT/'runtime'/'postgres-goals.log',
            '-o',f'-h 127.0.0.1 -p {PORT}','-w'])

def bootstrap():
    import winreg
    if DATA.joinpath('PG_VERSION').exists():
        start()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as k:
            os.environ['WEMEET_GOAL_DATABASE_URL']=winreg.QueryValueEx(k,'WEMEET_GOAL_DATABASE_URL')[0]
    else:
        password=secrets.token_urlsafe(32)
        DATA.parent.mkdir(parents=True,exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='wemeet-pg-',dir=DATA.parent)
        os.close(fd)
        try:
            user=run(['whoami.exe']).stdout.decode().strip()
            run(['icacls.exe',name,'/inheritance:r','/grant:r',user+':F'])
            Path(name).write_text(password,encoding='utf-8')
            run([BIN/'initdb.exe','-D',DATA,'-U','wemeet_ai','--encoding=UTF8','--locale=C','--auth=scram-sha-256','--pwfile='+name])
        finally:Path(name).unlink(missing_ok=True)
        url=f'postgresql+psycopg://wemeet_ai:{quote(password,safe="")}@127.0.0.1:{PORT}/wemeet_goals'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,'Environment') as k:
            winreg.SetValueEx(k,'WEMEET_GOAL_DATABASE_URL',0,winreg.REG_SZ,url)
        os.environ['WEMEET_GOAL_DATABASE_URL']=url
        start()
    import psycopg
    from sqlalchemy.engine import make_url
    settings=make_url(os.environ['WEMEET_GOAL_DATABASE_URL'])
    with psycopg.connect(host=settings.host,port=settings.port,user=settings.username,password=settings.password,dbname='postgres',autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname='wemeet_goals'").fetchone():
            c.execute('CREATE DATABASE wemeet_goals')
    run([os.sys.executable,'-m','alembic','upgrade','head'],cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'src')})
    with psycopg.connect(host=settings.host,port=settings.port,user=settings.username,password=settings.password,dbname='wemeet_goals') as c:
        revision=c.execute('SELECT version_num FROM alembic_version').fetchone()[0]
    (ROOT/'runtime'/'goal-db.json').write_text(json.dumps({'backend':'PostgreSQL','port':int(PORT),'database':'wemeet_goals','bind':'127.0.0.1','migration':revision},indent=2),encoding='utf-8')
    print('PostgreSQL ready: 127.0.0.1:'+PORT+' / wemeet_goals; migrations applied; credentials not printed')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--start-only',action='store_true');args=parser.parse_args()
    if args.start_only:start()
    else:bootstrap()
