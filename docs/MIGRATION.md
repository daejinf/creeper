# 0.15.0 현재 변경

대화·실행 이력도 PostgreSQL로 통합했습니다. SQLite는 읽기 전용 과거 자료입니다. 기존 M1 함수 계약은 유지하며 선택적 `persistence` 메타데이터와 저장 어댑터를 추가했습니다. [현재 연결·복구 방법](POSTGRES_INTEGRATION.md)을 우선하세요. 아래는 과거 버전 이력입니다.

# 0.14.0: 명시적인 PostgreSQL 목표 저장

- 기존 M0/M1 독립 함수는 유지합니다. DB는 선택형 `wemeet_ai.persistence.GoalRepository`입니다.
- 기본 AI 패키지는 DB·FastAPI 없이 import할 수 있습니다. 저장이 필요하면 `[database]` 추가 의존성을 설치합니다.
- 대화·실행 이력용 SQLite를 삭제하지 않습니다. 확인된 목표·개인 기여·계획·과제는 별도 PostgreSQL에 저장합니다.
- DB 마이그레이션은 배포 묶음의 `alembic.ini`와 `migrations/`에서 실행합니다. wheel 설치가 임의로 DB를 변경하지 않습니다.
- 과거 계획은 보관하고 현재 조건과의 일치 여부를 별도로 표시합니다. 평가 기준 자동 확인은 하지 않습니다.
- [설치·관계·SW 저장·M2 참조 안내](GOAL_DATABASE.md)를 확인하세요.

# 0.13.1: 코드 변경 반영과 결과 출처

- M1 공개 함수·입출력 계약은 유지합니다. `meta.source_revision`은 선택 필드로 추가됩니다.
- `start.ps1`은 작업 폴더 소스를 읽고 Python 수정 시 자동 재시작합니다.
- 변경 전 캐시는 다음 새 실행에서 사용하지 않습니다. 기존 대화·확인값·계획은 보존합니다.
- 설치된 SW 서버는 새 wheel 설치와 프로세스 재시작이 필요합니다. [코드 변경 반영](CODE_UPDATES.md)을 참고하세요.

# 0.13.0: Timely GPT 전환

- M1-A~F 함수와 입력 계약은 유지합니다. 기존 DB를 변환하거나 삭제하지 않습니다.
- M0 직접 사용: `from wemeet_ai.m0 import TimelyClient, Runtime`.
- `DeepSeekClient` 공개 이름은 제거했습니다. 예전 import는 수정해야 합니다.
- 환경변수는 `TIMELY_API_KEY`, `TIMELY_MODEL`, `TIMELY_BASE_URL`입니다. DeepSeek 키는 인증에 사용하지 않습니다.
- 기본 모델: `openai/gpt-5.6-luna`. 기본 base URL: `https://hello.timelyai.io/api/v2/chat/bridge/openai`.
- `thinking` 옵션은 보내지 않습니다. 자연어/JSON 검증·재시도·모듈당 HTTP 4회 제한은 유지합니다.
- 새 프롬프트/검증기 버전으로 캐시를 분리합니다. 이전 성공 기록의 모델명·토큰은 고치지 않습니다.
- HTTP 402는 `INSUFFICIENT_CREDITS`, 404는 `MODEL_NOT_AVAILABLE`. 모두 자동 재시도하지 않습니다.
- 시작: 안전한 키 등록 도구 → `./start.ps1`. `.env` 파일은 자동으로 읽지 않습니다.
- 과거 실행 기록은 DeepSeek 결과입니다. 현재 GPT의 품질 검증과 분리해서 해석하세요.

---

# 0.10.1 → 0.11.0

이전 API의 호환 래퍼를 누적하지 않고 독립 모듈로 정리한 변경입니다. 배포 패키지 이름은 wemeet-ai-core, import 이름은 wemeet_ai입니다.

| 이전 | 새 사용법 |
|---|---|
| ai-core 하위 프로젝트 | 루트 pyproject.toml에서 설치 |
| GoalAI 다중 상속 객체 | wemeet_ai.m1 각 모듈의 공개 함수 |
| advance_goal_setup·순차 워크플로 | 필요한 함수만 직접 호출; 전체 조합은 examples/continue_record.py |
| LEGACY/PROACTIVE/DIALOGUE 모드 | A 자연어 대화 정책 하나 |
| E의 goal_spec + group_plan 필수 | GoalContext + 본인 입력 |
| 전체 계획만 가능한 검증 | GROUP/MEMBER/BUNDLE 범위 선택 |
| checkins 객체 혼합 | 문자열 목록; 잘못된 값은 검토 오류 |
| 작업 관리 DB·서비스형 테스트 화면 | 독립 모듈 실행 + 명시적 JSON 저장/불러오기 |
| 이전 backend/frontend/Docker | 삭제. AI 코어에 불필요한 서비스 |

이전 기록·평가 원본은 사용자 PC의 저장소 밖 보관본에 해시와 복원 안내를 남겼습니다. 새 저장소에는 개인 자료를 넣지 않습니다. 백업 위치는 로컬 `.backup-location.txt`로 확인하며 Git에는 포함되지 않습니다.

`docs/deletion-inventory.json`은 제거한 기존 파일 분류입니다. `docs/test-migration.json`은 기존 검사와 새 계약 검사의 대응·폐기 사유입니다. 검사 개수가 이전과 같다는 이유로 품질이 유지됐다고 주장하지 않습니다.

임의의 이전 JSON을 새 모델에 그대로 넣지 마세요. 공개 문맥·본인 조건을 새 계약으로 명시적으로 변환하고 실제 사용자 확인은 SW가 다시 판단해야 합니다.

## 0.11.0 → 0.12.0

기존 독립 함수와 요청 계약은 유지합니다. `continuity`의 RecordView/Artifact/PreparedInput과 순수 입력 준비 함수를 추가했습니다. 테스트 화면에만 SQLite 기록 저장을 추가했습니다. schema_version은 기록 계약 `1.0`, 패키지 버전은 `0.12.0`으로 서로 다릅니다. 과거 서비스 DB는 자동 변환하지 않습니다.
