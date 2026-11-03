# 200개 시나리오 라이브러리 · v0.7

브라우저: `/scenarios`. 불러오기와 열람은 API를 호출하지 않는다. 기존 작업은 그대로 두고 새 SQLite 작업으로 복사한다. 지정 단계 불러오기는 작성된 선행 입력을 SW 요청 편집기에 넣으며, 실행 버튼을 누를 때 처음 호출한다.

기본 테스트 벤치 `/`의 **테스트 예제·JSON**에서도 같은 200개를 분야별로 검색·선택할 수 있다. 기존 5개 회귀 예제는 별도 그룹으로 유지한다. **선택 예제 JSON 저장**은 공동/개인 합성 입력·기준안을 포함한 시나리오 원본을 저장하고, **세션/요청 불러오기**는 이 JSON을 새 작업으로 복원한다. 불러온 개인 조건·기준안은 자동으로 승인된 계획 조건에 반영하지 않는다. 형식이 잘못된 시나리오는 현재 작업을 바꾸기 전에 거부한다.

## 데이터와 정답의 의미

`src/wemeet_ai/data/scenarios.v1.jsonl`: 기본 100개 + 조건 변형 100개. 10개 분야에 기본 10개/변형 10개다. 기본 사례의 상황·결과물·작업 순서는 `data_authoring/seeds.py`의 서로 다른 100개 기획으로 작성했다. `data_authoring/build.py`가 이를 다중 턴 공동·개인 대화와 검증 조건으로 구성한다. 합성 사례이며 실제 사용자 관찰 자료나 사람이 검토한 정답이라고 주장하지 않는다. 모범안의 기본 표시는 **기준안·AI 미실행·사람 검토 전**이다.

각 사례에 상황, 목적, 난이도, 시작 모듈, 구성원, 공개/개인 대화, 개인 조건, 기준 날짜, 근거, 기대 행동·금지 행동, 비교 조건과 유지 조건이 들어 있다. 일반 사례는 다중 턴이며 짧은 사례는 `short_conversation`으로 표시한다. 반복 질문, 체크인 객체, 수정값 보존, 주차, 평가 기준 버전 관련 회귀 태그를 포함한다. 회귀 태그가 있다는 것과 실제 브라우저 회귀 검증을 통과했다는 것은 별개다.

기본/변형은 같은 사례군이다. 참고 후보 60개 사례군(120개), 개발 평가 20개(40개), 보호 평가 20개(40개)로 분리했다. 보호 평가는 참고 검색에서 제외하지만 사람이 200개 모두 열람할 수 있으므로 외부에 비공개된 블라인드 벤치마크는 아니다.

## 검토와 AI 개선

사람이 검토자 이름과 함께 승인한 REFERENCE 사례만 참고 검색에 들어간다. 피드백 GOOD/NEEDS_WORK/FAILED는 자동 승인이 아니다. DEV/TEST는 참고 승인할 수 없다. 같은 사례군을 제외하며 동일 모듈의 관련 사례를 최대 2개 선택한다. 짧은 응답 예시와 설명 원칙만 넣고 평가 정답, 전체 대화, 프로필, 기대 값은 넣지 않는다. 날짜·이름도 예시 표시로 바꾼다. 사용한 ID와 데이터 버전은 실행 메타에 기록한다. 참고가 0개여도 일반 실행은 동작한다.

baseline은 기존 정책, improved는 구체적 추천·사실/제안 구분 정책과 승인된 참고 검색을 사용한다. `prompt_version`에 설정 버전이 추가된다. 승인 풀이 0개인 비교는 프롬프트 비교이며 참고 효과의 검증이 아니다. 모델 가중치 학습이나 fine-tuning을 수행하지 않는다.

## 평가 실행

```powershell
wemeet-ai scenario-audit --output runs/scenario-audit.json
wemeet-ai scenarios --output runs/scenario-library.json
# 작업만 만들기: AI 호출 없음
wemeet-ai scenario-eval --db runs/workspace.sqlite3 --output runs/job.json
# 저장된 job ID의 다음 5개 사례 실행 (기본 baseline/improved 각 실행)
wemeet-ai scenario-eval --db runs/workspace.sqlite3 --job-id ID --live
# 전부 순차 실행 / 실패만 재시도
wemeet-ai scenario-eval --db runs/workspace.sqlite3 --job-id ID --live --all-batches
wemeet-ai scenario-eval --db runs/workspace.sqlite3 --job-id ID --live --retry-failed
```

기본 5개 **사례** 배치이므로 비교 설정이 2개면 배치당 최대 10개 조합이다. E는 구성원 답변 스크립트로 최대 6회 모델 응답을 요청한다. PLAN은 공동 계획과 첫 구성원 계획을 호출한다. 200개는 200회 HTTP를 뜻하지 않는다. 실제 HTTP/토큰과 전송·구조 보정 재시도를 별도로 기록한다.

작업은 SQLite에 저장한다. 웹의 전체 순차 실행은 로컬 서버 프로세스 안에서 계속 진행한다. 서버를 종료하면 실행도 멈춘다. 재시작 후 재개 버튼으로 이어간다. 진행 중 요청이 중단된 항목은 중복 호출 방지를 위해 20분 lease가 끝난 후 재실행한다. 일시정지는 진행 중 요청을 강제로 끊지 않고 해당 결과를 저장한 뒤 멈춘다. 인증/429는 작업을 일시정지한다. 타임아웃 등 개별 실패는 기록하고 다음 항목을 진행한다.

같은 데이터·설정·코드·모델·키 지문·승인 참고 스냅샷의 성공 조합은 재호출하지 않고 결과를 재사용한다. 실패만 재시도할 수 있다. 코드가 바뀌면 기존 작업에 다른 코드를 섞지 않고 새 작업을 만든다. 실행 중 CLI와 웹을 별도 프로세스에서 동시에 조작하지 않는다. 한 서버 인스턴스에서 동시 평가 작업은 1개다.

## 결과 판독

작성 수 / 데이터 검사 수 / 실제 API 시도 사례 수 / API 완료 사례 수 / 사람 검토 수를 따로 표시한다. API 완료가 의미적 품질 통과를 뜻하지 않는다. 구조·개인 시간·근거·프라이버시 등 코드 검사와 사람의 추천/질문 품질 검토를 나눈다. 실패 응답도 실행 ID·실제 호출량과 함께 저장한다.

`GET /ai/v1/scenarios/jobs/{id}/report`는 설정별 API 완료·코드 검사 통과·오류·p95와 기본/변형 비교 대기열을 반환한다. 출력이 다르다는 사실을 올바른 적응이라고 판정하지 않는다. 사람이 바뀌어야 할 내용과 유지 조건을 검토해야 한다. C/E/PLAN의 작성 선행 입력을 사용한 모듈 검증은 A부터 F까지 전체 서비스가 성공했다는 증거가 아니다.

## SW 연결

계약: `Scenario.schema.json`, 데이터: `src/wemeet_ai/data/scenarios.v1.jsonl`, 웹 API: `/docs`.

- `GET /ai/v1/scenarios`: 검색·필터·20개 페이지·실제 카운트
- `GET /ai/v1/scenarios/{id}`: 공개·개인 합성 입력·짝 사례·기준안·LIVE 결과
- `GET /ai/v1/scenarios/{id}/entry`: 해당 모듈의 요청 입력과 선행 입력 출처
- `POST /ai/v1/scenarios/{id}/review`, `/feedback`: 사람 검토 기록
- `POST /ai/v1/scenarios/jobs`: 평가 작업 생성, 아직 호출 없음
- `POST /ai/v1/scenarios/jobs/{id}/start`: `automatic`, `retry_failed`
- `POST /ai/v1/scenarios/jobs/{id}/pause`: 결과 보존 후 정지
- `GET /ai/v1/scenarios/jobs/{id}`, `/report`: 진행/실패/실제 사용량/비교

코어는 SQLite·FastAPI를 import하지 않는다. 시나리오 저장·평가 관리·리뷰는 어댑터 책임이다. 개발자 라이브러리는 개인 합성 데이터와 기준안을 보여주므로 운영 서비스의 권한 모델로 재사용하지 않는다. `WEMEET_SCENARIO_DB_PATH`로 라이브러리/평가 저장소만 분리할 수 있으며 일반 작업 DB는 `WEMEET_DB_PATH`다.


## v0.9 평가 프로필

A는 DIALOGUE 자연어 답변·카드 없음·CONTINUE, C는 GROUNDED 출처 목록과 미정 보존을 검사합니다. A/B/C120개는 개선 설정1종으로5개 단위·동시1개로 실행했습니다. `VERIFICATION.md`에서 API 완료/코드 통과/사람 의미 검토를 따로 확인하세요. 원본 평가를 새 검사기 결과로 덮어쓰지 않습니다.
