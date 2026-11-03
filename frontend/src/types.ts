export type SkillLevel = "BEGINNER" | "INTERMEDIATE" | "ADVANCED";
export type PlanStatus = "NEEDS_CLARIFICATION" | "DRAFT" | "FAILED";
export type DimensionStatus = "MISSING" | "PARTIAL" | "CONFIRMED" | "CONFLICTED";

export interface SessionMemberSeed {
  member_id: string;
  nickname: string;
  avatar_color: string;
}

export interface SessionMember {
  nickname: string;
  avatar_color: string;
  intake_status: "PENDING" | "COMPLETED" | "PROVISIONAL";
}

export interface ChatMessage {
  id: string;
  nickname: string | null;
  author_type: "HUMAN" | "AI";
  message_type: string;
  text: string;
  created_at: string;
}

export interface ReadinessDimension {
  dimension: string;
  label: string;
  status: DimensionStatus;
  summary: string | null;
  reason: string | null;
  evidence_refs: string[];
  choices: string[];
}

export interface CoachChoice {
  dimension: string;
  option_index: number;
}

export interface CoachingState {
  dimensions: ReadinessDimension[];
  confirmed_count: number;
  partial_count: number;
  missing_count: number;
  conflicted_count: number;
  readiness_percent: number;
  next_action: "ASK_CLARIFICATION" | "SUMMARIZE" | "OFFER_OPTIONS" | "REQUEST_PRIVATE_INTAKE" | "WAIT" | "PLAN_READY";
  next_needed: string | null;
  plan_ready: boolean;
  deadline_passed: boolean;
}

export interface Todo {
  todo_id: string;
  title: string;
  description: string;
  difficulty: "EASY" | "MEDIUM" | "HARD";
  frequency: string;
  estimated_minutes: number;
  weight: number;
  completion_criteria: string;
}

export interface PlanProposal {
  status: PlanStatus;
  shared_goal: null | {
    title: string;
    description: string;
    metric: string;
    target_value: number;
    unit: string;
    start_date: string;
    end_date: string;
  };
  milestones: { title: string; due_date: string; target_value: number; completion_criteria: string }[];
  member_todos: { member_id: string; rationale: string; assignment_status: "CONFIRMED" | "PROVISIONAL"; tasks: Todo[] }[];
  weekly_plans: { week: number; group_focus: string; target_value: number; member_targets: Record<string, string> }[];
  first_week_todos: {
    member_id: string;
    scheduled_date: string;
    title: string;
    amount: string;
    estimated_minutes: number;
    difficulty: "EASY" | "MEDIUM" | "HARD";
    completion_criteria: string;
    assignment_status: "CONFIRMED" | "PROVISIONAL";
    preferred_time?: string | null;
  }[];
  adaptation_rules: { condition: string; adjustment: string }[];
  checkin_schedule: { day: string; local_time: string; timezone: string; purpose: string }[];
  missing_questions: string[];
  warnings: string[];
  evidence_refs: string[];
  prompt_version: string;
  model: string;
  run_id: string;
}

export interface PlanResponse {
  plan_id: string;
  version: number;
  approved: boolean;
  proposal: PlanProposal;
}

export interface KickoffSession {
  session_id: string;
  group_id: string;
  deadline_at: string;
  timezone: string;
  provider_mode: string;
  members: SessionMember[];
  messages: ChatMessage[];
  coaching_state: CoachingState;
  plan_draft: PlanResponse | null;
  messages_since_evaluation: number;
  evaluation_suggested: boolean;
}

export interface MessageResponse {
  user_message?: ChatMessage | null;
  ai_messages: ChatMessage[];
  coaching_state: CoachingState;
  plan_draft: PlanResponse | null;
  messages_since_evaluation: number;
  evaluation_suggested: boolean;
}

export type TopicStatus = "READY_TO_VOTE" | "NEEDS_DISCUSSION" | "NEEDS_INFORMATION" | "APPROVED" | "NO_MAJORITY" | "SUPERSEDED";
export type VoteChoice = "AGREE" | "DISAGREE" | "ABSTAIN";

export interface DecisionOption {
  option_id: string;
  label: string;
  value: Record<string, unknown>;
  evidence_refs: string[];
  proposers: string[];
  recommended: boolean;
  source: "RULE" | "HUMAN" | "AI";
}

export interface DecisionTopic {
  topic_id: string;
  dimension: string;
  title: string;
  status: TopicStatus;
  required: boolean;
  recommendation_reason: string | null;
  inferred: boolean;
  revision: number;
  approved_option_id: string | null;
  options: DecisionOption[];
  vote_summary: {
    participation_count: number;
    member_count: number;
    threshold: number;
    revealed: boolean;
    results: { nickname: string; option_id: string | null; choice: VoteChoice; reason: string | null }[];
    my_votes: Record<string, VoteChoice>;
  };
}

export interface ConversationEvaluation {
  evaluation_id: string;
  version: number;
  summary: string;
  warnings: string[];
  topics: DecisionTopic[];
  readiness: {
    required_total: number;
    approved_count: number;
    vote_needed_count: number;
    information_needed_count: number;
    intake_completed_count: number;
    member_count: number;
    plan_ready: boolean;
    blockers: string[];
  };
  messages_since_evaluation: number;
  evaluation_suggested: boolean;
  created_at: string;
  evaluation_status: "LOCAL_READY" | "AI_RUNNING" | "AI_ENRICHED" | "AI_FAILED" | "STALE";
  evaluated_message_count: number;
  error_code: string | null;
  ai_run_id: string | null;
  ai_latency_ms: number | null;
  ai_total_tokens: number | null;
}

export interface AIRun {
  id: string;
  provider: string;
  model: string;
  prompt_version: string;
  status: string;
  error_code: string | null;
  latency_ms: number;
  total_tokens: number;
  validation_errors: string[];
  raw_response: string | null;
  created_at: string;
}
