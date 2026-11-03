import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx

from .config import Settings
from .schemas import AIConversationEvaluation, FactExtractionResult, GoalPlanningRequest, PlanProposal, PlanStatus


@dataclass
class ProviderResult:
    payload: dict[str, Any]
    raw_response: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProviderNotConfigured(ProviderError):
    def __init__(self):
        super().__init__("AI_PROVIDER_NOT_CONFIGURED", "DEEPSEEK_API_KEY가 설정되지 않았습니다.")


class PlanningProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def generate(self, request: GoalPlanningRequest, prompt: str) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def extract_facts(self, messages: list[dict], prompt: str) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def evaluate_conversation(self, messages: list[dict], prompt: str) -> ProviderResult:
        raise NotImplementedError


class DeepSeekProvider(PlanningProvider):
    name = "deepseek"

    def __init__(self, settings: Settings):
        if not settings.deepseek_api_key:
            raise ProviderNotConfigured()
        self.model = settings.deepseek_model
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url.rstrip("/")

    def generate(self, request: GoalPlanningRequest, prompt: str) -> ProviderResult:
        return self._structured(prompt, PlanProposal.model_json_schema(), "plan_proposal")

    def extract_facts(self, messages: list[dict], prompt: str) -> ProviderResult:
        return self._structured(prompt, FactExtractionResult.model_json_schema(), "coaching_facts")

    def evaluate_conversation(self, messages: list[dict], prompt: str) -> ProviderResult:
        return self._structured(prompt, AIConversationEvaluation.model_json_schema(), "conversation_evaluation")

    def _structured(self, prompt: str, schema: dict, schema_name: str) -> ProviderResult:
        body = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": "You are a safe Korean group-goal coach. Use only supplied evidence and return only the requested JSON schema.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "schema": schema,
                }
            },
            "temperature": 0.2,
        }
        data = None
        last_error: ProviderError | None = None
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=15.0, pool=10.0)
        for attempt in range(2):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(f"{self.base_url}/responses", headers={"Authorization": f"Bearer {self.api_key}"}, json=body)
                if response.status_code == 429:
                    raise ProviderError("RATE_LIMITED", "DeepSeek rate limit exceeded")
                response.raise_for_status()
                data = response.json()
                break
            except httpx.TimeoutException as exc:
                last_error = ProviderError("TIMEOUT", "DeepSeek request timed out")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise ProviderError("UPSTREAM_ERROR", f"DeepSeek returned {exc.response.status_code}") from exc
                last_error = ProviderError("UPSTREAM_ERROR", f"DeepSeek returned {exc.response.status_code}")
            except httpx.HTTPError:
                last_error = ProviderError("NETWORK_ERROR", "Could not reach DeepSeek")
            except ProviderError as exc:
                last_error = exc
            if attempt == 0:
                time.sleep(1)
        if data is None:
            raise last_error or ProviderError("UPSTREAM_ERROR", "DeepSeek request failed")

        output_text = data.get("output_text") or self._extract_output_text(data)
        if not output_text:
            raise ProviderError("EMPTY_RESPONSE", "DeepSeek returned an empty response")
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise ProviderError("INVALID_JSON", "DeepSeek returned invalid JSON") from exc
        usage = data.get("usage") or {}
        return ProviderResult(
            payload=payload,
            raw_response=output_text,
            prompt_tokens=int(usage.get("input_tokens", 0)),
            completion_tokens=int(usage.get("output_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
        )

    @staticmethod
    def _extract_output_text(data: dict) -> str | None:
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    return content["text"]
        return None


class MockPlanningProvider(PlanningProvider):
    name = "mock"
    model = "deterministic-planner-v1"

    def extract_facts(self, messages: list[dict], prompt: str) -> ProviderResult:
        facts: list[dict] = []
        for message in messages:
            text = message["text"]
            ref = message["id"]
            lowered = text.lower()
            if any(word in text for word in ["목표", "공부", "운동", "읽", "걷", "만들", "완성"]):
                facts.append({"dimension": "goal", "value": {"summary": text[:120]}, "status": "CONFIRMED", "confidence": 0.9, "evidence_refs": [ref]})
            # "주 3회"는 실행 빈도이지 성과 목표가 아니다. 빈도 숫자가
            # metric_target을 덮거나 충돌시키지 않도록 횟수 단위는 별도로 본다.
            target = re.search(r"(\d+(?:\.\d+)?)\s*(개|문제|페이지|분|시간|단어|권|km|킬로)", text, re.IGNORECASE)
            if target:
                facts.append({"dimension": "metric_target", "value": {"target_value": float(target.group(1)), "unit": target.group(2), "metric": f"완료 {target.group(2)}"}, "status": "CONFIRMED", "confidence": 1, "evidence_refs": [ref]})
            duration = re.search(r"(\d+)\s*주", text)
            date_range = re.findall(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", text)
            if len(date_range) >= 2:
                dates = [f"{y}-{int(m):02d}-{int(d):02d}" for y, m, d in date_range[:2]]
                facts.append({"dimension": "timeline", "value": {"start_date": dates[0], "end_date": dates[1]}, "status": "CONFIRMED", "confidence": 1, "evidence_refs": [ref]})
            elif duration:
                facts.append({"dimension": "timeline", "value": {"duration_weeks": int(duration.group(1))}, "status": "PARTIAL", "confidence": 0.9, "evidence_refs": [ref]})
            frequency = re.search(r"주\s*(\d+)\s*회", text)
            if frequency:
                facts.append({"dimension": "frequency", "value": {"per_week": int(frequency.group(1))}, "status": "CONFIRMED", "confidence": 1, "evidence_refs": [ref]})
            if "인증" in text or "사진" in text or "코드 화면" in text:
                method = "코드 화면" if "코드" in text else "사진과 활동 설명" if "사진" in text else "활동 기록"
                facts.append({"dimension": "verification", "value": {"method": method}, "status": "CONFIRMED", "confidence": 0.9, "evidence_refs": [ref]})
            if any(word in text for word in ["체크인", "점검", "회고"]):
                clock = re.search(r"(\d{1,2})\s*시", text)
                day_map = {"월": "MON", "화": "TUE", "수": "WED", "목": "THU", "금": "FRI", "토": "SAT", "일": "SUN"}
                day_labels = re.findall(r"(월|화|수|목|금|토|일)(?:요일)?(?=\s|에|,|과|와|$)", text)
                days = list(dict.fromkeys(day_map[label] for label in day_labels))
                hour = int(clock.group(1)) if clock else None
                if hour is not None and any(word in text for word in ["오후", "저녁", "밤"]) and hour < 12:
                    hour += 12
                facts.append({"dimension": "checkin", "value": {"time": f"{hour:02d}:00" if hour is not None else None, "days": days, "summary": text[:100]}, "status": "CONFIRMED" if days and hour is not None else "PARTIAL", "confidence": 0.8, "evidence_refs": [ref]})
            if any(word in text for word in ["공개", "비공개", "본인만"]):
                scope = "PRIVATE" if "본인만" in text or "비공개" in text else "OBJECTIVE_ONLY"
                facts.append({"dimension": "privacy", "value": {"scope": scope}, "status": "CONFIRMED", "confidence": 0.9, "evidence_refs": [ref]})
            if any(word in text for word in ["합의", "좋아요", "동의", "그렇게 해"]):
                facts.append({"dimension": "consensus", "value": {"agreed": True}, "status": "CONFIRMED", "confidence": 0.9, "evidence_refs": [ref]})
        payload = {"facts": facts}
        raw = json.dumps(payload, ensure_ascii=False)
        return ProviderResult(payload=payload, raw_response=raw)

    def evaluate_conversation(self, messages: list[dict], prompt: str) -> ProviderResult:
        facts = self.extract_facts(messages, prompt).payload["facts"]
        grouped: dict[str, list[dict]] = {}
        for fact in facts:
            grouped.setdefault(fact["dimension"], [])
            signature = json.dumps(fact["value"], ensure_ascii=False, sort_keys=True)
            if any(json.dumps(option["value"], ensure_ascii=False, sort_keys=True) == signature for option in grouped[fact["dimension"]]):
                continue
            proposer_ids = [message.get("member_id") for message in messages if message.get("id") in fact["evidence_refs"] and message.get("member_id")]
            grouped[fact["dimension"]].append({
                "label": self._fact_label(fact["dimension"], fact["value"]),
                "value": fact["value"],
                "evidence_refs": fact["evidence_refs"],
                "proposer_member_ids": proposer_ids,
                "recommended": len(grouped[fact["dimension"]]) == 0,
            })
        titles = {
            "goal": "공동 목표", "metric_target": "측정 기준과 목표값", "timeline": "진행 기간",
            "frequency": "실행 빈도", "verification": "인증 방식", "checkin": "체크인 방식", "privacy": "공개 범위",
        }
        topics = [{"dimension": dimension, "title": titles[dimension], "options": options, "recommendation_reason": "대화에서 가장 구체적으로 제시된 안을 추천합니다."} for dimension, options in grouped.items() if dimension in titles]
        payload = {"summary": f"구성원 {len({m.get('member_id') for m in messages})}명의 대화를 바탕으로 {len(topics)}개 안건을 정리했습니다.", "topics": topics, "warnings": []}
        return ProviderResult(payload=payload, raw_response=json.dumps(payload, ensure_ascii=False))

    @staticmethod
    def _fact_label(dimension: str, value: dict) -> str:
        if dimension == "goal": return value.get("title") or value.get("summary", "공동 목표")
        if dimension == "metric_target": return f"{value.get('target_value')}{value.get('unit')} · {value.get('metric', '')}"
        if dimension == "timeline": return f"{value.get('start_date') or value.get('duration_weeks', '?')} ~ {value.get('end_date') or '기간 확인 필요'}"
        if dimension == "frequency": return f"주 {value.get('per_week')}회"
        if dimension == "verification": return value.get("method", "인증 방식")
        if dimension == "checkin": return f"{', '.join(value.get('days', []))} {value.get('time') or ''}".strip()
        if dimension == "privacy": return value.get("scope", "공개 범위")
        return str(value)

    def generate(self, request: GoalPlanningRequest, prompt: str) -> ProviderResult:
        constraints = request.known_constraints
        assert constraints.start_date and constraints.end_date
        message_ids = [message.message_id for message in request.kickoff_messages if message.message_id]
        midpoint = constraints.start_date + (constraints.end_date - constraints.start_date) / 2
        target = constraints.target_value or 1
        title = constraints.goal_title or "공동 목표"
        frequency = constraints.frequency_per_week or max(1, min(3, len(constraints.checkin_days)))
        unit = constraints.unit or "회"
        metric = constraints.metric or f"완료 {unit}"
        total_days = max(1, (constraints.end_date - constraints.start_date).days)
        weeks = max(1, (total_days + 6) // 7)
        member_todos = []
        for index, member in enumerate(request.members, start=1):
            minutes = min(member.weekly_available_minutes, 60 * frequency)
            difficulty = {"BEGINNER": "EASY", "INTERMEDIATE": "MEDIUM", "ADVANCED": "HARD"}[member.skill_level]
            member_todos.append(
                {
                    "member_id": member.member_id,
                    "rationale": f"{member.skill_level} 수준, 주 {member.weekly_available_minutes}분, {', '.join(member.preferred_times) or '합의된 시간'}을 반영했습니다.",
                    "assignment_status": "PROVISIONAL" if member.provisional else "CONFIRMED",
                    "tasks": [
                        {
                            "todo_id": f"todo-{index}",
                            "title": f"{title} · {member.nickname or f'개인 맞춤 {index}'} 실행",
                            "description": f"{metric} 목표를 위해 주 {frequency}회, 총 {minutes}분 안에서 실행합니다.",
                            "difficulty": difficulty,
                            "frequency": f"주 {frequency}회",
                            "estimated_minutes": max(10, minutes // frequency),
                            "weight": 1,
                            "completion_criteria": "활동 기록과 인증 자료를 제출한다.",
                            "evidence_refs": message_ids[:2],
                        }
                    ],
                }
            )
        payload = {
            "status": PlanStatus.draft.value,
            "shared_goal": {
                "title": title,
                "description": "킥오프 대화에서 합의한 내용을 기반으로 한 공동 실행 목표입니다.",
                "metric": metric,
                "target_value": target,
                "unit": unit,
                "start_date": constraints.start_date.isoformat(),
                "end_date": constraints.end_date.isoformat(),
            },
            "milestones": [
                {
                    "title": "중간 점검",
                    "due_date": midpoint.isoformat(),
                    "target_value": target / 2,
                    "completion_criteria": "누적 목표의 절반 이상을 완료한다.",
                    "evidence_refs": message_ids[:2],
                },
                {
                    "title": "최종 달성",
                    "due_date": constraints.end_date.isoformat(),
                    "target_value": target,
                    "completion_criteria": "공동 목표값을 달성한다.",
                    "evidence_refs": message_ids,
                },
            ],
            "member_todos": member_todos,
            "checkin_schedule": [
                {
                    "day": day,
                    "local_time": constraints.checkin_time or "20:00",
                    "timezone": constraints.timezone,
                    "purpose": "활동 기록 확인",
                }
                for day in (constraints.checkin_days or ["MON", "WED", "FRI"])
            ],
            "verification_policy": {
                "method": constraints.verification_method,
                "pass_criteria": ["계획에 맞는 활동임을 확인할 수 있다.", "제출 시각과 활동 설명이 포함되어 있다."],
                "on_insufficient_evidence": "REQUEST_RETRY",
            },
            "privacy_policy": {
                "group_visible_fields": ["completion_status", "achievement_rate", "consecutive_misses"],
                "private_fields": ["failure_reason", "ai_analysis"],
                "requires_member_consent_for_expansion": True,
            },
            "assumptions": [],
            "missing_questions": [],
            "evidence_refs": message_ids,
            "warnings": ["구성원 승인 전에는 이 계획을 활성화하지 않습니다."],
            "weekly_plans": [
                {
                    "week": week,
                    "group_focus": f"{title} {week}주차 실행",
                    "target_value": target / weeks,
                    "member_targets": {m.member_id: f"주 {frequency}회, 가용 시간 내 수행" for m in request.members},
                }
                for week in range(1, weeks + 1)
            ],
            "first_week_todos": [
                {
                    "member_id": member.member_id,
                    "scheduled_date": (constraints.start_date + timedelta(days=index - 1)).isoformat(),
                    "title": f"{title} 첫 실행",
                    "amount": f"{target / max(1, len(request.members) * frequency * weeks):.1f}{unit}",
                    "estimated_minutes": max(10, min(member.weekly_available_minutes // frequency, 120)),
                    "difficulty": {"BEGINNER": "EASY", "INTERMEDIATE": "MEDIUM", "ADVANCED": "HARD"}[member.skill_level],
                    "completion_criteria": f"{constraints.verification_method}을 제출한다.",
                    "assignment_status": "PROVISIONAL" if member.provisional else "CONFIRMED",
                    "preferred_time": member.preferred_times[0] if member.preferred_times else None,
                    "evidence_refs": message_ids[:2],
                }
                for index, member in enumerate(request.members, start=1)
            ],
            "adaptation_rules": [
                {"condition": "주간 목표의 70% 미만 달성", "adjustment": "다음 주 분량을 20% 줄이고 장애물을 다시 확인한다."},
                {"condition": "주간 목표를 초과 달성", "adjustment": "가용 시간 안에서 다음 단계 난이도를 한 단계 높인다."},
            ],
            "prompt_version": "m1-plan-v2",
            "model": self.model,
            "run_id": str(uuid4()),
        }
        raw = json.dumps(payload, ensure_ascii=False)
        return ProviderResult(payload=payload, raw_response=raw)


def build_provider(settings: Settings, force_mock: bool = False) -> PlanningProvider:
    if force_mock or settings.ai_provider == "mock":
        return MockPlanningProvider()
    if settings.ai_provider == "deepseek":
        return DeepSeekProvider(settings)
    raise ProviderError("UNSUPPORTED_PROVIDER", f"지원하지 않는 AI_PROVIDER입니다: {settings.ai_provider}")

