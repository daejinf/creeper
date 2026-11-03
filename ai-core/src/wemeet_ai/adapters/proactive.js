/* Human decisions on model recommendations, never implicit group approval. */
function renderProposalEdit(card,proposal){
 const edit=state.proposalEdits?.[proposal.proposal_id];if(!edit)return;
 const input=el('textarea');input.value=edit.text;input.setAttribute('aria-label','추천안 수정 요청');input.placeholder='어떻게 바꾸고 싶은지 알려주세요.';input.oninput=()=>{edit.text=input.value;};
 const request=el('button','수정안 받기'),cancel=el('button','취소');
 request.onclick=safe(async()=>{const text=edit.text.trim();if(!text)return;if(!await reviewRecommendation(proposal,'DEFERRED'))return;delete state.proposalEdits[proposal.proposal_id];$('message').value='추천안 수정 요청: '+text;await runCoach();});
 cancel.onclick=()=>{delete state.proposalEdits[proposal.proposal_id];renderChat();if(typeof scheduleWorkspaceSave==='function')scheduleWorkspaceSave();};card.append(input,request,cancel);
}
function renderCoachingCards(bubble,message){
 state.proposalEdits??={};const turn=state.coachTurns?.[message.message_id];if(!turn)return;
 for(const text of turn.observations||[])bubble.append(el('p',text,'muted'));
 if(turn.missing_or_conflicting?.length)bubble.append(card('함께 확인할 내용',turn.missing_or_conflicting));
 for(const proposal of turn.proposals||[]){
  const c=card(proposal.title,proposal.rationale,proposal.status==='PENDING'?'AI 추천 · 검토 전':proposal.status);
  for(const [field,value]of Object.entries(proposal.field_updates))c.append(card(labels[field]||field,humanDraftValue(field,value)));
  if(Object.keys(proposal.field_errors||{}).length)c.append(card('제외된 항목 · 반영 전 보완 필요',Object.entries(proposal.field_errors).map(([k,v])=>(labels[k]||k)+': '+v)));const buttons=el('div',undefined,'actions');
  for(const [text,action]of [['이대로 반영','ACCEPTED'],['수정','EDIT'],['다른 안 보기','ALTERNATIVE'],['보류','DEFERRED']]){
   const button=el('button',action==='ACCEPTED'&&Object.keys(proposal.field_errors||{}).length?'유효한 항목만 반영':text);button.disabled=busy||proposal.status!=='PENDING'||(action==='EDIT'&&Boolean(state.proposalEdits[proposal.proposal_id]));
   button.onclick=safe(async()=>{
    if(action==='EDIT'){state.proposalEdits[proposal.proposal_id]={text:''};renderChat();if(typeof scheduleWorkspaceSave==='function')scheduleWorkspaceSave();}
    else if(action==='ALTERNATIVE'){if(!await reviewRecommendation(proposal,'DEFERRED'))return;$('message').value='방금 추천안 '+proposal.title+'과 다른 실행 대안을 제안해주세요.';await runCoach();}
    else await reviewRecommendation(proposal,action);
   });buttons.append(button);
  }
  c.append(buttons);if(proposal.status==='PENDING')renderProposalEdit(c,proposal);bubble.append(c);
 }
 if(turn.next_actions?.length)bubble.append(card('추천하는 다음 행동',turn.next_actions));
}
async function reviewRecommendation(proposal,decision){
 const reviewer=state.members.find(m=>m.member_id===owner());if(!reviewer)return false;
 const result=await call('coach/review',{proposal,decision,reviewer});if(result.status!=='OK')return false;
 Object.assign(proposal,result.data.proposal);state.reviewedProposals??=[];
 if(!state.reviewedProposals.some(r=>r.event_id===result.data.event_id)){
  state.reviewedProposals.push(result.data);const title=decision==='ACCEPTED'?'내 선택으로 추천안에 동의합니다':'추천안은 지금 보류합니다';
  state.groupChat.push({message_id:'decision:'+result.data.event_id,speaker_id:owner(),role:'user',text:title+': '+proposal.title+' · 이 선택은 그룹 전체 합의가 아닙니다.',created_at:new Date().toISOString()});
  // User choice is conversation evidence; only C review may edit the goal draft.
  changed('group');
 }
 render();if(typeof scheduleWorkspaceSave==='function')scheduleWorkspaceSave();return true;
}
