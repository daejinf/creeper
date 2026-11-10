"""Encrypted, atomically replaced response spool; never a second goal database."""
import json
import os
from pathlib import Path
from uuid import uuid4
from cryptography.fernet import Fernet

class EncryptedRecoveryStore:
    def __init__(self,path,key):
        self.path=Path(path);self.path.mkdir(parents=True,exist_ok=True)
        if not key:raise ValueError('WEMEET_RECOVERY_KEY is required')
        self.cipher=Fernet(key.encode() if isinstance(key,str) else key)

    def filename(self,operation_id):
        from uuid import UUID
        return self.path/(str(UUID(operation_id))+'.bin')

    def get(self,operation_id):
        p=self.filename(operation_id)
        if not p.exists():return None
        return json.loads(self.cipher.decrypt(p.read_bytes()).decode('utf-8'))

    def put(self,operation_id,packet):
        p=self.filename(operation_id)
        old=self.get(operation_id)
        if old is not None:
            if old!=packet:raise ValueError('Recovery packet conflict')
            return
        tmp=self.path/(str(uuid4())+'.tmp')
        try:
            with tmp.open('xb') as f:
                f.write(self.cipher.encrypt(json.dumps(packet,ensure_ascii=False,sort_keys=True).encode('utf-8')))
                f.flush();os.fsync(f.fileno())
            os.replace(tmp,p)
        finally:tmp.unlink(missing_ok=True)

    def delete(self,operation_id):self.filename(operation_id).unlink(missing_ok=True)
