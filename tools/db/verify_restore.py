"""Administrator-only backup/restore drill; reports counts, never row bodies."""
import os,json,subprocess,hashlib
from pathlib import Path
from uuid import uuid4
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from tools.db.cutover import user_get
ROOT=Path(__file__).resolve().parents[2]
BIN=Path('C:/Program Files/PostgreSQL/16/bin')

def main():
    u=make_url(user_get('WEMEET_GOAL_ADMIN_DATABASE_URL'));name='wemeet_restore_'+uuid4().hex[:12]
    folder=Path(json.loads((ROOT/'runtime/cutover-backup.json').read_text())['backup']);dump=folder/'after.dump'
    env={**os.environ,'PGPASSWORD':u.password};common=['-h',u.host,'-p',str(u.port),'-U',u.username]
    subprocess.run([str(BIN/'pg_dump.exe'),*common,'-d',u.database,'-Fc','-f',str(dump)],env=env,check=True)
    settings=dict(host=u.host,port=u.port,user=u.username,password=u.password)
    with psycopg.connect(**settings,dbname='postgres',autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            subprocess.run([str(BIN/'pg_restore.exe'),*common,'-d',name,'--exit-on-error',str(dump)],env=env,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            counts={};hashes={}
            for database in [u.database,name]:
                with psycopg.connect(**settings,dbname=database) as c:
                    tables=[r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='wemeet_goal'")]
                    counts[database]={t:c.execute(sql.SQL('SELECT count(*) FROM wemeet_goal.{}').format(sql.Identifier(t))).fetchone()[0] for t in tables}
                    hashes[database]={}
                    for table in tables:
                        values=[json.dumps(row[0],ensure_ascii=False,sort_keys=True,default=str) for row in c.execute(sql.SQL('SELECT row_to_json(r) FROM wemeet_goal.{} r').format(sql.Identifier(table)))]
                        hashes[database][table]=hashlib.sha256('\n'.join(sorted(values)).encode()).hexdigest()
            assert counts[u.database]==counts[name]
            assert hashes[u.database]==hashes[name]
            result={'restored_counts_match':True,'restored_content_hashes_match':True,'tables':counts[u.database],'content_sha256':hashes[u.database],'paid_http':0}
            (ROOT/'runtime/restore-drill.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
        finally:admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
if __name__=='__main__':main()
