/* Code changes never trigger a paid model call or silently overwrite history. */
(()=>{
let baselineUI=null,polling=false;
const messages={CURRENT:'현재 코드로 생성됨',OUTDATED:'이전 코드로 생성됨',UNTRACKED:'이전 기록 · 코드 정보 없음',RELOAD_PENDING:'모듈 변경 반영 대기',NOT_RUN:'아직 실행하지 않음'};
window.kickoffCodeLabel=status=>messages[status]||'코드 상태 확인 중';
async function check(){if(polling||document.hidden)return;polling=true;try{const value=await api('/api/code-status');window.kickoffCode=value;if(!baselineUI)baselineUI=value.ui_revision;const pending=value.reload_required||value.adapter_reload_required;
 $('codeState').textContent='AI Core '+value.version+' · '+(value.source_kind==='WORKSPACE'?'작업 폴더 연결':'설치된 패키지 연결')+' · '+(pending?'코드 반영 대기':value.reload_mode==='AUTO'?'Python 자동 반영 켜짐':'Python 수동 재시작 모드');
 $('codeModules').textContent=Object.entries(value.modules).map(([name,v])=>name+' · 서버 '+v.loaded+' · 파일 '+v.disk).join('\n');
 $('codeRefresh').hidden=baselineUI===value.ui_revision&&!pending;
 if(baselineUI!==value.ui_revision)$('codeRefresh').textContent='화면 코드 변경됨 · 입력 보관 후 새로고침';
 else $('codeRefresh').textContent=pending&&value.reload_mode==='MANUAL'?'서버 재시작 필요':'코드 반영 상태 다시 확인';
 }catch{$('codeState').textContent='서버 코드 반영·연결 확인 중… 입력은 유지됩니다.';}finally{polling=false;}}
 $('codeRefresh').onclick=()=>{if(baselineUI!==window.kickoffCode?.ui_revision){window.dispatchEvent(new Event('kickoff:save-drafts'));location.reload();}else check();};
 window.addEventListener('beforeunload',()=>window.dispatchEvent(new Event('kickoff:save-drafts')));check();setInterval(check,3000);window.addEventListener('focus',check);
})();
