import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, ArrowRight, Beaker, Bot, Check, ChevronRight, CircleAlert, Clock3,
  Goal, LayoutDashboard, LoaderCircle, LockKeyhole, MessageSquareText, Send,
  ShieldCheck, Sparkles, ThumbsDown, ThumbsUp, Vote, X
} from "lucide-react";
import { api } from "./api";
import { demoMembers } from "./demo";
import type {
  AIRun, ChatMessage, ConversationEvaluation, KickoffSession, MessageResponse, PlanResponse,
  SessionMemberSeed, VoteChoice
} from "./types";

type View = "kickoff" | "plan" | "lab";
type Health = { status: string; provider: string; model: string; provider_mode: string; error_code?: string };

function App() {
  const booted = useRef(false);
  const [view, setView] = useState<View>("kickoff");
  const [health, setHealth] = useState<Health | null>(null);
  const [session, setSession] = useState<KickoffSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationEvaluation, setConversationEvaluation] = useState<ConversationEvaluation | null>(null);
  const [messageCount, setMessageCount] = useState(0);
  const [evaluationSuggested, setEvaluationSuggested] = useState(false);
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [activeMemberId, setActiveMemberId] = useState(demoMembers[0].member_id);
  const [messageText, setMessageText] = useState("");
  const [intakeMember, setIntakeMember] = useState<SessionMemberSeed | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<AIRun[]>([]);
  const [labEvaluation, setLabEvaluation] = useState<Record<string, number> | null>(null);

  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    void (async () => {
      try {
        const status = await api.health();
        setHealth(status);
        const created = await api.createSession(demoMembers);
        applySession(created);
        if (status.status !== "ok") setError("대화는 저장할 수 있지만 AI 평가는 사용할 수 없습니다. DEEPSEEK_API_KEY를 확인해주세요.");
      } catch (err) {
        setError(err instanceof Error ? err.message : "코칭 세션을 시작하지 못했습니다.");
      }
    })();
  }, []);

  const applySession = (value: KickoffSession) => {
    setSession(value);
    setMessages(value.messages);
    setPlan(value.plan_draft);
    setMessageCount(value.messages_since_evaluation);
    setEvaluationSuggested(value.evaluation_suggested);
  };

  const applyResponse = (value: MessageResponse) => {
    setMessages((current) => [
      ...current,
      ...(value.user_message ? [value.user_message] : []),
      ...value.ai_messages
    ]);
    setMessageCount(value.messages_since_evaluation);
    setEvaluationSuggested(value.evaluation_suggested);
    setConversationEvaluation((current) => current ? { ...current, evaluation_status: "STALE", messages_since_evaluation: value.messages_since_evaluation } : current);
    if (value.plan_draft) setPlan(value.plan_draft);
  };

  const send = async () => {
    if (!session || loading) return;
    const text = messageText.trim();
    if (!text) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.sendMessage(session.session_id, activeMemberId, text);
      applyResponse(result);
      setMessageText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "메시지를 처리하지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const saveIntake = async (input: IntakeFormValue) => {
    if (!session || !intakeMember) return;
    setLoading(true);
    try {
      const result = await api.saveIntake(session.session_id, intakeMember.member_id, input);
      applyResponse(result);
      const refreshed = await api.getSession(session.session_id);
      setSession(refreshed);
      setMessageCount(refreshed.messages_since_evaluation);
      setEvaluationSuggested(refreshed.evaluation_suggested);
      if (refreshed.plan_draft) setPlan(refreshed.plan_draft);
      setIntakeMember(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "개인 문답을 저장하지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const evaluateConversation = async () => {
    if (!session || loading) return;
    setLoading(true); setError(null);
    try {
      const result = await api.evaluateConversation(session.session_id, activeMemberId);
      setConversationEvaluation(result); setMessageCount(0); setEvaluationSuggested(false);
      void pollEvaluation(session.session_id, activeMemberId, result.evaluation_id);
    } catch (err) { setError(err instanceof Error ? err.message : "대화를 평가하지 못했습니다."); }
    finally { setLoading(false); }
  };

  const pollEvaluation = async (sessionId: string, memberId: string, evaluationId: string) => {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      try {
        const latest = await api.getLatestEvaluation(sessionId, memberId);
        if (latest.evaluation_id !== evaluationId) return;
        setConversationEvaluation(latest);
        if (latest.evaluation_status !== "AI_RUNNING") return;
      } catch { return; }
    }
  };

  const voteTopic = async (topicId: string, optionId: string | null, choice: VoteChoice) => {
    if (!session || loading) return;
    setLoading(true); setError(null);
    try { setConversationEvaluation(await api.vote(session.session_id, topicId, activeMemberId, optionId, choice)); }
    catch (err) { setError(err instanceof Error ? err.message : "투표를 반영하지 못했습니다."); }
    finally { setLoading(false); }
  };

  const reopenTopic = async (topicId: string) => {
    if (!session || loading) return;
    setLoading(true);
    try { setConversationEvaluation(await api.reopenTopic(session.session_id, topicId)); }
    catch (err) { setError(err instanceof Error ? err.message : "재논의를 시작하지 못했습니다."); }
    finally { setLoading(false); }
  };

  const addTopicOption = async (topicId: string, label: string) => {
    if (!session || !label.trim() || loading) return;
    setLoading(true);
    try { setConversationEvaluation(await api.addTopicOption(session.session_id, topicId, activeMemberId, label.trim())); }
    catch (err) { setError(err instanceof Error ? err.message : "선택지를 추가하지 못했습니다."); }
    finally { setLoading(false); }
  };

  const generatePlan = async () => {
    if (!session || !conversationEvaluation?.readiness.plan_ready || loading) return;
    setLoading(true); setError(null);
    try { const result = await api.generateSessionPlan(session.session_id); setPlan(result); setView("plan"); }
    catch (err) { setError(err instanceof Error ? err.message : "계획 초안을 생성하지 못했습니다."); }
    finally { setLoading(false); }
  };

  const approve = async () => {
    if (!plan) return;
    setLoading(true);
    try {
      setPlan(await api.approvePlan(plan.plan_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "계획을 승인하지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const openLab = async () => {
    setView("lab");
    try { setRuns(await api.listRuns()); } catch { /* empty */ }
  };

  const runEvaluation = async () => {
    setLoading(true);
    try {
      const result = await api.evaluate(10);
      setLabEvaluation(result.metrics);
      setRuns(await api.listRuns());
    } catch (err) {
      setError(err instanceof Error ? err.message : "평가를 실행하지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const nicknameById = useMemo(
    () => Object.fromEntries(demoMembers.map((member) => [member.member_id, member.nickname])),
    []
  );

  return (
    <div className="app-shell">
      <Sidebar view={view} setView={setView} openLab={openLab} />
      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">AI GROUP GOAL STUDIO</p>
            <h1>{view === "kickoff" ? "AI 킥오프 코칭" : view === "plan" ? "적응형 계획 검토" : "AI Lab"}</h1>
          </div>
          <ProviderBadge health={health} />
        </header>
        {error && <div className="error-banner"><CircleAlert size={18} />{error}<button onClick={() => setError(null)}>닫기</button></div>}

        {view === "kickoff" && (
          <KickoffView
            session={session}
            messages={messages}
            evaluation={conversationEvaluation}
            messagesSinceEvaluation={messageCount}
            evaluationSuggested={evaluationSuggested}
            plan={plan}
            activeMemberId={activeMemberId}
            setActiveMemberId={setActiveMemberId}
            messageText={messageText}
            setMessageText={setMessageText}
            send={send}
            loading={loading}
            evaluateConversation={evaluateConversation}
            voteTopic={voteTopic}
            reopenTopic={reopenTopic}
            addTopicOption={addTopicOption}
            generatePlan={generatePlan}
            openIntake={setIntakeMember}
            showPlan={() => setView("plan")}
          />
        )}
        {view === "plan" && (
          <PlanView plan={plan} nicknameById={nicknameById} loading={loading} approve={approve} goKickoff={() => setView("kickoff")} />
        )}
        {view === "lab" && (
          <LabView runs={runs} evaluation={labEvaluation} loading={loading} runEvaluation={runEvaluation} />
        )}
      </main>
      {intakeMember && <IntakeModal member={intakeMember} loading={loading} close={() => setIntakeMember(null)} save={saveIntake} />}
    </div>
  );
}

function ProviderBadge({ health }: { health: Health | null }) {
  const mode = health?.provider_mode ?? "연결 확인 중";
  const kind = mode.includes("LIVE") ? "live" : mode.includes("MOCK") ? "mock" : "error";
  return <div className={`provider-pill ${kind}`}><span className="status-dot" />{mode}{health?.model ? ` · ${health.model}` : ""}</div>;
}

function Sidebar({ view, setView, openLab }: { view: View; setView: (view: View) => void; openLab: () => void }) {
  return (
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Sparkles size={20} /></div><div><strong>WE·MEET</strong><span>AI workspace</span></div></div>
      <nav>
        <button className="nav-muted"><LayoutDashboard size={19} /><span>대시보드</span></button>
        <p className="nav-label">COACHING</p>
        <button className={view === "kickoff" ? "active" : ""} onClick={() => setView("kickoff")}><MessageSquareText size={19} /><span>AI 킥오프</span><ChevronRight size={16} /></button>
        <button className={view === "plan" ? "active" : ""} onClick={() => setView("plan")}><Goal size={19} /><span>계획 검토</span><ChevronRight size={16} /></button>
        <p className="nav-label">QUALITY</p>
        <button className={view === "lab" ? "active" : ""} onClick={openLab}><Beaker size={19} /><span>AI Lab</span><ChevronRight size={16} /></button>
      </nav>
      <div className="privacy-card"><ShieldCheck size={20} /><div><strong>Privacy by default</strong><span>개인 문답은 그룹 대화에 공개되지 않아요.</span></div></div>
      <div className="profile"><div className="avatar">CR</div><div><strong>Creeper Team</strong><span>코칭 프로토타입</span></div></div>
    </aside>
  );
}

function KickoffView(props: {
  session: KickoffSession | null;
  messages: ChatMessage[];
  evaluation: ConversationEvaluation | null;
  messagesSinceEvaluation: number;
  evaluationSuggested: boolean;
  plan: PlanResponse | null;
  activeMemberId: string;
  setActiveMemberId: (value: string) => void;
  messageText: string;
  setMessageText: (value: string) => void;
  send: () => void;
  loading: boolean;
  evaluateConversation: () => void;
  voteTopic: (topicId: string, optionId: string | null, choice: VoteChoice) => void;
  reopenTopic: (topicId: string) => void;
  addTopicOption: (topicId: string, label: string) => void;
  generatePlan: () => void;
  openIntake: (member: SessionMemberSeed) => void;
  showPlan: () => void;
}) {
  return (
    <section className="workspace-grid coaching-layout">
      <div className="panel chat-panel">
        <div className="panel-header">
          <div><span className="section-kicker">GROUP DISCUSSION</span><h2>먼저 자유롭게 의견을 나눠보세요</h2><p>메시지는 그대로 저장되며, AI는 평가 버튼을 눌렀을 때만 전체 대화를 분석합니다.</p></div>
          <button className="evaluate-button" onClick={props.evaluateConversation} disabled={props.loading || props.messages.length <= 1}><Sparkles size={17}/>{props.evaluation ? "AI 다시 평가" : "AI 대화 평가"}</button>
        </div>
        {props.evaluationSuggested && <div className="evaluation-nudge"><Sparkles size={17}/><div><strong>새 의견이 {props.messagesSinceEvaluation}개 쌓였어요.</strong><span>자동 분석하지 않았습니다. 지금 전체 대화를 다시 평가할까요?</span></div><button onClick={props.evaluateConversation}>평가 실행</button></div>}
        <div className="chat-stream">
          {props.messages.map((message) => <ChatBubble key={message.id} message={message} />)}
          {props.loading && <div className="ai-message thinking"><div className="bot-icon"><LoaderCircle className="spin" size={18} /></div><div><strong>WE·MEET AI</strong><p>요청한 작업을 처리하고 있어요.</p></div></div>}
          {props.plan && <PlanChatCard plan={props.plan} showPlan={props.showPlan} />}
        </div>
        <div className="composer">
          <select value={props.activeMemberId} onChange={(event) => props.setActiveMemberId(event.target.value)}>
            {demoMembers.map((member) => <option value={member.member_id} key={member.member_id}>{member.nickname}</option>)}
          </select>
          <input value={props.messageText} onChange={(event) => props.setMessageText(event.target.value)} onKeyDown={(event) => event.key === "Enter" && props.send()} placeholder="목표, 기간, 방식 등 어떤 의견이든 자유롭게 입력하세요" />
          <button className="icon-button" onClick={() => props.send()} disabled={props.loading} aria-label="메시지 보내기"><Send size={18} /></button>
        </div>
      </div>
      <aside className="right-rail">
        <DecisionBoard evaluation={props.evaluation} loading={props.loading} vote={props.voteTopic} reopen={props.reopenTopic} generatePlan={props.generatePlan} />
        <IntakePanel session={props.session} open={props.openIntake} />
      </aside>
    </section>
  );
}

function ChatBubble({ message }: { message: ChatMessage }) {
  if (message.author_type === "AI") {
    const special = message.message_type === "CONFLICT_OPTIONS" ? " conflict" : "";
    return <div className={`ai-message${special}`}><div className="bot-icon"><Bot size={18} /></div><div><div className="message-meta"><strong>WE·MEET AI</strong><span>{new Date(message.created_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}</span></div><p>{message.text}</p></div></div>;
  }
  const member = demoMembers.find((item) => item.nickname === message.nickname);
  return <div className="message"><div className={`member-dot ${member?.avatar_color ?? "violet"}`}>{message.nickname?.slice(0, 1)}</div><div><div className="message-meta"><strong>{message.nickname}</strong><span>{new Date(message.created_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })}</span></div><p>{message.text}</p></div></div>;
}

function DecisionBoard({ evaluation, loading, vote, reopen, generatePlan }: { evaluation: ConversationEvaluation | null; loading: boolean; vote: (topicId: string, optionId: string | null, choice: VoteChoice) => void; reopen: (topicId: string) => void; generatePlan: () => void }) {
  if (!evaluation) return <div className="panel decision-empty"><div className="decision-empty-icon"><Vote size={24}/></div><span className="section-kicker">DECISION BOARD</span><h3>아직 평가하지 않았어요</h3><p>충분히 대화한 뒤 <b>AI 대화 평가</b>를 누르면 목표·기간·빈도·인증 등 여러 안건을 한 번에 정리합니다.</p><small>AI 추천은 자동 확정되지 않으며 구성원 투표를 거칩니다.</small></div>;
  const groups = [
    ["합의 완료", evaluation.topics.filter((t) => t.status === "APPROVED")],
    ["투표 필요", evaluation.topics.filter((t) => t.status === "READY_TO_VOTE")],
    ["논의 필요", evaluation.topics.filter((t) => ["NEEDS_DISCUSSION", "NO_MAJORITY"].includes(t.status))],
    ["정보 부족", evaluation.topics.filter((t) => t.status === "NEEDS_INFORMATION")]
  ] as const;
  return <div className="panel decision-board"><div className="decision-head"><div><span className="section-kicker">AI EVALUATION v{evaluation.version}</span><strong>의사결정 보드</strong></div><span>{evaluation.readiness.approved_count}/{evaluation.readiness.required_total} 확정</span></div><p className="evaluation-summary">{evaluation.summary}</p>
    {groups.map(([label, topics]) => topics.length > 0 && <section className="topic-group" key={label}><h4>{label}<span>{topics.length}</span></h4>{topics.map((topic) => <article className={`decision-topic ${topic.status.toLowerCase()}`} key={topic.topic_id}><div className="topic-title"><div><strong>{topic.title}</strong>{topic.inferred && <span className="inferred">AI 보완값 · 투표 필요</span>}</div><span>{topic.vote_summary.participation_count}/{topic.vote_summary.member_count}명 참여</span></div>{topic.options.length === 0 ? <p className="missing-copy">대화에서 결정 가능한 제안을 찾지 못했어요. 관련 의견을 더 나눈 뒤 다시 평가해주세요.</p> : topic.options.map((option) => <div className={`decision-option ${topic.approved_option_id === option.option_id ? "selected" : ""}`} key={option.option_id}><div className="option-copy"><strong>{option.label}</strong>{option.proposers.length > 0 && <span>제안 · {option.proposers.join(", ")}</span>}{option.recommended && <em><Sparkles size={12}/>AI 추천</em>}</div>{topic.status === "READY_TO_VOTE" || topic.status === "NEEDS_DISCUSSION" ? <div className="vote-actions"><button className={topic.vote_summary.my_votes[option.option_id] === "AGREE" ? "active agree" : ""} onClick={() => vote(topic.topic_id, option.option_id, "AGREE")} disabled={loading}><ThumbsUp size={14}/>동의</button><button className={topic.vote_summary.my_votes[option.option_id] === "DISAGREE" ? "active disagree" : ""} onClick={() => vote(topic.topic_id, option.option_id, "DISAGREE")} disabled={loading}><ThumbsDown size={14}/>부동의</button></div> : null}</div>)}{topic.recommendation_reason && <p className="recommendation"><Sparkles size={14}/>{topic.recommendation_reason}</p>}{topic.vote_summary.revealed && <div className="vote-results">{topic.vote_summary.results.map((result, index) => <span key={`${result.nickname}-${index}`}>{result.nickname} · {result.choice === "AGREE" ? "동의" : result.choice === "DISAGREE" ? "부동의" : "보류"}</span>)}</div>}{topic.status === "APPROVED" || topic.status === "NO_MAJORITY" ? <button className="reopen-button" onClick={() => reopen(topic.topic_id)}>재논의</button> : topic.options.length > 0 && <button className="abstain-button" onClick={() => vote(topic.topic_id, null, "ABSTAIN")}>이 안건 보류</button>}</article>)}</section>)}
    <div className="plan-readiness"><div><strong>{evaluation.readiness.plan_ready ? "계획 생성 준비 완료" : "아직 계획을 만들 수 없어요"}</strong>{evaluation.readiness.blockers.map((blocker) => <span key={blocker}>{blocker}</span>)}</div><button className="primary" disabled={!evaluation.readiness.plan_ready || loading} onClick={generatePlan}><Sparkles size={16}/>계획 초안 생성</button></div>
  </div>;
}

function IntakePanel({ session, open }: { session: KickoffSession | null; open: (member: SessionMemberSeed) => void }) {
  return (
    <div className="panel intake-panel">
      <div className="panel-title"><span><LockKeyhole size={18} />개인 비공개 문답</span></div>
      <p className="panel-copy">수준과 가능한 시간은 AI만 계획에 사용하며 그룹에 원문을 공개하지 않아요.</p>
      <div className="intake-members">
        {demoMembers.map((member, index) => {
          const state = session?.members[index];
          const done = state?.intake_status === "COMPLETED";
          return <button key={member.member_id} onClick={() => open(member)}><div className={`member-dot ${member.avatar_color}`}>{member.nickname.slice(0, 1)}</div><div><strong>{member.nickname}</strong><span>{done ? "문답 완료" : "입력 필요"}</span></div>{done ? <Check size={16} /> : <ChevronRight size={16} />}</button>;
        })}
      </div>
    </div>
  );
}

function PlanChatCard({ plan, showPlan }: { plan: PlanResponse; showPlan: () => void }) {
  const goal = plan.proposal.shared_goal;
  return <div className="plan-chat-card"><div><span className="section-kicker">AI-GENERATED DRAFT</span><strong>{goal?.title}</strong><p>{plan.proposal.weekly_plans.length}주 계획 · 첫 주 To-do {plan.proposal.first_week_todos.length}개</p></div><button onClick={showPlan}>상세 계획 보기<ArrowRight size={15} /></button></div>;
}

type IntakeFormValue = { skill_level: string; weekly_available_minutes: number; preferred_times: string[]; constraints: string[] };

function IntakeModal({ member, loading, close, save }: { member: SessionMemberSeed; loading: boolean; close: () => void; save: (value: IntakeFormValue) => void }) {
  const [skill, setSkill] = useState("BEGINNER");
  const [minutes, setMinutes] = useState(180);
  const [preferred, setPreferred] = useState("평일 저녁");
  const [constraint, setConstraint] = useState("");
  return <div className="modal-backdrop"><div className="intake-modal panel"><button className="modal-close" onClick={close}><X size={18} /></button><div className={`member-dot large ${member.avatar_color}`}>{member.nickname.slice(0, 1)}</div><span className="section-kicker">PRIVATE INTAKE</span><h2>{member.nickname}님의 실행 조건</h2><p>이 답변은 다른 구성원에게 공개되지 않습니다.</p><label>현재 수준<select value={skill} onChange={(e) => setSkill(e.target.value)}><option value="BEGINNER">처음 시작해요</option><option value="INTERMEDIATE">경험이 있어요</option><option value="ADVANCED">익숙해요</option></select></label><label>주간 가능 시간<input type="number" min="30" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} /><small>분</small></label><label>선호 시간<input value={preferred} onChange={(e) => setPreferred(e.target.value)} /></label><label>제약 또는 어려움<textarea value={constraint} onChange={(e) => setConstraint(e.target.value)} placeholder="선택 입력" /></label><button className="primary" disabled={loading} onClick={() => save({ skill_level: skill, weekly_available_minutes: minutes, preferred_times: [preferred], constraints: constraint ? [constraint] : [] })}><LockKeyhole size={17} />비공개로 저장</button></div></div>;
}

function PlanView({ plan, nicknameById, loading, approve, goKickoff }: { plan: PlanResponse | null; nicknameById: Record<string, string>; loading: boolean; approve: () => void; goKickoff: () => void }) {
  if (!plan || !plan.proposal.shared_goal) return <EmptyState icon={<Goal size={28} />} title="아직 계획 초안이 없어요" text="필수 안건의 투표와 개인 문답을 완료하면 계획 초안 생성 버튼이 활성화됩니다." action="대화로 돌아가기" onClick={goKickoff} />;
  const proposal = plan.proposal;
  const goal = proposal.shared_goal!;
  return <section className="plan-page"><div className="plan-hero panel"><div><span className="section-kicker">ADAPTIVE PLAN v{plan.version}</span><h2>{goal.title}</h2><p>{goal.description}</p><div className="goal-meta"><span><Clock3 size={16} />{goal.start_date} — {goal.end_date}</span><span><Activity size={16} />{goal.target_value}{goal.unit}</span><span><ShieldCheck size={16} />승인 전 비활성</span></div></div><div className={`approval-badge ${plan.approved ? "approved" : ""}`}><Check size={22} /><strong>{plan.approved ? "승인 완료" : "승인 대기"}</strong><span>AI가 대화 근거로 만든 초안</span></div></div>
    <div className="plan-section"><div className="section-heading"><div><span className="section-kicker">FIRST 7 DAYS</span><h2>바로 실행할 첫 주</h2></div></div><div className="todo-grid">{proposal.first_week_todos.map((todo) => <div className="panel todo-card" key={todo.member_id}><div className="todo-person"><div className="member-dot violet">{nicknameById[todo.member_id]?.slice(0,1)}</div><div><strong>{nicknameById[todo.member_id]}</strong><span>{todo.assignment_status === "PROVISIONAL" ? "임시 계획" : "개인 조건 반영 완료"}</span></div></div><div className="task"><div><span className={`difficulty ${todo.difficulty.toLowerCase()}`}>{todo.difficulty}</span><strong>{todo.title}</strong></div><p>{todo.scheduled_date} · {todo.amount}{todo.preferred_time ? ` · ${todo.preferred_time}` : ""}</p><div className="task-meta"><span>{todo.estimated_minutes}분</span><span>{todo.assignment_status}</span></div><small>완료 기준 · {todo.completion_criteria}</small></div></div>)}</div></div>
    <div className="plan-columns"><div className="panel timeline-card"><div className="panel-title"><span><Goal size={18}/>주차별 계획</span></div>{proposal.weekly_plans.map((week) => <div className="milestone" key={week.week}><i>{week.week}</i><div><strong>{week.group_focus}</strong><span>목표 {week.target_value}{goal.unit}</span></div></div>)}</div><aside><div className="panel approval-card"><h3>이 계획에 동의하나요?</h3><p>승인 전에는 알림이나 벌점에 적용되지 않습니다.</p><button className="primary" onClick={approve} disabled={loading || plan.approved}><Check size={18}/>{plan.approved ? "승인 완료" : "계획 승인하기"}</button></div></aside></div></section>;
}

function LabView({ runs, evaluation, loading, runEvaluation }: { runs: AIRun[]; evaluation: Record<string, number> | null; loading: boolean; runEvaluation: () => void }) {
  const [selected, setSelected] = useState<AIRun | null>(null);
  return <section className="lab-page"><div className="lab-hero panel"><div><span className="section-kicker">M0/M1 EVALUATION</span><h2>코칭과 계획 품질을 확인하세요</h2><p>mock은 테스트 전용이며 일반 실행에서는 DeepSeek 연결 실패를 숨기지 않습니다.</p></div><button className="primary compact" onClick={runEvaluation} disabled={loading}><Beaker size={18}/>10개 계획 평가</button></div>{evaluation && <div className="metric-grid">{Object.entries(evaluation).map(([key,value]) => <div className="panel metric" key={key}><span>{key.replaceAll("_"," ")}</span><strong>{key.includes("rate") || key.includes("accuracy") ? `${Math.round(value*100)}%` : `${value}ms`}</strong></div>)}</div>}<div className="lab-grid"><div className="panel run-list"><div className="panel-title"><span><Activity size={18}/>최근 AI 실행</span><span>{runs.length} runs</span></div>{runs.map((run) => <button key={run.id} className={selected?.id === run.id ? "selected" : ""} onClick={() => setSelected(run)}><span className={`run-state ${run.status.toLowerCase()}`}/><div><strong>{run.status}</strong><span>{run.prompt_version}</span></div><div><strong>{run.latency_ms}ms</strong><span>{run.provider}</span></div></button>)}</div><div className="panel inspector"><div className="panel-title"><span><Bot size={18}/>실행 검사기</span></div>{selected ? <><dl><dt>Provider</dt><dd>{selected.provider}</dd><dt>Prompt</dt><dd>{selected.prompt_version}</dd><dt>Error</dt><dd>{selected.error_code ?? "없음"}</dd></dl><pre>{selected.raw_response ?? "원본 응답 없음"}</pre></> : <div className="inspector-empty"><Bot size={34}/><p>실행 기록을 선택하면<br/>구조화 응답을 확인할 수 있어요.</p></div>}</div></div></section>;
}

function EmptyState({ icon, title, text, action, onClick }: { icon: React.ReactNode; title: string; text: string; action: string; onClick: () => void }) {
  return <div className="empty-state panel">{icon}<h2>{title}</h2><p>{text}</p><button className="primary compact" onClick={onClick}>{action}<ArrowRight size={17}/></button></div>;
}

export default App;
