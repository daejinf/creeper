# SW팀 연결 순서 · Python 우선

1. README대로 wheel을 설치합니다. 소스 PYTHONPATH나 테스트용 .deps를 사용하지 않습니다.
2. `python examples/sw_m0_m1_end_to_end.py --output demo-output`을 실행합니다. 기본 실행은 유료 호출0인 fixture입니다.
3. group/의 공동 state와 private/{member_id}/의 개인 상태를 각각 저장·복원하는 부분을 SW 저장소로 교체합니다.
4. 예제의 **모의 사용자 확인** 지점은 실제 수락·수정 이벤트로 바꿉니다.
5. `AuthoredFixtureClient` 대신 서버 환경변수 키를 사용하는 `GoalAI()`를 연결합니다. 운영에서 fixture로 자동 대체하지 않습니다.
6. PUBLIC_API.md의 Result 분기와 개인 데이터 경계를 그대로 적용합니다.

## 실제 연결 최소 예시

```python
from datetime import date, datetime, timezone
from wemeet_ai.api import GoalAI, GoalSetupRequest, Member, Message, MemoryResultCache

cache = MemoryResultCache()  # 프로세스 수명 동안 공유; 운영 다중 프로세스는 SW 구현
ai = GoalAI(cache_backend=cache, cache_scope="authenticated-workspace-id")
request = GoalSetupRequest(
    action="COACH",
    members=[Member(member_id="u1", nickname="민지")],
    conversation=[Message(message_id="m1", speaker_id="u1", role="user",
        text="동아리 소개 홈페이지를 함께 만들고 싶어요.", created_at=datetime.now(timezone.utc))],
    reference_date=date.today(), timezone="Asia/Seoul")
result = ai.advance_goal_setup(request)
if result.status == "OK":
    print(result.data.reply)
    state_json = result.data.state.model_dump(mode="json")  # SW가 저장
elif result.status == "NEEDS_INPUT":
    print(result.questions)  # 반환된 state가 있으면 함께 보존
else:
    print(result.error.code, result.error.retryable)  # 키를 로그에 쓰지 않음
```

이 예시는 환경변수 키가 있으면 실제 유료 호출을 실행합니다. 공동 검토는 action=REVIEW와 저장 state를 전달합니다. 대화가 추가됐다면 최신 전체 conversation도 보냅니다. 검토 수락은 RESPOND, 명세 확정은 CONFIRM입니다.

## 호출·저장 지도

| 단계 | SW에서 전달 | AI에서 반환 | SW 저장·표시 |
|---|---|---|---|
| 공동 코칭 | 공개 대화·구성원·날짜 | reply와 state | 채팅·state |
| 공동 검토 | REVIEW + state | 근거·추천·필요 입력 | 카드·분석/초안 버전 |
| 사용자 선택 | RESPOND + 실제 actor·선택 | 갱신 state·준비도 | 사용자 선택·확인 이력 |
| 공동 확정 | CONFIRM + state | GroupGoalSpec | spec_id와 확인 조건 |
| 공동 계획 | GroupGoalSpec | GroupPlan | plan_id/revision |
| 개인 문답 | 공개 공동 계획·본인 대화/state | reply·질문·선택지·state | 본인 범위에만 저장 |
| 개인 목표 | 본인 상태·개인 대화 | contribution/profile_fields | 실제 사용자 검토 |
| 개인 계획 | 확인된 본인 조건·기여 목표 | public_plan/private_notes | 공개·개인 분리 |
| 검증·M2 기준 | 공동/개인 계획·실제 확인자 | valid/issues·baseline | 버전·과제·확인 시각 |

예제는 두 구성원 중 한 명의 개인 계획이 실패해도 다른 사람의 성공 결과를 유지합니다. 저장한 envelope를 복원해 FAILED 구성원만 재호출합니다. 서비스에서는 필요한 재시도 정책과 작업 큐를 적용하되, AI가 성공한 모든 단계를 매번 다시 실행하지 않습니다.

## SW 책임과 AI 책임

AI 코어가 정보 추출·추천·근거 검사·상태 전환·계획 생성·코드 검증을 담당합니다. SW는 로그인·그룹 소속·owner_member_id 결정·DB 트랜잭션·그룹 승인·알림·활성화를 담당합니다. 상태를 클라이언트가 임의 수정해 승인으로 사용할 수 없도록 서버에서 검증합니다.

state.revision은 동시 편집 잠금이 아닙니다. SW 저장소가 낙관적 잠금이나 트랜잭션을 적용합니다. JSON 예제는 저장 형식의 최소 예이며, 운영 DB 스키마를 강제하지 않습니다.

## 보조 HTTP·개발자 화면

Python이 아닌 서비스는 OpenAPI의 /ai/v1/goal-setup과 개별 API를 사용할 수 있습니다. 테스트 웹은 운영용 인증 서비스가 아닙니다. 화면 A~F는 모듈 결과를 보는 도구이며 SW가 같은 화면 구성을 복제할 필요는 없습니다.

[PUBLIC_API.md](PUBLIC_API.md) · [M2 참조 계약](SW_M2_CONTRACT.md) · [검증 범위](VERIFICATION.md)
