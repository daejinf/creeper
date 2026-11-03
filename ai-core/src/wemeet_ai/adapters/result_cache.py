"""Local-only SQLite persistence with in-process coalescing. SW supplies its own backend."""
import json
import time
from .repository import SQLiteRepository
from ..cache import MemoryResultCache

class SQLiteResultCache(MemoryResultCache):
    def __init__(self,path):
        super().__init__();self.repository=SQLiteRepository(path)
        with self.repository.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS ai_result_cache(key TEXT PRIMARY KEY, expires REAL, payload TEXT)')
    def get(self,key):
        with self.repository.connect() as db:row=db.execute('SELECT payload FROM ai_result_cache WHERE key=? AND expires>?',(key,time.time())).fetchone()
        return json.loads(row[0]) if row else None
    def put(self,key,value,ttl=86400):
        with self.repository.connect() as db:db.execute('INSERT OR REPLACE INTO ai_result_cache VALUES(?,?,?)',(key,time.time()+ttl,json.dumps(value,ensure_ascii=False)))
