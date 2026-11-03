import type { AIRun, ConversationEvaluation, KickoffSession, MessageResponse, PlanResponse, SessionMemberSeed, VoteChoice } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) }
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : detail?.message ?? JSON.stringify(detail);
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => call<{ status: string; provider: string; model: string; provider_mode: string; error_code?: string }>("/api/v1/health"),
  createSession: (members: SessionMemberSeed[]) =>
    call<KickoffSession>("/api/v1/kickoff/sessions", {
      method: "POST",
      body: JSON.stringify({
        group_id: "creeper-coaching",
        deadline_at: "2026-11-01T23:59:00+09:00",
        timezone: "Asia/Seoul",
        members,
        initial_messages: []
      })
    }),
  sendMessage: (sessionId: string, memberId: string, text: string) =>
    call<MessageResponse>(`/api/v1/kickoff/sessions/${sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({ member_id: memberId, text })
    }),
  saveIntake: (
    sessionId: string,
    memberId: string,
    intake: { skill_level: string; weekly_available_minutes: number; preferred_times: string[]; constraints: string[] }
  ) =>
    call<MessageResponse>(`/api/v1/kickoff/sessions/${sessionId}/members/${memberId}/intake`, {
      method: "PUT",
      body: JSON.stringify(intake)
    }),
  getSession: (sessionId: string) => call<KickoffSession>(`/api/v1/kickoff/sessions/${sessionId}`),
  evaluateConversation: (sessionId: string, memberId: string) =>
    call<ConversationEvaluation>(`/api/v1/kickoff/sessions/${sessionId}/evaluations?viewer_member_id=${encodeURIComponent(memberId)}`, { method: "POST" }),
  getLatestEvaluation: (sessionId: string, memberId: string) =>
    call<ConversationEvaluation>(`/api/v1/kickoff/sessions/${sessionId}/evaluations/latest?viewer_member_id=${encodeURIComponent(memberId)}`),
  vote: (sessionId: string, topicId: string, memberId: string, optionId: string | null, choice: VoteChoice, reason?: string) =>
    call<ConversationEvaluation>(`/api/v1/kickoff/sessions/${sessionId}/topics/${topicId}/vote`, {
      method: "POST",
      body: JSON.stringify({ member_id: memberId, option_id: optionId, choice, reason: reason || null })
    }),
  reopenTopic: (sessionId: string, topicId: string) =>
    call<ConversationEvaluation>(`/api/v1/kickoff/sessions/${sessionId}/topics/${topicId}/reopen`, { method: "POST" }),
  addTopicOption: (sessionId: string, topicId: string, memberId: string, label: string) =>
    call<ConversationEvaluation>(`/api/v1/kickoff/sessions/${sessionId}/topics/${topicId}/options`, {
      method: "POST", body: JSON.stringify({ member_id: memberId, label, value: {} })
    }),
  generateSessionPlan: (sessionId: string) =>
    call<PlanResponse>(`/api/v1/kickoff/sessions/${sessionId}/plans`, {
      method: "POST",
      body: JSON.stringify({ idempotency_key: crypto.randomUUID() })
    }),
  approvePlan: (planId: string) => {
    const key = crypto.randomUUID();
    return call<PlanResponse>(`/api/v1/plans/${planId}/approve`, {
      method: "POST",
      headers: { "X-Idempotency-Key": key },
      body: JSON.stringify({ idempotency_key: key })
    });
  },
  revisePlan: (planId: string, memberId: string, reason: string) =>
    call<PlanResponse>(`/api/v1/plans/${planId}/revise`, {
      method: "POST",
      body: JSON.stringify({ rejection_feedback: [{ member_id: memberId, reason }] })
    }),
  listRuns: () => call<AIRun[]>("/api/v1/ai-runs"),
  evaluate: (limit = 10) =>
    call<{ evaluation_id: string; provider: string; case_count: number; metrics: Record<string, number> }>(
      "/api/v1/evaluations/run",
      { method: "POST", body: JSON.stringify({ limit, use_live_provider: false }) }
    )
};
