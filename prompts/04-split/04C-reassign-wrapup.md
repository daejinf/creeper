# 04 세션 C — 재배정 API, 문서 정리, 4단계 종료 검증 (수행 11~13 + 검증 절 전체)

주 실행 프롬프트는 `prompts/04-role-todos.md`다. 이 파일은 그 프롬프트의 수행·검증 절을 세션별로 나눈 것이며 내용을 바꾸거나 완화하지 않는다. 충돌하면 `prompts/04-role-todos.md`, `AGENTS.md`가 우선한다. 이 세션이 4단계의 마지막이다. 5단계는 시작하지 않는다.

## 공통 규칙 (세션 A·B·C 동일)

- 새 세션에서 시작한다. 이전 맥락은 대화가 아니라 `docs/HANDOFF.md`와 활성 문서에서 이어받는다. 작업 전 `AGENTS.md`, `prompts/README.md`, `prompts/VALIDATION.md`, `docs/HANDOFF.md`, `docs/PRODUCT.md`(D-B1·D-B3·D-B4), 실제 코드를 읽는다. 문서와 코드가 다르면 실제 코드를 근거로 보고한다.
- 선행 게이트를 확인한다: 3단계 완료 기록, D-B4 승인 기록, **HANDOFF에 `세션 A 완료`와 `세션 B 완료` 기록**. 하나라도 없으면 BLOCKED로 그 사실만 보고한다.
- 03 프롬프트와 이전 보정 프롬프트는 쓰지 않는다. 3단계를 다시 구현하거나 배정 전 인터뷰를 복원하지 않는다.
- 원본 HTML 9개, `prompts/04-role-todos.md`, `AGENTS.md` 헌법 영역, PRODUCT.md의 기존 승인 결정을 수정하지 않는다.
- LIVE 호출은 D-A8을 따른다. 모든 호출의 call_id·request_id·latency·usage와 누적 호출 수를 `docs/LIVE_TESTING.md`에 기록하고 fixture를 LIVE로 보고하지 않는다. 누적이 단일 작업 100회에 가까워지면 멈추고 사전 승인을 받는다.
- 커밋하지 않는다. 사용자가 확인한 뒤 커밋한다.
- 중단 조건(04 프롬프트 `중단 조건`)에 해당하면 추측으로 진행하지 않고 BLOCKED로 보고한다. 같은 원인의 실패가 3회 반복되면 같은 시도를 멈추고 시도·공통 원인·대안·필요한 조치를 보고한다.
- 수행 12의 공통 제약(서버에서만 AI 호출, `textContent`/템플릿 이스케이프만 등)을 유지한다.

## 이 세션의 범위

`prompts/04-role-todos.md` 수행 **11~13**과 **`검증` 절 전체**를 한다.

11. 재배정 operation: 그룹장 요청 + 대상자 동의가 있을 때만 새 assignment revision을 만드는 서버 기능과 테스트(화면 없음). 새 revision·ProjectBrief·cohort·preference submission 변경 시 관련 draft·확정 투두·숨겨진 기준·역할 맞춤 인터뷰 snapshot·coverage를 STALE 처리하고 현재 입력으로 쓰지 않는다. 역할과 무관한 확인된 시간·도구·선호는 보존할 수 있다.
12. 점검: 모든 AI 호출이 서버에서만 이뤄지고 브라우저는 Timely·키를 다루지 않으며 출력은 `textContent`/템플릿 이스케이프로만 나가는지 코드로 확인한다.
13. PRODUCT.md의 D-B4가 구현과 일치하는지 확인하고, 남은 3단계 경계 테스트를 4단계에 맞게 고친다. `docs/ARCHITECTURE.md`·`SECURITY.md`·`LIVE_TESTING.md`·`ROADMAP.md`·`HANDOFF.md`를 현재 구현과 일치시킨다. 구현이 D-B4와 다르면 D-B4를 자동 변경하지 말고 차이와 필요한 승인을 보고한다.

## 이 세션에서 지킬 조건

- 세션 A·B의 코드를 읽고 그대로 이어서 쓴다. 이미 만든 구조를 임의로 다시 설계하지 않는다.
- 이 세션에서 AI 프롬프트나 검증 코드를 고쳤다면, 마지막 수정 뒤 **최종 코드로 LIVE를 다시 실행**하고 모든 호출을 기록한다.
- 이 세션이 4단계 종료 검증이므로, 전체 결정적 테스트와 `prompts/VALIDATION.md`의 교차 단계 회귀를 한 번 실행한다.

## 이 세션의 검증

04 프롬프트 `검증` 절 **전부**를 확인한다. 특히 다음을 빠뜨리지 않는다.

- 전체 결정적 테스트, 마이그레이션 upgrade → downgrade → upgrade, 원본 HTML 해시 9개, `prompts/validate-prompts.ps1`.
- 재배정의 새 revision과 STALE 전파, 복구(새로고침·서버 재시작).
- 보안·개인정보 교차 사용자·교차 그룹·CSRF·XSS, 그룹 API 세 필드.
- 최종 코드 기준 Timely LIVE(coverage·인터뷰·투두 초안·자연어 수정)와 호출 기록.
- 1440×900 캡처(배정 안내, 인터뷰 중, 투두 초안, 수정 후, 확정 뒤)를 `artifacts/screens`에 저장하고, 실제 브라우저에서 순위 제출 → 배정 안내 → 인터뷰 → 투두 → 수정 → 확정 → 새로고침 복구 흐름을 확인한다. 디자인 눈 확인은 사용자가 한다.

## 완료 보고와 완료 조건

04 프롬프트의 `완료 보고` 형식으로 보고한다(결과 / 검증 / 달랐던 사실 / OPEN·BLOCKED / 다음 한 단계, LIVE 호출 수 포함). 4단계 공식 완료는 사용자의 디자인 눈 확인 뒤에 ROADMAP에 기록하며, 그 전까지는 "완료 대기"로 쓴다. 다음 한 단계는 `GATE-D.md`의 vision 사전 확인과 추천안 작성이다.
