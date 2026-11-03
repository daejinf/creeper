"""Supported SW imports. Internal helpers and local test adapters are not this API."""
from .service import GoalAI
from .client import DeepSeekClient, AIError, Completion
from .contracts import (
    Result, Error, Meta, Usage, Member, Message, AnalysisRequest, ConversationAnalysis,
    DraftField, GoalSpecDraft, MemberCoachModel,
    GoalSetupRequest, GoalSetupResponse, GoalSetupState, GoalSetupOutput,
    GroupGoalSpec, GroupPlan, MemberGoalRequest, MemberCoachReply, MemberCoachState,
    MemberGoalDraft, PersonalQuestion, AnswerOption, ButtonAnswer, MemberProfile,
    ContributionGoal, MemberPlanRequest, MemberPlanDraft, ValidationRequest,
    ValidationReport, RevisionRequest, HandoffRequest, HandoffPackage, BaselineRef,
)
from .baseline import BaselineRequest, ActivityReference, build_baseline, validate_activity_reference
from .cache import ResultCache, MemoryResultCache
from .tracing import TraceSink, MemoryTraceSink, JsonlTraceSink

PUBLIC_API_VERSION='1.0'
__all__=[
    'PUBLIC_API_VERSION','GoalAI','DeepSeekClient','AIError','Completion',
    'Result','Error','Meta','Usage','Member','Message',
    'AnalysisRequest','ConversationAnalysis','DraftField','GoalSpecDraft','MemberCoachModel',
    'GoalSetupRequest','GoalSetupResponse','GoalSetupState','GoalSetupOutput',
    'GroupGoalSpec','GroupPlan','MemberGoalRequest','MemberCoachReply','MemberCoachState',
    'MemberGoalDraft','PersonalQuestion','AnswerOption','ButtonAnswer','MemberProfile',
    'ContributionGoal','MemberPlanRequest','MemberPlanDraft','ValidationRequest',
    'ValidationReport','RevisionRequest','HandoffRequest','HandoffPackage','BaselineRef',
    'BaselineRequest','ActivityReference','build_baseline','validate_activity_reference',
    'ResultCache','MemoryResultCache','TraceSink','MemoryTraceSink','JsonlTraceSink',
]
