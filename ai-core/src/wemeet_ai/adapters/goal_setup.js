/* Thin view adapter. The core owns transitions, required inputs and confirmation. */
function setupPayload(action,response=null){
 const previous=state.goalSetup?structuredClone(state.goalSetup):{schema_version:'1.0',revision:0,conversation:[],members:[],coach_state:{consecutive_questions:0,answered_topics:[]},analysis:null,draft:null,source_fingerprint:'',review_history:[],group_spec:null};
 previous.analysis=state.analysis;previous.draft=state.draft;
 return {action,state:previous,...analysisPayload(),actor_member_id:owner(),response};
}
function applySetup(result){
 if(!result.data?.state)return;
 const next=result.data.state;state.goalSetup=next;
 if(next.analysis){state.analysis=next.analysis;state.analysisVersion=state.groupVersion;state.analysisRevision=state.revision;}
 if(next.draft){state.draft=next.draft;state.draftSourceVersion=next.draft_source_fingerprint&&next.draft_source_fingerprint!==next.source_fingerprint?-1:state.groupVersion;state.draftRevision=state.revision;}
 if(result.data.group_spec){state.spec=result.data.group_spec;state.specRevision=state.revision;}
 for(const meta of result.data.runs||[])if(!state.runs.some(r=>r.meta.run_id===meta.run_id))state.runs.push({operation:meta.task,owner:null,meta,status:result.error&&meta===(result.data.runs||[]).at(-1)?'FAILED':'OK',traces:[],trace_archived:true});
 if(typeof scheduleWorkspaceSave==='function')scheduleWorkspaceSave();
}
async function reviewGoalSetup(){
 requireSavedDraft();append();if(isPrivate())throw Error('공동 목표 검토는 공개 그룹 대화에서 실행해주세요. 개인 코칭은 E에서 이어갈 수 있습니다.');
 const result=await call('goal-setup',setupPayload('REVIEW'));applySetup(result);
 showStep(state.draft?'C':'B');render();
}
async function setupRespond(operation,fields,values={},text=null){
 const result=await call('goal-setup',setupPayload('RESPOND',{operation,fields,values,text}));
 applySetup(result);return result;
}
confirmDraftFields=async function(keys){
 requireSavedDraft();const result=await setupRespond('ACCEPT',keys);
 if(result.status==='OK'){changed();state.draftRevision=state.revision;notice('검토한 내용이 반영됐습니다.');}else notice(result.questions.join(' '));render();
};
prepare=async function(){
 requireSavedDraft();if(state.draftSourceVersion!==state.groupVersion)throw Error('대화가 변경됐습니다. 목표 검토하기로 최신 내용을 반영해주세요.');
 const result=await call('goal-setup',setupPayload('CONFIRM'));applySetup(result);$('prepareStatus').replaceChildren();
 if(result.status==='OK'&&result.data.group_spec){state.group=null;state.memberPlans={};state.validation=null;showStep('D');}
 else{for(const item of result.data?.needed_inputs||[])$('prepareStatus').append(card(labels[item.field]||item.label||item.field,item.reason));notice('아래 표시된 항목을 이 화면에서 확인해주세요.');}render();
};
supplement=async function(keys){
 requireSavedDraft();const fields=keys||Object.entries(state.draft?.fields||{}).filter(([k,f])=>f.value===null&&draftRequired.has(k)).map(([k])=>k);
 if(!fields.length){notice('추천이 필요한 필수 항목이 없습니다. 내용을 검토하고 수락해주세요.');return;}
 const result=await setupRespond('ALTERNATIVE',fields);if(result.status==='OK'){changed();state.draftRevision=state.revision;}render();
};
$('workflow').onclick=safe(reviewGoalSetup);$('prepare').onclick=safe(prepare);$('supplementAll').onclick=safe(()=>supplement());
