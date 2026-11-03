import hashlib
import os
from urllib.parse import urlsplit

from ..tracing import MemoryTraceSink


def provider_identity(client):
    key=os.getenv('DEEPSEEK_API_KEY','')
    return {'configured':bool(key),'model':client.model,'host':urlsplit(client.base_url).hostname,
        'key_fingerprint':hashlib.sha256(key.encode()).hexdigest()[:12] if key else None,'key_source':'DEEPSEEK_API_KEY environment',
        'billing_note':'이 키와 DeepSeek 대시보드 계정·필터가 같은지는 별도로 확인해야 합니다. 비용은 계산하지 않습니다.'}


class PersistentTraceSink(MemoryTraceSink):
    def __init__(self,repository,workspace=None):
        super().__init__();self.repository=repository;self.workspace=workspace;self.calls={};self.storage_errors=[]
    def emit(self,event):
        super().emit(event)
        try:self._persist(event)
        except Exception:self.storage_errors.append('실행 사용량 기록을 DB에 저장하지 못했습니다.')
    def _persist(self,event):
        data=event['sanitized_payload'];kind=event['event']
        if kind=='provider.request':
            call={'call_id':data['call_id'],'run_id':event['run_id'],'model':data['body']['model'],'host':urlsplit(data['url']).hostname,'called_at':event['timestamp'],'usage':{},'status':None}
            self.calls[data['call_id']]=call;self.repository.record_call(call)
        elif kind=='provider.response' and data.get('call_id') in self.calls:
            call=self.calls[data['call_id']];body=data.get('body') or {};call.update(status=data.get('status'))
            if isinstance(body,dict):call.update(request_id=body.get('id'),model=body.get('model') or call['model'],usage=body.get('usage') or {})
            self.repository.record_call(call)
        elif kind in {'run.completed','run.incomplete'}:
            self.repository.record_run(self.workspace,event.get('owner_member_id'),data['meta'],data['status'])
