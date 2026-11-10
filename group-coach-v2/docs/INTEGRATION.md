# SW 연결 계약

## 두 가지 연결 방법

1. **코어 직접 사용**: `group_coach(GroupRequest)` 또는 `personal_coach(PersonalRequest)`. DB·웹 의존성 없음. SW가 입력·상태·결과를 저장하고 투표를 연결합니다.
2. **제공 저장 어댑터 사용**: `Store.execute(...)`로 입력 저장·실행 소유권·결과 저장을 함께 사용합니다. 이 어댑터는 별도 PostgreSQL 전용입니다. 코어 함수 안에서 DB를 읽지 않습니다.

두 방법을 동시에 호출하면 AI를 두 번 부릅니다. **저장 어댑터를 택했다면 execute만 호출하세요.**

```python
from wemeet_coach.persistence import Store
store = Store(database_url, recovery_directory, recovery_key)
result = store.execute(
    group_id, action_id, 'group.PROPOSE', {}, expected_revision,
)
# 개인: module='personal.TALK'/'personal.PLAN', owner=authenticated_member_id
# 자유 입력 params={'text': '주당 2시간 가능해요', 'message_id': message_id}
# 버튼 params={'answer': {'question_id': question_id, 'option_id': option_id},
#              'message_id': message_id}
```

## 입력과 결과의 경계

- 공동 함수: 공개 대화·구성원·날짜·이전 제안·반대 이유만 전달합니다.
- 개인 함수: 승인된 공개 Goal·본인 ID·본인 대화·본인 PersonalState만 전달합니다. 다른 사람의 프로필을 포함하지 않습니다.
- GroupReply: `reply`, 선택적 `proposal`, `questions`.
- PersonalReply: `reply`, 다음 요청에 그대로 넣는 `state`, 선택적 `plan`, `missing`.
- `state.question.options`는 **허용된 개인 필드**에만 값을 넣습니다. AI가 자바스크립트나 화면 동작을 실행하지 않습니다.
- 개인 PLAN의 확인 state는 모델이 다시 생성하지 않습니다. SW는 이전처럼 결과의 state를 저장하면 됩니다. 내부의 작은 계획 출력 계약은 공개 API를 바꾸지 않습니다.
- 공동 COACH에는 `approval_status`(기본 UNCONFIRMED)를 전달할 수 있습니다. 저장 어댑터는 실제 제안 상태를 넣습니다. 호출자가 임의로 APPROVED를 만들어 승인을 대신해서는 안 됩니다.
- 자연어/버튼 응답은 저장된 실제 메시지 ID를 근거로 연결합니다. AI 추천은 사용자 확인으로 간주하지 않습니다.

Result는 `status/data/needed_inputs/error/meta/persistence`입니다. JSON 변환은 `model_dump(mode='json')`. 토큰은 공급자 응답에서 얻으며 `transport_attempts`는 실제 전송 수입니다. 저장된 결과 반환의 `reused=true,new_http=0`은 신규 유료 호출이 아닙니다. 원본 메타의 토큰을 새 비용으로 합산하지 마세요.

## 저장하는 순서

1. 인증/소속 검사(SW).
2. `store.message` 또는 개인 execute의 text로 메시지 선저장.
3. 작업 ID와 expected_revision 전달. DB에서 고유 동작 ID를 확보한 프로세스만 실행.
4. 잠금 해제 후 모델 호출.
5. 유효 결과·이벤트·계획 관계를 원자적으로 저장.
6. 반환 persistence 확인. 실패한 새 단계 때문에 이전 성공 결과를 삭제하지 않음.

같은 사용자 동작은 action_id와 전체 입력을 그대로 유지합니다. RUNNING/UNKNOWN에 새 action_id를 만들어 자동 재호출하지 않습니다. 정상 COMPLETE이지만 AI 오류인 경우 오류를 검토하고 ‘새 요청’을 명시적으로 실행하세요.

## 합의·버전

`vote(group_id, proposal_id, actor, agree, issue, reason, action_id)`는 모델을 호출하지 않습니다. 반대 시 issue/reason이 필요합니다. DB도 만장일치가 아닌 APPROVED를 거부합니다. 수정은 `group.REVISE` 또는 `edit_proposal`로 새 버전을 생성합니다. 이전 찬성은 승계하지 않습니다.

같은 버전 투표 진행 중 공개 결과에는 닉네임별 표가 없습니다. all-response/revision-close 후 공개됩니다. 반대 이유에는 민감한 개인 사유를 쓰지 않도록 UI에서 안내하세요. 이름 문자열의 단순 익명화만으로 모든 서술적 재식별을 막을 수는 없습니다.

개인 계획은 `publish_personal` 전까지 비공개 초안입니다. 새 개인 조건은 본인 기존 계획만 stale로 표시합니다. 공동 조건 버전이 바뀌면 관련 개인 계획도 재검토해야 합니다. 과거 목표·계획·과제·기준은 불변 스냅샷입니다.

## 표시와 보관

| 자료 | 표시 범위 | SW 책임 |
|---|---|---|
| `view(group_id)` | 해당 모임 | 소속 검사, 공개 채팅·합의 카드 표시 |
| `personal_view(group_id, owner)` | 인증된 본인 | 서버에서 본인 ID 결정, 다른 사람 자료 접근 거부 |
| `operations`, 복구 파일 | 서비스 내부 | 일반 사용자에 직접 노출 금지 |
| publish 결과 | 본인이 확인한 모임 공개 | 기여/과제/기한만 공유 |
| baseline/plan/task | 후속 평가 담당 내부 | 정확한 ID와 접근 범위로 조회 |

테스트 HTTP 래퍼는 인증이 없는 localhost 실험입니다. URL의 owner를 바꾸는 기능을 운영 권한으로 사용하면 안 됩니다. 운영 연결 시 SW의 인증 계층을 필수로 적용하세요.

## M2에 줄 것

개인 공개 확인 후 반환된 `baseline_id`, `plan_version_id`, task의 `task_id`를 활동 기록에 저장합니다. `evaluation_context`가 세 ID의 모임·소유자 관계를 확인하고 기준 과제를 반환합니다. M2의 실제 점수/평가 로직은 이 패키지에 없습니다. 기준 수정은 새 계획·새 기준으로 남기고 이전 활동의 참조를 변경하지 마세요.

## HTTP 보조 연결

8020의 `/docs`가 현재 계약입니다. 기본 경로:

- `POST /api/groups`, `GET /api/groups/{group_id}`
- `POST .../messages`, `POST .../ai` (COACH/PROPOSE/REVISE)
- `POST .../proposals/{proposal_id}/vote`, `POST .../proposal-edit`
- `GET .../personal/{owner}`, `POST .../personal/{owner}/ai`
- `POST .../personal/{owner}/conditions`, `/edit`, `/publish`

모든 변경에 action_id 또는 message_id가 필요합니다. AI/제안 직접 수정에는 expected_revision을 포함합니다. 409는 충돌, 422는 입력 오류, 503은 저장소 장애입니다. 이 경로를 기존 8010 서버와 혼합하지 마세요.


## 0.2.0 사용자·기준 조회

새 연결은 [USER_STORAGE.md](USER_STORAGE.md)의 `ActorContext`와 범위 제한 reader를 사용하세요. 기존 Store 저수준 메서드는 신뢰된 서버 내부용입니다. 외부 사용자 입력으로 owner를 임의 지정하지 마세요. 공동 기준·개인 현재 계획 포인터 및 실패 복구도 같은 문서에 설명합니다.
