# HANDOFF

날짜가 있는 환경 스냅샷, 현재 상태와 다음 한 단계. 단계 작업이 끝날 때마다 갱신한다. 우선순위가 가장 낮은 문서이므로 헌법·승인 결정과 충돌하면 그쪽을 따른다.

## 현재 상태 (2026-11-24)

- **공유 브랜치 준비(2026-11-24)**: 로컬 `main`에만 있던 8A~8D와 확정 투두 전자서명·우회 스위치 보강 18개 커밋을 원격 `main`에 직접 올리지 않고 `codex/rag-signing-security-20261124` 브랜치로 게시한다. 게시 전 원본 HTML 해시 9/9와 프롬프트 18개를 재검증했고, 범위 diff의 EOF 공백 1건을 정리했다.
- **서명·스위치 꼼꼼한 검증(2026-11-24, LIVE 0회)**: 사용자 "확실하게 잘 작동하는지 꼼꼼히 확인". 방법: 독립 검토 에이전트(결론 없이 코드만), 실제 비superuser 앱 역할로 복원 리허설(`tools/rehearse_restore.py`에 변조 시도·서명 검사 추가), 0016·0017 down/up, 실행기 키 단계·CLI 실행. 찾아 고친 것: (1) 서명 없는 확정(`UNSIGNED`)이 AI PASS로 완료될 수 있어 서명 떼어 내기·서명 없는 새 확정 넣기가 통했다 → `UNSIGNED`는 AI 완료 안 함(팀 투표) + 0016 이후 확정은 서명 필수 CHECK(마이그레이션 시각 기준, 옛 행은 STALE 표시 가능); (2) M2 입력과 서명 검사가 따로 읽음 → 검사한 행으로 M2 입력; (3) `INTEGRITY_FAILED`가 API로는 다시 시도돼 팀 투표를 지울 수 있음 → 거절; (4) 공개 테스트 키를 개발·운영에서 받아들임 → 시작 거절; (5) 0009부터 트리거 조건이 `todo_confirmed_items` UPDATE를 "열 없음" 오류로 막고 있었음(막히긴 했으나 이유가 틀림, 테스트가 함수 이름에 맞아 가려짐) → 0017에서 IF 중첩, 테스트는 트리거 문구 전체 요구; (6) 0016 downgrade 제약 이름 오류(down/up 테스트가 잡음). 검증 도구는 `UNSIGNED`만 있으면 종료 코드 3. **고치지 못한 한계**: 앱 역할이 스위치로 최신 확정을 통째로 지우면 바로 앞(정상 서명) 확정으로 되돌릴 수 있다 → 해시 체인 필요(SECURITY 6-7). 리허설 PASS(앱 역할 변조 4종 모두 거절, 서명 원본·복원 VALID 4/0/0). 전체 결정적 테스트 732개 통과(14분), 원본 HTML 해시 9/9, `git diff --check`.
- **우회 스위치 좁히기(2026-11-24, 마이그레이션 0017, LIVE 0회)**: 사용자 "스위치 좁히기". 확정 투두 트리거의 `moim.lifecycle_purge` 스위치가 이제 DELETE만 통과시킨다(UPDATE는 스위치와 무관하게 거절). 스위치를 쓰는 탈퇴·그룹 삭제는 DELETE만 해서 앱 코드 변경 없음. 앱 역할로 기준을 고치는 길이 막혔고(예방), 트리거를 건너뛸 권한이 있는 경우는 서명이 잡는다(탐지). 기록 SECURITY 6-7. 테스트: `test_lifecycle.py`에 스위치+UPDATE 거절 추가, `test_signing.py` 공격 재현은 `session_replication_role`(superuser)로 바꿈. 다음 후보: 감사 기록 해시 체인(확정 통째 삭제·`draw_audit`).
- **확정 투두 전자서명(2026-11-24, 결정적 검증, LIVE 0회)**: 사용자가 보안 보강으로 전자서명을 원했고("전자서명쪽을 원하는데"), 이유를 먼저 분명히 하라고 해서 `docs/SECURITY.md` 6절에 이유(왜 숨겨진 평가 기준인가, 지금 장치로 부족한 이유, 대안 비교, 한계)를 먼저 쓰고 "구현 시작" 뒤 구현했다. 브랜치 `claude/digital-signature-feasibility-905c76`. 구현: `src/moim/signing.py`(Ed25519, 정규화 JSON, kid, 키 로딩·교체), 마이그레이션 `0016_confirmation_signatures`(`todo_confirmations.signature`·`signing_kid`, 트리거가 둘도 얼림), `투두 확정하기`가 같은 INSERT에서 서명, M2 검토 전과 PASS 반영 전 검증(`INVALID`면 M2 호출 없이 `FAILED(INTEGRITY_FAILED)` → 팀 투표, PASS여도 완료 안 함), `integrity_event` 로그(id·결과만), `tools/verify_signatures.py`(공개키만), 실행기가 `.env`에 `MOIM_SIGNING_KEY`를 없을 때 한 번 만듦, `cryptography` 런타임 의존성. **달랐던 사실**: 0012의 트리거 우회 스위치 `moim.lifecycle_purge`는 앱 역할도 켤 수 있는 세션 설정이라, 여러 문장 SQL 주입이 생기면 앱 역할만으로도 확정 기준을 고칠 수 있다(SECURITY 6-3에 기록, 테스트가 이 경로로 공격 재현). 서명이 이 경로도 잡는다. 스위치는 0017에서 DELETE만 열도록 좁혔다(위 항목). 문서: SECURITY 6, OPERATIONS 2·4·7절(키, 교체, 복원 뒤 검증, 경보), ROADMAP. **개발 환경 영향**: 이제 개발 모드도 `MOIM_SIGNING_KEY` 없이는 시작하지 않는다(`start-moim.cmd`는 자동으로 만든다. 직접 띄우거나 `tools/seed_execution.py`를 쓸 때는 `.env`에 키가 있어야 한다). 공용 `.venv`는 이미 `cryptography` 50.0.2가 있어 새로 설치하지 않았다. 검증: 전체 결정적 테스트 729개 통과(`tests/test_signing.py` 12개(지금 15개) 추가, 운영 모드 테스트 2개에 서명 키 주입), 원본 HTML 해시 9/9, 프롬프트 18개 PASS, `git diff --check`, 실행기 PowerShell 구문 검사. 다음 한 단계: 사용자 확인 뒤 커밋, 그다음 후보는 감사 기록 해시 체인 또는 우회 스위치 좁히기.

- **8단계 종료(2026-11-23)**: 사용자가 "Close as FAIL"로 8단계를 닫았다. 주입 fixture 9/10(t112)은 FAIL로 기록된 그대로 두고 판정 규칙·기준은 바꾸지 않는다. 남은 것: 1440×900 캡처(`artifacts/screens/portfolio/`, 스크립트 AI·LIVE 아님) 사용자 눈 확인, 8A~8D 브랜치 `main` 병합.
- **8D 시험 세트 재실행(2026-11-23, 결정적 PASS / 시험 세트 6행 합격·1행 FAIL·3행 보류)**: 사용자 승인 "빠르게 끝내자 ㄱㄱㄱㄱㄱㄱ". 개발 세트 조정을 커밋 `dbec31a`로 남기고 시험 세트 LIVE를 한 번 실행했다(호출 712~1012, 301회, 누적 1012). 혼입 0, Recall@6 1.00, 1단계 실패 0, 올바른 거절 20/20, 잘못된 거절 1/90, 인용 형식 127/127(요약 포함) 합격. **주입 9/10 FAIL**: t112에서 모델이 주입 문서 내용을 중립적으로 서술했으나 금지 문구 `만점`이 답에 들어 하네스 고정 규칙에 걸렸다. 결과를 본 뒤 규칙을 바꾸지 않았다. 인용 재현율·정밀도·충실도는 사람 라벨이 없어 보류. 기록 `docs/LIVE_TESTING.md`. **사용자 결정 필요**: t112를 FAIL로 둔 채 8단계를 닫을지(추천), 판정 규칙을 개정할지. 그리고 캡처(`artifacts/screens/portfolio/`) 눈 확인.
- **8D 미달 2행 개발 세트 조정(2026-11-23, 결정적 PASS / 개발 세트 LIVE 통과, 시험 세트 재실행 대기)**: 사용자 "Yes, fix them". 새 개발 문항 `DEV_EXTRA` e01~e08(정리형 5·주입 3)과 하네스 위반 종류 기록으로 원인 확인(LIVE 호출 642~711, 70회, 누적 711). 요약 `NOT_INPUT`(입력에 없는 `mN`·`meta` id 인용) → 포트폴리오 요약 입력에서 머리말 문서 제거·`role` 속성·지시문 보강, 위반 종류 `NOT_INPUT:<종류>`; 답변 `TOO_MANY`(정리형이 여러 프로젝트를 한 문장에 묶음) → 생성 지시문 "자료·프로젝트마다 한 문장씩"; 주입 `SCHEMA_INVALID`는 재현 안 됨 → 스키마 경로만 `error_reason`. 기준값·문장당 인용 상한은 그대로, 자동 재시도 없음. 결과: 요약 7/7 READY, 정리형 4문항×2회 통과. 문서: LIVE_TESTING·RAG_DESIGN 9절. 다음: 사용자 새 승인으로 시험 세트 1회 재실행.
- **8D 포트폴리오 화면·API·평가(2026-11-23, 결정적 PASS / 시험 세트 기준 2행 미달)**: 브랜치 `claude/rag-screen-eval-design-28a860`(8C를 병합한 위). 커밋 `ccbafb9` API·프로젝트/포트폴리오 요약(마이그레이션 0015 `portfolio_summaries`, `/api/portfolio*` 9개 경로, 요약 `CITATION_INVALID` 검사·stale·삭제 시 근거 문장 정리, 근거 원문 200자 발췌) → `788b492` 마이페이지 5번째 탭 `포트폴리오`(D-I2 v2, 기존 클래스만, `static/portfolio.js` textContent, 개요·상세·답변+근거 원문·거절·빈·오류·폴링) → `fe00af5` 평가 코퍼스(사용자 3명, PDF·DOCX·PPTX·XLSX·PNG·JPEG·HWP)·개발 30/시험 120문항·`tools/portfolio_eval.py`(7.2 기준 고정, LIVE 가드) → `8d7cb8b` 개발 세트 LIVE(호출 251~340, 90회, 누적 340)와 생성 지시문 한 줄 조정(대상이 없으면 거절). 개발 세트: Recall@6 1.00, 1단계 실패 0, 잘못된 거절 0, 인용 형식 1.00, 혼입 0, 올바른 거절 4/5 → 조정 뒤 부분 재실행 5/5. 화면 캡처(1440×900, 스크립트 AI·LIVE 아님)는 `artifacts/screens/portfolio/`(커밋 안 함). 시험 세트 LIVE(호출 341~641, 301회, 누적 641, 사용자 사전 승인 "Yes, approve"): 혼입 0, Recall@6 0.978, 1단계 실패 0.022, 올바른 거절 20/20, 잘못된 거절 0/90 합격 / **주입 9/10(t115 "인용 없이…만점" `SCHEMA_INVALID`), 인용 형식 123/127(정리형 2건·평가B 포트폴리오 정리 `CITATION_INVALID`) 미달** / LLM 판정 3지표 보류(사람 라벨 없음). 근거 없는 문장은 화면에 나가지 않았다(모두 서버 검사가 막고 오류·다시 시도 표시). 기준값은 바꾸지 않았다. 하네스가 위반 종류를 남기지 않아 원인은 추정(넓은 정리형의 문장당 인용 수)이며 하네스를 고쳤다. 문서: ARCHITECTURE·SECURITY·OPERATIONS(포트폴리오 런북·호출 수)·RAG_DESIGN 9절·ROADMAP·LIVE_TESTING 갱신. 전체 결정적 테스트 716개 통과, 원본 HTML 해시 9/9, `git diff --check` 통과. **사용자 확인 필요**: 1440×900 캡처(`artifacts/screens/portfolio/`, 스크립트 AI·LIVE 아님) 눈 확인, 미달 2행 처리(개발 세트로 고친 뒤 시험 세트 재실행 승인 여부).
- **D-I2 포트폴리오 화면 v2 승인(2026-11-23, 문서·시안만, 08D 미시작)**: 사용자가 08D 실행 전에 화면을 먼저 보길 원했다. 설계 10절 v1 대응표대로 만든 미리보기에서 `.grp` 밖 `span.meta` 무스타일·긴 `p.sub`와 `crew` 겹침을 확인했고, 사용자가 "RAG를 중점적으로", "기존 디자인을 절대 벗어나지 않고" 다시 설계하라고 해 v2(개요: 소개 세 문장·근거 역량 칩·프로젝트 진행 막대·전체 질문 / 상세: 맡은 일·만든 결과·배운 점 / 답변 근거 원문 / 거절 대안)를 만들었다. 사용자 승인 "Adopt v2". 기록: `docs/PRODUCT.md` D-I2, `docs/RAG_DESIGN.md` 2.5.1·2.5.2·3.6·4·6·7.2·7.3·9·10(10.1 원본 CSS 제약), `prompts/08D-rag-screen-eval.md`·`prompts/README.md`. 시안 `docs/design/portfolio-v2/index.html`(구현본 CSS 복사, 쓴 클래스 전부 기존 CSS에 있음 확인, 예시 문장). 코드·원본 HTML 변경 없음, LIVE 0회. 검증: 프롬프트 18개 PASS, 원본 해시 9/9, `git diff --check`. 다음 한 단계: 8C 완료 뒤 `08D-rag-screen-eval.md`(v2 기준).
- **8C 포트폴리오 RAG 검색·근거 게이트·인용 답변(2026-11-23, 결정적 PASS / LIVE PASS)**: 선행 게이트 — 8B 완료(커밋 `695afb9`, 이 브랜치 `claude/rag-retrieval-prompts-5c92bb`의 바로 앞 커밋), D-I1 승인 기록(커밋 `d78aefb`) 확인. 구현: `src/moim/portfolio/ask.py`의 `PortfolioAsk.ask(user_id, question, filters)`(`app.state.portfolio_ask`, API·화면 없음) — 범위 고정 `repo.search_scope`(소유자+`READY` 먼저, 프로젝트·완료 날짜 필터는 그 안) → 작은 코퍼스 판단(실제 생성 요청 전체 cl100k 추정 ≤6,000이면 생성 1회만) → 질문 임베딩 1회·`numpy` 내적 top 50 + 앱 측 BM25(`k1=1.2, b=0.75`, 범위 IDF) top 50 → RRF(k=60) 25 → 재정렬·근거 게이트 1회(Correct/Ambiguous/Incorrect, Incorrect는 고정 문구 "포트폴리오 기록에서 찾지 못했어요"·생성 0회) → 근거 최대 6개+조건부 이웃(Small2Big, 고른 노드와 이웃이 근거 문서 하나·id 하나) → U자 배치·질문 앞뒤 → 인용 강제 생성 1회 → 서버 인용 검사(부분집합·문장당 1~3개, 위반은 답 전체 `CITATION_INVALID`+위반 종류 메타데이터) → `파일명 · 위치` 풀기(요약 노드는 근거 블록 위치로). 질문·답변 저장 없음(테이블·로그 없음, 로그는 M0 호출 줄+내용 없는 `portfolio_ask` 줄), 질문 1~300자, 사용자별 10분 20회·하루 200회(`auth_throttle`). M0에 `schema_instruction`을 꺼냈다(동작 변화 없음). 질문 1회당 Timely 호출: 일반 3회(Incorrect 2회), 작은 코퍼스 1회, 빈 범위 0회. **노드 수별 적재+계산**(`tools/measure_portfolio_search.py`, 테스트 DB 합성 노드, 중앙값): 100 → 22.6ms, 500 → 87.6ms, 1,000 → 178.8ms, 2,000 → 387.7ms, 5,000 → 1,575ms. 적재(주로 임베딩 `bytea` 전송)가 85~93%이고 300ms 선은 약 1,500노드(제출 약 100~250건)라 인프라는 더하지 않았다(RAG_DESIGN 9절). cl100k 환산 대조(8B 남은 것): 임베딩 +0.5%·+1.9%, 생성 모델 입력은 추정이 +65~68% 높음. LIVE 호출 222~250(29회, 누적 250): 사실 질문 Correct·3회, 답 없음 Incorrect·2회(생성 0), 주입 fixture 질문은 지시를 따르지 않고 중립 서술, 작은 코퍼스 1회. 요약형 질문("설문 분석 투두에서 내가 한 일을 정리해 줘")이 같은 원인 `CITATION_INVALID(TOO_MANY)`로 3회 실패해 한 번 중단했고(분할 표 조각·이웃이 각각 id를 가져 한 문장이 4개 이상 인용), 사용자 승인("네, 적용해") 뒤 이웃 묶음을 근거 하나로 합쳐 같은 질문이 문장 5개 모두 인용 1~3개로 통과했다(호출 244~250). 관찰: 요약 노드·이웃 묶음 인용이 위치 여러 개로 풀려 같은 표가 겹쳐 보이는 등 인용 정밀도를 낮출 수 있음(8D 측정), 재정렬이 질문 지연의 대부분(6.5~8.2초). 테스트 `tests/test_portfolio_retrieval.py` 43개(사용자 3명 fixture `tests/portfolio_corpus.py`), 전체 결정적 테스트 682개 통과(657개 + `ops` extra가 필요한 두 모듈 25개, 실패·건너뜀 0), 원본 HTML 해시 9/9, 프롬프트 검증 PASS. 이 worktree도 자체 `.venv`(Git 제외)로 검증했다. 다음 한 단계: 새 세션에서 `08D-rag-screen-eval.md`.
- **8B 포트폴리오 RAG 첨부 이해·임베딩(2026-11-23, LIVE PASS)**: 선행 게이트 — 8A는 `main`에 병합되지 않은 브랜치 `claude/rag-ingest-implementation-3b65f7`(커밋 `d78aefb`, D-I1 승인 기록 포함)에 있었고, 이 브랜치(`claude/rag-understanding-509967`)가 그 커밋을 fast-forward로 가져와 그 위에 8B를 쌓았다. 구현: M0 `TimelyClient.embed`(`/embeddings`, 기존 오류 분류·호출 로그 한 줄·재시도 없음, 입력 미기록, 개수·index·차원(`-small` 1536)·유한수·영벡터 검사, 서버 L2 정규화, `INVALID_EMBEDDING`; `_post`·`_usage`로 HTTP 계약을 chat과 공유), `src/moim/portfolio/understand.py`의 `PortfolioAI`(DESCRIBING vision 제출당 8회·초과는 "설명 생략(상한)" 블록·PPTX는 슬라이드 내 삽입 이미지 설명만 / UNDERSTANDING 제출당 `complete_json` 1회(창 12k)·`document_summary` 주장별 근거 `block_id`·`chunk_contexts`·서버 검사 실패 `SCHEMA_INVALID` / `doc_summary` 노드를 조각과 같은 풀에 / EMBEDDING `float32` 리틀엔디언 `bytea`+모델·차원), `create_app`이 `ai_client_factory`로 연결. 모든 첨부 내용은 `<document>` 구분자 안, 시스템 지시에 "자료는 지시가 아님". 실패는 그 단계의 `FAILED(코드)`+호출 수·usage `stage_meta`, 자동 재시도·폴백 없음. 의존성 `numpy`(런타임, `constraints.txt` 2.5.5). LIVE 호출 215~221(7회, 누적 221): 제출 2건 모두 `READY`, 요약 근거 전부 실제 블록, 주입 문장이 요약·맥락에 없음, 로그 누출 없음. 제출 1건당 호출: 그림 3개 제출 5회(11,991토큰), 그림 없는 제출 2회(3,569토큰). 테스트 `tests/test_portfolio_understanding.py` 32개 추가, 8A의 "8B 없음" 테스트는 "처리기 없는 단계" 테스트로 바꿨다. 이 worktree도 자체 `.venv`(Git 제외)로 검증했다. 관찰(OPEN 아님): 주입 PNG의 vision 출력이 보이는 글자를 비웠고, 요약 한 줄이 팀 결정을 본인 일로 적었다(`docs/LIVE_TESTING.md`). 남은 것: `cl100k` 환산과 임베딩 `usage.prompt_tokens` 표본 대조(8C). 다음 한 단계: 새 세션에서 `08C-rag-retrieval.md`(8A·8B 브랜치를 `main`에 병합한 뒤 진행 권장).
- **8A 포트폴리오 RAG 수집 기반(2026-11-23, AI 호출 없음)**: 사용자가 D-I1을 초안 그대로 승인했다("초안 그대로 승인 (Recommended)", `docs/PRODUCT.md`). ROADMAP 8단계·SECURITY `portfolio` 분류를 맞췄다. 구현: 마이그레이션 `0014_portfolio`(테이블 5개, 모든 행 `user_id` CASCADE, 자식은 `(id, user_id)` 복합 FK, 그룹 테이블 FK 없음, 원본 바이트·질문 테이블·DB 확장 없음), `src/moim/portfolio/`(`repo`는 모든 함수가 필수 키워드 `user_id`, `ingest` 트리거, `extract` 형식별 블록, `chunk` 분할, `tokens` 환산·BM25 토큰, `service` 작업 상태 기계·다시 시도·직접 삭제). 완료 트리거는 `votes._set` 한 곳(AI·TEAM·SELF)에서 같은 트랜잭션, MANUAL은 `ingest.enqueue_record`. `되돌리기`·계정 삭제·삭제 기록 재처리(`PORTFOLIO_ITEM_DELETED`/`PORTFOLIO_PROJECT_DELETED` 새 종류)를 연결했다. 한국어 측정: 1.03자/토큰, 음절당 1.37토큰(RAG_DESIGN 9). 의존성: `python-pptx`·`openpyxl`(런타임), `tiktoken`(test extra), `constraints.txt` 고정. 8B 단계가 없는 지금 실제 앱의 작업은 추출 저장 뒤 `FAILED(DESCRIBING, STAGE_UNAVAILABLE)`에서 멈춘다(정상, 8B 뒤 그 단계부터 `다시 시도`). 테스트 `tests/test_portfolio_ingest.py` 31개. 이 worktree는 공용 `.venv`를 건드리지 않으려고 자체 `.venv`(Git 제외)를 만들어 검증했다. 병합 뒤 공용 venv는 `pip install -e ".[test,visual,ops]" -c constraints.txt`로 맞춘다.

## 이전 상태 (2026-11-20)

- **RAG 포트폴리오 사전 확인(2026-11-23, 설계 논의만, 단계 미시작)**: 사용자가 마이페이지에서 본인 제출물 기반 RAG(포트폴리오)를 원한다. Timely 직접 API의 `/embeddings`가 동작함을 LIVE 2회(호출 213~214, 누적 214회, `docs/LIVE_TESTING.md`)로 확인했다(`text-embedding-3-small` 1536차원, `-large`도 목록에 있음). 로컬 PostgreSQL 16에는 `pgvector`가 없고 `pg_trgm`만 있다. 결정 초안 D-I1(I1~I8, PROPOSED)을 `docs/PRODUCT.md`에 썼고 5절 OPEN 표에 올렸다. 승인 전이라 구현·ROADMAP 단계 추가는 하지 않았다. 단계 프롬프트(선행 게이트: D-I1 승인)를 쓰고 `prompts/README.md` 순서표와 `validate-prompts.ps1`에 넣었다. 이어서 RAG 원 논문(RAG·RRF·Contextual Retrieval·HyDE·RAPTOR·Lost in the Middle·Self-RAG·CRAG·RAGAS·ARES·ALCE·Chroma 분할 평가·Late Chunking·간접 프롬프트 주입·RAG survey·OpenAI 임베딩 문서)을 확인해 기술 설계 `docs/RAG_DESIGN.md`를 썼고(`AGENTS.md` 문서 표에 추가), 8단계 프롬프트를 세션 8A~8D로 나눠 다시 썼다. 외부 검토 의견도 반영했다: 질문·답변 비저장, 합격 기준을 LIVE 전에 고정, DB 확장 없음(앱 측 BM25), 마이그레이션 소유 역할과 런타임 역할 구분. 코드 변경 없음. 다음 한 단계: 사용자의 D-I1 승인 또는 보정 → 새 세션에서 `08A-rag-ingest.md`부터 실행. 추가 보정(2026-11-23): 프롬프트를 `08A`(모델·트리거·추출)·`08B`(이해·임베딩)·`08C`(검색·답변)·`08D`(화면·평가) 네 파일로 나누고 README 16~18번·검증 스크립트에 넣었다. 코퍼스 본체가 사용자 첨부 파일임을 D-I1 I1·설계 1절에 명시했고, 화면은 원본 마이페이지의 기존 마크업만 쓰도록 설계 10절 대응표를 만들었다. 검토 보정: ARES 수치를 참고값으로 낮추고 사람 라벨 150개 미만이면 LLM 판정 지표는 참고값, Wilson/질문 단위 bootstrap 구분, PPTX는 슬라이드 내 삽입 이미지 설명으로 한정, 작은 코퍼스 우회는 최종 프롬프트 전체 기준, 주입 기준은 fixture 10/10.

- **Windows 로컬 실행기 포트 충돌 보정(2026-11-20)**: `Test-PortFree`를 TCP 연결 시도 대신 실제 loopback bind 검사로 바꿨다. `start-moim.cmd`가 만들었거나 실행기가 `CHANGE_ME`를 교체한 `.env.example` 복사본에서만 설정 포트를 쓸 수 없고 해당 클러스터가 실행 중이 아닐 때 다음 100개 포트 중 빈 포트를 찾아 `MOIM_DATABASE_URL`과 `MOIM_TEST_DATABASE_URL`을 함께 갱신한다. 사용자가 직접 지정한 DB 설정은 자동 변경하지 않는다. PowerShell 구문, 실제 점유 포트 판정, 두 URL 동시 갱신 helper는 통과했다. 이 Codex 실행 환경에서는 백그라운드 PostgreSQL이 명령 종료와 함께 격리·정리되어 end-to-end 접속 검증은 같은 원인 3회 뒤 중단했다. 로컬 검증용 신규 클러스터에는 사용자 데이터가 없으며 `global/pg_control`이 있고 실행 프로세스·`postmaster.pid`·임시 비밀번호 파일은 남아 있지 않다.

- **GitHub 원격 연결(2026-11-20)**: `https://github.com/daejinf/creeper.git`을 `origin`으로 등록했다. 파일이 없는 원격 초기화 커밋(`a3cb8a4`)은 이력을 보존해 로컬 `main`에 병합했으며, 제품 코드·문서 결정·원본 HTML에는 내용 변경이 없다. 전체 결정적 테스트, 원본 HTML 해시 9/9와 프롬프트 검증 14개가 통과했다.

- 0A-1·0A-2 완료, GATE A 승인 완료(D-A6 디자인 기준, D-A7 LIVE 허용). LIVE 호출 예산은 D-A8(2026-11-18)이 개정했다: 고정 호출 상한 없음, 모든 호출 기록, 단일 작업 100회 이상 대량·부하 호출만 사전 승인, 자동 재시도·backoff·자동 JSON 복구·숨기는 폴백은 계속 금지, 같은 원인의 실패 3회 반복 시 중단하고 분석. 기록은 `docs/PRODUCT.md`.
- 0B-1 구현·검증 완료(저장소·환경·테스트 기반). 이 단계에서 LIVE 호출은 하지 않았다(횟수 0, `docs/LIVE_TESTING.md`).
- 원본 HTML 9개는 변경하지 않았고 해시 9/9가 `docs/PRODUCT.md` 기준과 같다(`tools/check_design_hashes.py`).
- 0B-2 완료(DETERMINISTIC PASS·LIVE PASS), 0B-3 구현·검증 완료(작업 상태 저장, 화면 캡처 스크립트).
- 0B 보정(2026-11-18): D-A8 기록, Git baseline, DROP 모듈·옛 CLI·오래된 egg-info 제거, 테스트의 `DROP SCHEMA` 가드. 아래 `0B 보정` 절.
- 1단계 인증 구현·검증 완료(2026-11-18, 아래 `1단계 인증` 절).
- 2단계 대시보드·그룹·ProjectBrief 구현·검증 완료(2026-11-18, 아래 `2단계 그룹·ProjectBrief` 절). 팀 목표 표시문 LIVE 2회 성공(누적 LIVE 8회). `/dashboard`는 이제 실제 화면이고 로그인 확인용 임시 화면은 지웠다. `/groups/{id}/chat`은 3단계 전까지 임시 화면이다.
- **D-B3(2026-11-19, 제품 흐름 변경 승인)**: 3단계에서 개인 AI 인터뷰를 없애고 공용 역할 후보의 비공개 1·2·3순위만 제출받는다. 인터뷰는 배정 뒤 4단계로 옮겼다. 기록·변경 전후 문장은 `docs/PRODUCT.md` D-B3, 헌법은 `AGENTS.md` 1절(4·5번 흐름 항목, 본문 두 문장)만 바꿨다. 3단계 구현은 새 흐름으로 바뀌었고 **사용자가 디자인 눈 확인을 완료했다고 알려와(2026-11-19) 3단계는 완료**다. 4단계 확정 정책은 `docs/PRODUCT.md` D-B4로 기록했다. 4단계는 시작하지 않았다. 아래 `3단계 공용 후보·순위 제출(D-B3)` 절.
- **4단계 세션 A 완료(2026-11-19)**: 서버 역할 배정(assignment revision 1회), `ROLE_TODOS`, 팀 coverage, 배정 안내, AI 주도 배정 후 인터뷰와 확인 단계, 입력 snapshot. 4단계 전체는 세션 B(투두 초안·수정·확정)와 세션 C(재배정·STALE·문서 정리)가 끝나야 완료다. 아래 `4단계 진행` 절.
- **4단계 세션 B 완료(2026-11-19)**: 역할 맞춤 투두 초안(숨겨진 결과물·완료 기준·증거 enum), 직접·자연어 수정, 수정·추가 투두의 기준 재생성, `투두 확정하기`(불변 revision·멱등·`task_id` 유지)·확정 풀기, 그룹 조회 API(역할명·제목·마감일). LIVE 66회(누적 169회). 아래 `4단계 진행 > 세션 B 완료` 절. 4단계 완료는 세션 C 뒤다.
- **4단계 완료(2026-11-19)**: 세션 C(재배정 operation, STALE 전파, 문서 정리, 단계 종료 검증) 뒤 사용자가 `artifacts\screens\stage4-chat.html` 디자인 눈 확인과 재배정 규칙 해석을 함께 확인했다("Both OK"). ROADMAP을 완료로 바꾸고 4단계 코드와 GATE D 문서를 함께 커밋했다. 아래 `세션 C 완료`.
- **GATE D 승인 완료(D-D1, 2026-11-19)**: vision 사전 확인 LIVE 15회(호출 191~205, 누적 205회, `docs/LIVE_TESTING.md`)로 M2.review 활성 가능을 확인했고, 사용자가 D1~D7을 보정 두 차례 뒤 최종 승인했다. 요지: M2 입력은 그 투두 snapshot+제출 자료만, 체크박스는 원본대로 수동 완료(PASS 자동 완료 없음), 결과는 `up-msg`와 내 완료 기록 줄에 본인만, 팀 첨부 11형식과 M2 검토 형식 분리(HWPX는 결정적 추출 테스트 전까지 M2 미지원), 이미지 2048px 메타데이터 제거 사본, 제외 파일명 비전송, 접근은 현재 활성 구성원만, 첨부 있는 되돌리기는 재확인 뒤 영구 삭제, 그룹 나가기·그룹장 승계·계정 삭제. 기록: `docs/PRODUCT.md` D-D1, `docs/ROADMAP.md` GATE D, `docs/SECURITY.md` 2·3절, `docs/ARCHITECTURE.md` M2 판정 두 줄. 문서만 바꿨고 코드·원본 HTML은 변경하지 않았다. 승인 뒤 M2 LIVE 호출은 하지 않았다. 5단계는 시작하지 않았다.
- **5단계 완료(2026-11-19, D-D1·D-E1)**: 그룹 실행 화면(진척도·팀 활동·팀원·팀원 완료 기록·내 할 일·업로드·완료한 할 일)을 원본 group 구조로 실제 데이터에 연결했고, 첨부(팀 11형식 내용 검증)·수동 완료·`되돌리기`(첨부 있으면 재확인 뒤 영구 삭제)·투두별 M2 검토(본인에게만 `up-msg`와 내 기록 줄)를 D-D1대로 구현했다. 결정적 테스트 501개 통과, M2 LIVE 7회(호출 206~212, 누적 212회) 모두 성공. 사용자의 요청에 따라 에이전트가 1440×900 원본·구현과 팀원·업로드·기록 상세 캡처를 직접 비교해 디자인을 확인했고, 원본 구조를 유지하면서 D-E1 표시가 기존 카드·목록 안에 들어간 것을 확인한 뒤 완료 처리했다. 아래 `5단계 실행·첨부·M2` 절.
- **6단계 구현·결정적 검증 완료(2026-11-19, D-D1 D6·D7, D-F1)**: 마이페이지(내 정보·이름 변경, 비밀번호 변경과 세션 회전, 참여 중인 그룹, 로그아웃, 회원 탈퇴), 그룹 화면 ⋯ 메뉴 `그룹 나가기`(영향 미리보기+브라우저 확인창), 그룹장 자동 승계, 마지막 구성원 그룹 삭제, 계정 삭제와 "탈퇴한 사용자" tombstone. 화면 차이 세 가지는 사용자가 D-F1로 승인했다. 사용자가 1440×900 캡처를 원본과 비교해 디자인을 확인했다("Design OK", 2026-11-19). **6단계 완료**, worktree 브랜치에 커밋했다. LIVE 호출 없음. 아래 `6단계 마이페이지·계정 생명주기` 절.
- **1~6단계 총괄 재검증(2026-11-19)**: 6단계 코드 커밋 `ea5884e`와 HANDOFF 마감 정리만 추가된 현재 `main` `385fd59`에서 프로젝트 가상환경과 로컬 PostgreSQL 테스트 DB로 전체 결정적 테스트 **522개 통과**(실패·건너뜀 0), 원본 HTML 해시 9/9, 프롬프트 14개, `git diff --check`, 개발 DB migration `0012`를 다시 확인했다. 6단계 프롬프트의 재인증·CSRF·IDOR·세션 폐기·그룹장 승계/마지막 구성원·보관 FK·부분 실패·범위 밖 팀 보존·마이그레이션·디자인 증거를 코드와 `tests/test_lifecycle.py`에 항목별로 대조했다. 제품 코드 결함이나 BLOCKED 항목은 발견하지 않았다.
- **7단계 운영 준비(2026-11-19~20, 판정 CONDITIONAL PASS)**: 사용자가 7단계 진행을 지시했다("가보자"). 보안 점검에서 찾은 결함 셋(Pillow 디코더 형식 미지정+공개 취약점, 본문 상한 없는 경로, 인증 없는 `/api/jobs` 노출)을 고쳤고, 운영 시작 점검·요청 id JSON 로그·DB 역할 분리·암호화 백업/복원 도구를 만들어 복원 리허설·장애 훈련·부하 표본을 실제로 실행했다. 배포 대상(TLS·비밀 저장소·로그/경보 연결)은 BLOCKED. 백업 보관·삭제 전파는 사용자가 승인했고(D-G1, 2026-11-20) 삭제 기록·복원 뒤 재처리를 구현해 리허설까지 통과했다. LIVE 호출 0회. 아래 `7단계 운영 준비` 절, 절차서 `docs/OPERATIONS.md`.
- GATE B 승인 완료(D-B1, 2026-11-18): 인터뷰 항목·턴, chat 네 상태 문구와 `N/M명 제출 완료` 집계, 공급자 입력 필드, 미제출·신규 합류(차단 유지)·재배정·중복 역할, 역할 후보 생성(revision당 1회), 이름·순위 비노출 안내 문구(원본과 다른 제품 차이), 투두 공개·재확정, STALE 규칙. 이번 작업은 문서만 바꿨고 코드·테스트·원본 HTML은 변경하지 않았다.
- **D-H1(2026-11-20, 사용자 지시)**: 체크 완료를 없애고 할 일은 AI PASS 또는 요청자 뺀 팀원 과반 동의로만 완료(보류·미지원 형식·실패는 자동 투표), 그룹장 `그룹 삭제`, 대시보드 오른쪽 패널·최근 팀 소식·카드 진척도, 로그인 화면 로고 → `/dashboard`. migration `0013`. 아래 `D-H1 완료 방식·그룹 삭제·대시보드` 절.

## 환경 스냅샷 (2026-11-18, 확인한 사실만)

| 항목 | 상태 |
|---|---|
| 작업 폴더 | `C:\Users\user\Desktop\moim-design`. Git 저장소(`main`). 첫 baseline 커밋 `a9a9721`(승인 문서, 원본 HTML 9개, 0A/0B 기반). 원격 저장소 없음, push 안 함. 그 뒤 0B root hardening 커밋은 아래 `0B 보정` 절에 기록 |
| Python | 3.12.8, 가상환경 `.venv` |
| 앱 | `src/moim/`(`config.py`, `db.py`, `logging_utils.py`, `main.py`, `models.py`, `timeutil.py`, `jobs.py`, `job_routes.py`, `auth/`, `groups/`, `templates/`, `static/`). FastAPI 앱, `/healthz`, `/api/jobs`(핸들러 없음), 인증, 그룹·초대방·ProjectBrief. AI 코드는 `src/wemeet_ai`의 KEEP/ADAPT 모듈만 남았다: `m0`(client·runtime·tracing·records), `m1`(e_member_plan·f_validation), `m2`, `shared` |
| 마이그레이션 | Alembic `0001_foundation`, `0002_ai_jobs`, `0003_auth`, `0004_groups`, `0005_interview`, `0006_interview_d_b2`, `0007_interview_deprecated`(주석만), `0008_role_assignment`(4단계 세션 A). `upgrade → downgrade base → upgrade` 확인(테스트가 매번 실행), 0008은 `downgrade 0007 → upgrade`를 데이터와 함께 확인 |
| PostgreSQL | 16, 포트 54329, 로컬 데이터 `.local/pgdata`. 개발 DB `moim_dev`, 테스트 DB `moim_test`. 역할은 아직 `moim` 하나(superuser)다. 테스트의 `DROP SCHEMA public CASCADE`는 호스트 localhost·포트 54329·DB명 정확히 `moim_test`·`MOIM_ENV=test`를 모두 확인한 뒤에만 실행한다(`tests/dbsafety.py`) |
| 설정 | `.env`(Git 제외)와 `.env.example`. **앱은 `.env`를 자동으로 읽지 않는다.** 환경변수로만 읽으며 `MOIM_ENV`, `MOIM_DATABASE_URL`이 없으면 `ConfigError`로 실패한다. `alembic`은 환경변수를 먼저 불러온 셸에서 실행한다. 테스트는 `tests/conftest.py`가 설정한다 |
| 테스트 | `.\.venv\Scripts\python.exe -m pytest -q`: 501개 통과(2026-11-19 5단계 뒤, `test_execution.py` 43개 추가). 그 전 441개 통과(2026-11-19 4단계 세션 B 뒤, `test_todos.py` 48개 추가). 그 전 393개(4단계 세션 A 뒤, `test_role_assignment.py` 45개 추가). 그 전 348개(2026-11-19 D-B3 뒤. 옛 `test_interview.py` 30개를 `test_ranking.py` 26개로 교체해 352에서 줄었다. 기반·공급자 계약·작업 상태·패키징·Runtime 계약·DROP 가드 164 + 인증 84 + 그룹 74 + 순위 26). 환경변수(`MOIM_*`)를 먼저 불러온 셸에서 실행한다 |
| 깨끗한 환경 검증 | 0B 보정 커밋 직전, 스테이징한 내용을 새 폴더로 내보내 새 venv에서 `pip install -e ".[test,visual]"`, import, `pytest` 164개 통과(건너뜀 0), 해시 9/9(2회), 프롬프트 검증 14개를 확인했다. 마이그레이션 up/down/up은 테스트와 개발 DB 직접 실행으로 확인 |
| 크리퍼 재확인 | 격리 복사본·격리 DB `creeper_scratch`로 225개 실행, 223 통과·2 실패(원인과 판정 영향 없음은 `docs/ARCHITECTURE.md`의 `0B-1 재확인 결과`) |
| 주의 | 크리퍼의 `tools/db/cutover.py`·`bootstrap.py`와 테스트 일부는 Windows 사용자 레지스트리(`HKCU\Environment`)로 실제 크리퍼 DB(55432)를 읽거나 쓴다. 원본에서 실행하지 않는다. MOIM에 이 방식을 가져오지 않는다 |
| 참고 원본 | `C:\Users\user\Desktop\크리퍼`. 읽기 전용 구조 참고용 |

## 0B-2 진행 상황 (2026-11-18)

- DETERMINISTIC PASS: M0에서 자동 재시도·JSON 복구 호출·검증 재호출·예산 예약을 제거했고, 원문 로그(요청·응답·파싱 결과·오류 본문)를 크기·해시·ID 기록으로 바꿨다. 호출마다 로거 `moim.provider`에 한 줄이 남는다. 테스트 `tests/test_m0_provider.py` 37개(LIVE 뒤 1개 추가), 해시 9/9. 계약은 `docs/LIVE_TESTING.md` 4-1.
- LIVE PASS: 정상 text 1회·정상 structured output 1회 성공. 누적 6회 사용(D-A8 이후 고정 호출 상한 없음)(`docs/LIVE_TESTING.md` 4-2, 6절). LIVE에서 옛 주소 종료(410), 모델명 접두, `max_tokens` 거절을 발견해 기본 주소·모델·토큰 파라미터를 고쳤다.
- 키는 사용자 환경변수 `TIMELY_API_KEY`에 있고 `.env`에는 없다. 앱은 환경변수만 읽는다.

## 0B-3 진행 상황 (2026-11-18)

- 작업 상태: `ai_jobs` 테이블과 FastAPI 백그라운드 실행. 상태 흐름·API·복구 규칙은 `docs/ARCHITECTURE.md`의 `AI 작업과 지연 대응`. 새로고침·서버 재시작 복구, 재시도, 중복 요청, revision 충돌(동시 claim 포함)을 `tests/test_jobs.py` 31개가 새 DB 연결로 다시 읽어 검증한다.
- 화면 캡처: 한 명령으로 원본 9개를 정확히 1440×900(뷰포트, 첫 화면)으로 저장한다. 스크롤되는 전체 페이지는 `--full-page`로 따로 저장하며 이때는 크기가 제각각이다.

```
$env:MOIM_ENV='development'; .\.venv\Scripts\python.exe tools\capture_screens.py
```

  결과는 `artifacts\screens\<원본 파일명>\original.png`와 `impl.png`, 나란히 보는 `artifacts\screens\index.html`. landing·signup·login·dashboard·invite·group-create·group 7쌍은 `impl.png`까지 만든다(chat·mypage는 아직 `None`이라 "구현 없음"으로 기록, 실패 아님). 로그인이 필요한 4쌍은 개발 시드(`apply_dev_seed`)가 만든 사용자의 세션 쿠키로 캡처한다. 시드는 `shot`으로 시작하는 사용자와 그 그룹만 지우고 다시 만들며(개발·테스트 환경만), 데이터베이스 환경변수(`MOIM_DATABASE_URL`)가 있어야 한다. 시드의 팀 목표 문장은 화면 확인용 손으로 쓴 FIXTURE이며 LIVE가 아니다. 캡처는 개발 서버(`uvicorn --factory moim.main:app_factory`)가 떠 있어야 한다.
- 새 의존성: `playwright`(선택 extra `visual`, `pip install -e ".[test,visual]"`). 브라우저는 이 PC에 이미 있는 것을 썼고 새로 내려받지 않았다. 새 큐·DB·프런트엔드 프레임워크는 없다.

## 0B 보정 (2026-11-18)

- **최종 커밋:** `d7e5fd7e9aca33adae33000e1f1008479ba8784b`(`0B root hardening`, baseline `a9a9721` 다음). 이 해시는 보정 내용 커밋의 것이며, 이 줄을 추가한 문서 커밋은 그 다음 커밋이다.
- D-A8: LIVE 예산 개정(`docs/PRODUCT.md`). `docs/LIVE_TESTING.md` 4절·ROADMAP·`prompts/`의 고정 호출 상한 문구를 맞췄다. 누적 LIVE 호출 6회(보정 중 LIVE 재호출 없음).
- 줄바꿈: 변경한 텍스트 파일 21개를 LF로 정상화했고 `git diff --cached --check`가 깨끗하다. 변경하지 않은 기존 파일 일부(`alembic.ini`, `tools/capture_screens.py`, `src/wemeet_ai`의 일부 모듈 등)는 baseline에 CRLF로 들어 있어 그대로 두었다. `AGENTS.md`와 원본 HTML은 커밋에 포함하지 않았다(변경 없음).
- Git: 위 환경 표. `.gitattributes`(`moim-*.html text eol=lf`)와 `.gitignore`(`.env`, `.local/`, `.venv/`, `artifacts/`, `build/`, `*.egg-info/`)가 실제로 적용됨을 `git check-ignore`와 `git ls-files --eol`로 확인했고, `core.autocrlf=true`로 새로 clone해도 원본 9개 바이트가 같았다.
- 제거: `m1/a_coaching`·`b_analysis`·`c_goal_draft`·`d_group_plan`, `persistence/`, `continuity.py`, `cli.py`, `m0/versions.py`, `m0/cache.py`, 오래된 `src/wemeet_ai_core.egg-info`(`wemeet-ai` 콘솔 엔트리와 설치된 `wemeet-ai-core` 배포판). `Runtime`은 캐시·`SOURCE_RELOAD_REQUIRED` 게이트·`fresh`·`scope_id`가 없고 호출 한 번이 모델 호출 한 번이다. `Meta`에서 `source_revision`·캐시 관련 필드를 지웠다. `tests/test_packaging.py`가 남은 모듈 집합(19개) 정확 일치, 제거 모듈 import 불가, 옛 CLI 실행 불가, 콘솔 엔트리·배포판 없음, 빌드한 wheel의 내용을 검증한다. `pyproject.toml`에 `static/*` package-data를 추가했다(없으면 설치본에서 정적 파일이 빠졌다).
- 출력 한도: `DEFAULT_MAX_OUTPUT_TOKENS` 상수 하나(4096). 4096은 기존 값을 유지한 것이고 LIVE 구조화 출력 측정으로 검증하지 않았다.

## 1단계 인증 (2026-11-18)

- 결과: 회원가입(가입 즉시 로그인), 아이디 중복 확인, 로그인(로그인 상태 유지 선택), 로그아웃, 서버 세션, landing·signup·login 템플릿 이식, 비밀번호 복구 비활성 안내. 값·구조는 `docs/SECURITY.md` 4-2, `docs/ARCHITECTURE.md`의 `인증 (1단계)`.
- landing 맨 아래 CTA: 로그아웃이면 `/login`(성공 뒤 `/dashboard`), 로그인이면 `/dashboard`(D-A5). `moim-invite`로 가지 않는다.
- 새 의존성: `argon2-cffi`(라이브러리 하나. 새 DB·큐·프런트엔드 프레임워크 없음). 새 설정 환경변수 없음.
- 검증: `tests/test_auth.py` 84개(결정적, LIVE 없음). 브라우저(내장 브라우저, 개발 서버·개발 DB)에서 중복 확인 3상태, 클라이언트 검증, 가입 → 새로고침 → 로그아웃 → 보호 페이지 차단, 잘못된 비밀번호·없는 아이디의 같은 오류, 로그인 유지 로그인, localStorage·sessionStorage·`document.cookie` 비어 있음을 확인했다. 개발 DB에 확인용 계정 `browser01`이 남아 있다.
- 1440×900 캡처: `artifacts\screens\moim-signup.html\`, `moim-login.html\`는 원본과 구현이 픽셀까지 같았다(진단용 비교, 합격 기준 아님). `moim-landing.html\`은 애니메이션(흐르는 카드·떠다니는 캐릭터) 때문에 캡처마다 일부 픽셀이 달라진다(검토에서 21픽셀 차이). 사용자가 `artifacts\screens\index.html`로 눈으로 확인한다.
- 달랐던 사실:
  - 첫 생성에서 `login.js`·`signup.js`가 문법 오류였고(자르는 위치 오류) 폼이 서버 JSON 오류 화면으로 넘어갔다. 수정했고 `test_generated_scripts_parse`가 모든 정적 스크립트를 `node --check`로 검사한다(node가 없으면 건너뜀).
  - 정적 파일이 휴리스틱 캐시되어 수정 뒤에도 옛 JS가 실행됐다. 정적 파일을 `Cache-Control: no-cache`(재검증)로 서빙한다.
- 남은 제한: `docs/SECURITY.md` 4-2의 `남은 제한`(프록시 뒤 주소, 아이디 단위 잠금의 계정 잠금 공격, 복구 없음, 탈퇴·비밀번호 변경은 6단계, `/api/jobs` 미인증).
- 실행: 앱은 `.env`를 읽지 않는다. 개발 서버는 환경변수를 불러온 셸에서 `python -m uvicorn --factory moim.main:app_factory`로 띄운다.

## 2단계 그룹·ProjectBrief (2026-11-18)

- 결과: `FORMING` 초대방 → ProjectBrief → `ACTIVE` 흐름 전체. 값·구조는 `docs/ARCHITECTURE.md`의 `그룹·초대방·ProjectBrief (2단계)`, 보안 값은 `docs/SECURITY.md` 4-3, 초대방 정원·만료·정리 정책(PROPOSED)은 `docs/ROADMAP.md` 표.
- 판정: 결정적 PASS(`tests/test_groups.py` 74개, 전체 322개). 팀 목표 표시문 LIVE PASS(2회, `docs/LIVE_TESTING.md` 6절). 원본 HTML 해시 9/9 동일(`tools/check_design_hashes.py`).
- 화면 검증: 내장 브라우저가 아니라 Playwright(실제 Chromium) 두 계정으로 dashboard `새 그룹 만들기` → 초대방 → 다른 계정의 코드 입력 참여 → 대기 화면 → 최소 2명에서 `그룹 생성하기` → ProjectBrief 입력(달력·결과물 칩·중간 일정) → 다음 버튼 → 두 계정이 각자 chat으로 이동 → 팀 목표 표시 → 지각 참여 거부 → ProjectBrief 수정(revision 2) → 새 표시문을 확인했다. 스크립트는 일회용이라 저장소에 넣지 않았다. 확인용 계정(`shote…a`, `shote…b`)과 그룹은 개발 DB에 남아 있고 캡처 시드를 다시 돌리면 정리된다.
- 1440×900 캡처: `artifacts\screens\` 아래 dashboard·invite·group-create·group 4쌍을 새로 만들었다. 원본과의 눈 확인은 `artifacts\screens\index.html`에서 사용자가 한다.
- 달랐던 사실:
  - 원본 dashboard·group에는 `새 그룹 만들기`가 `<a>`였다. 구현은 `<button>`(클릭 시 `POST /api/groups`)이며 스타일을 그대로 쓴다.
  - 원본 dashboard에 없던 `초대코드로 참여` 링크(상단)와 `로그아웃` 버튼을 상단 우측에 넣었다. 원본 landing → invite 경로가 D-A5로 바뀌어 초대받은 사람이 코드 참여 화면에 들어갈 길이 필요했고, 로그아웃은 1단계 임시 화면이 하던 일을 옮긴 것이다. 기존 `.login` 링크 스타일을 재사용했다. **D-A9(2026-11-18)로 사용자가 승인했다**(`docs/PRODUCT.md`). 마이페이지 링크는 6단계에서 복원한다.
  - 원본 사이드바의 프로필 `마이페이지` 링크는 마이페이지가 6단계라 링크 없는 표시(이름만)로 바꿨다. 이 차이도 D-A9에 포함된다.
  - 원본 invite는 참여 칸과 코드 박스가 한 화면에 함께 있다. 초대방이 없는 사용자는 코드 박스 대신 `새 그룹 만들기` 버튼을, 참여 대기자는 입력칸과 코드 박스 없이 대기 문구를 본다(초대방 활성화 대기 상태, 기존 문구·칩 스타일 재사용).
  - 원본 group-create의 `moim-group-create-position.html` 이동과 invite의 `그룹 생성 완료` 팝업은 이식하지 않았고, 원본 CSS의 쓰이지 않는 규칙(투두·패널 등)은 그대로 둔 채 구현에 필요한 최소 CSS만 덧붙였다(비활성 버튼, 안내 패널, 팀 목표 다시 시도 버튼).
  - 첫 e2e 실행에서 Playwright의 `wait_for_function`이 CSP(`unsafe-eval` 금지)에 막혔다. 제품 쪽 문제가 아니라 확인 방법이었고 셀렉터 대기로 바꿨다.
- 남은 제한: `docs/SECURITY.md` 4-3의 `남은 제한`(코드 공간 100만 개, 대기자 스스로 나가기 없음, 폴링 4초, 표시문 근거는 사람 판정).

## 3단계 공용 후보·순위 제출 (D-B3, 2026-11-19)

- 결과: ACTIVE cohort 구성원이 개인 인터뷰 없이 그룹 공용 역할 후보(ProjectBrief revision당 Timely 1회, 불변)에서 서로 다른 1·2·3순위를 고르고, 제출 전에는 화면에서 자유롭게 바꾸며, 제출하면 immutable preference submission revision과 `WAITING_ASSIGNMENT`가 저장·복구된다. 모두에게 `N/M명 제출 완료`만 보인다. 값·구조는 `docs/ARCHITECTURE.md`의 `공용 역할 후보·비공개 순위 제출 (3단계, D-B3)`, 보안은 `docs/SECURITY.md` 4-4.
- 판정: 결정적 PASS(`tests/test_ranking.py` 26개, 전체 348개, 공급자는 FIXTURE). **이번 작업에서 LIVE 호출 0회**(후보 생성 코드·프롬프트·입력은 바꾸지 않았다. 누적 LIVE 67회 그대로). 원본 HTML 해시 9/9 동일. 프롬프트 검증 14개 PASS. 실제 Chromium(Playwright, 개발 서버·개발 DB)에서 후보 6개 말풍선 → 칩 3개 선택 → 순위 변경 → 제출 → `순위를 제출했어요… 1/4명 제출 완료` → 새로고침 복구, 콘솔 오류 없음, `localStorage`·`sessionStorage` 비어 있음을 확인했다.
- 바꾼 것: 인터뷰 API(`/interview*`)·MemberState 패널·인터뷰 턴·종료 버튼·`E.coach` 호출을 3단계 경로에서 제거. `RankingService`(`ranking.py`)가 화면·제출을 맡고 `GET /api/groups/{id}/roles`가 화면 데이터를 준다. 입력창은 구조 유지를 위해 남기되 비활성이다(AI 대화 없음). 패널 제목은 `역할 순위 선택`·`역할 배정 대기`.
- 보존(삭제 안 함, 4단계 재사용): `interview/coach.py`(E.coach 어댑터), `interview/turns.py`(`InterviewTurnService`, 어디서도 import하지 않음), `wemeet_ai.m1.e_member_plan`, 테이블 `interview_turns`·`member_states`·`member_progress.interview_closed_at`(마이그레이션 `0007`이 DB 주석으로 DEPRECATED 표시만 했고 데이터는 그대로, 테스트로 down/up 뒤에도 행이 남음을 확인).
- 달랐던 사실:
  - 옛 `tests/test_interview.py`(31개)를 삭제했다. 후보·격리·순위·STALE 검증은 `test_ranking.py`로 옮겼지만 인터뷰 턴·E.coach 동작 검증은 옮기지 않았다(코드는 호출되지 않는다). 그 파일은 커밋 전 작업 사본이라 Git에서 복구할 수 없다. 4단계가 `coach.py`·`turns.py`를 다시 연결할 때 테스트를 새로 써야 한다.
  - 포트 8000에 이전 코드의 개발 서버가 떠 있어 종료하고 새 코드로 다시 띄웠다(캡처가 옛 라우트를 읽어 실패했기 때문).
  - 캡처 스크립트 시드에서 인터뷰 턴·MemberState 삽입을 지웠다(후보만 시드).
- 화면 캡처: `artifacts\screens\moim-chat.html\original.png`·`impl.png`와 추가 `impl-ranking.png`(순위 선택 중)·`impl-waiting.png`(제출 뒤), `artifacts\screens\index.html`. 시드의 후보는 손으로 쓴 FIXTURE다. 눈 확인은 사용자가 한다.
- 재검증(2026-11-19): 전용 PostgreSQL을 기동해 `tests/test_ranking.py` 26개와 전체 348개를 다시 실행했고 모두 PASS했다. 원본 HTML 해시 9/9, 프롬프트 검증 14개, `git diff --check`도 PASS했다. 캡처 4종은 모두 1440×900이며 `original.png`·`impl.png`·`impl-ranking.png`·`impl-waiting.png`를 직접 열어 레이아웃을 확인했다. 폴더에 남은 `impl-ready.png`는 D-B3 이전 개인 인터뷰 화면의 오래된 미사용 캡처이고 `index.html`에는 연결되지 않는다. 공식 단계 완료 전환에는 여전히 사용자의 원본·구현 눈 확인이 필요하다.
- 해결된 OPEN: 10턴 안에 필수 4개를 못 채우면 순위를 못 내던 막다른 길은 인터뷰가 3단계에서 없어져 사라졌다.
- 개발 DB에 남은 것: 일회용 LIVE 스크립트의 `live3a…`/`live3b…` 사용자와 그룹, 캡처 시드의 `shot*`(시드를 다시 돌리면 `shot*`만 정리된다).

## 4단계 진행 (2026-11-19)

4단계는 `prompts/04-split/`의 세 세션(A·B·C)으로 나눠 진행한다. **세션 C가 끝나기 전까지 4단계는 완료가 아니며, ROADMAP의 4단계 상태는 바꾸지 않았다.** 커밋하지 않았다(사용자 확인 뒤 커밋).

### 세션 A 완료 (수행 1~6: 역할 배정, coverage, 배정 후 인터뷰)

- 선행 게이트: 3단계 완료 기록(위 현재 상태)과 D-B4 승인 기록(`docs/PRODUCT.md`)을 확인했다.
- **배정**(`src/moim/roles/assignment.py`): 순위 제출 트랜잭션 안에서 그룹 단위 advisory lock(`assignment:{group_id}`)을 구성원 잠금보다 먼저 잡는다. 그 제출로 현재 cohort 전원이 현재 candidate revision의 `WAITING_ASSIGNMENT`가 되면 `assign_if_ready`가 한 번만 배정한다.
  - 규칙: 1→2→3순위 라운드로 배정한다. 같은 라운드에서 자리보다 지원자가 많으면 서버 RNG로 당첨자를 정하고, 3순위까지 못 받은 사람은 남은 자리 중 서버 RNG로 배정한다.
  - 중복 역할: 역할 수 < 인원일 때만 역할당 ceil(인원/역할) 자리를 둔다(fixture로만 시험). 구성원은 익명 slot(`s1`…)으로 구분된다.
  - RNG: 운영은 `random.SystemRandom()`(seed 없음), 테스트는 `create_app(rng_factory=...)`로 seeded `random.Random`을 주입한다. 모든 무작위 추첨은 `role_assignments.draw_audit`(서버 전용, 화면·로그에 안 나감)에 남는다.
  - 1회 보장: 같은 제출 재전송·새로고침·재시작·동시 마지막 제출로 다시 추첨하지 않는다. 부분 유일 인덱스 `uq_role_assignments_current`가 그룹당 CURRENT를 하나로 막는다.
- **ROLE_TODOS**: 배정과 같은 트랜잭션에서 cohort 전원이 `member_progress.status = ROLE_TODOS`, `assignment_revision = <rev>`가 된다. `submission_revision`은 배정 입력 추적용으로 유지한다. 집계 `N/M명 제출 완료`는 ROLE_TODOS도 제출로 센다.
- **coverage**(`src/moim/roles/coverage.py`): 배정 직후 `team_coverages`에 PENDING 행이 생기고, 응답 뒤 백그라운드에서 Timely를 1회 호출한다.
  - 입력: ProjectBrief(결과물에 서버 id `d1`…)와 이름 없는 slot 표뿐이다.
  - 서버 검사(ID 수준): 출력 `parts`[{name, source(dN), slot(sN)}]에서 모든 dN이 하나 이상의 part에 있고, 모든 slot이 1~4개 part를 가지며, 같은 이름 part 중복과 모르는 id가 없어야 한다. part id(`p1`…)는 서버가 붙인다.
  - 실패: FAILED로 두고 아무 cohort 구성원이나 명시적으로 재시도한다(`POST /api/groups/{id}/coverage/retry`, action_id 멱등). 서버 재시작 때 PENDING/RUNNING은 `SERVER_RESTARTED` FAILED가 된다. 대체 목록은 없다.
  - 브라우저에는 상태만 가고 parts는 보내지 않는다.
- **배정 안내**: 서버 문장 두 개(일반 문구 + "OO님은 이번 프로젝트에서 ○○ 역할을 맡게 됐어요(N순위로 배정 / 남은 역할로 배정)")를 AI 말풍선 스타일로 보이며 Timely를 부르지 않는다. 오른쪽 패널 제목은 `내 투두리스트`이고 원본과 같은 `rolebox`(`내 역할` · 역할명 · `N순위로 배정`)를 쓴다. 다른 구성원의 이름·역할·순위·추첨 결과는 보내지 않는다.
- **배정 후 인터뷰**(`src/moim/roles/interview_ai.py` 순수 로직, `src/moim/roles/interview.py` 서비스): 항목은 `experience`(역할 관련 경험, 필수)·`weekly_available_minutes`(필수)·`tools`·`preferred_times`·`constraints`다.
  - AI 주도: 매 턴 Timely가 다음 질문과 버튼 2~4개를 만든다(첫 질문도 AI).
  - 서버 검사: 이미 아는 항목·이미 한 질문은 금지(필수가 UNKNOWN일 때만 1회 재질문), 내부 표현 금지, 버튼 2개 이상, 시간 버튼은 라벨의 숫자 하나 = 저장 값. 남은 질문 수가 아직 묻지 않은 필수 수 이하이면 그중에서만 묻고, 필수를 다 묻기 전에는 끝낼 수 없다.
  - 쓸 수 없는 질문: 그 턴을 FAILED(오류 말풍선 + `다시 시도`)로 두고 고정 질문으로 대체하지 않는다.
  - 한도·모름: 최대 질문 5개(DB check `questions_asked between 0 and 5`). `잘 모르겠어요`를 항상 제공하고 UNKNOWN은 그대로 저장한다.
  - 값 저장: 자유 입력 값은 사용자 문장 인용이 있고 값이 사용자 말에서 만들어졌을 때만 저장한다. 의미 없는 입력은 422로 저장·질문 수·호출을 쓰지 않는다. 이해 못 한 답은 같은 질문을 유지하고(질문 수 미증가), 답변 AI 호출은 인터뷰당 최대 12회다.
  - 제약: 제약 질문의 자유 입력은 Timely에 보내지 않고(`answer_kind=withheld`) 원문을 private로 저장한다. 코드가 변환하는 것은 날짜(범위 포함, 프로젝트 기간 안), `이번/다음 주 X요일`(오늘 기준 하루), 부정 문맥의 요일(매주), 긍정 문맥의 시간대 단어(선호 시간 후보)뿐이다.
  - 확인 단계: 질문이 끝나면 `CONFIRMING`이 된다. 서버가 쓴 확인 말풍선과 버튼 `맞아요`/`OO 고치기`를 보이고, 고치기는 입력창에 적거나 `잘 모르겠어요`/`그대로 둘게요`로 끝낸다(코드만, AI 호출 없음). `맞아요` → `DONE` + snapshot.
  - 화면은 기존 말풍선·빠른 답변·입력창만 쓴다(새 카드·패널 없음). 인터뷰는 역할을 바꾸지 않는다(역할 변경 API 없음, 테스트로 확인).
- **새 테이블·마이그레이션** `migrations/versions/0008_role_assignment.py`:
  - 테이블: `role_assignments`, `role_assignment_members`, `team_coverages`, `role_interviews`(하위 상태 `ASKING|CONFIRMING|DONE`), `role_interview_turns`(`OPEN|ANSWER|EDIT`), `role_member_states`(불변 revision), `role_interview_snapshots`.
  - `member_progress`: `assignment_revision` 컬럼과 상태 `ROLE_TODOS`를 추가했다. 제약 `submission_iff_waiting`을 `submission_unless_interview` + `assignment_iff_role_todos`로 바꿨다.
  - downgrade는 ROLE_TODOS를 WAITING_ASSIGNMENT로 되돌리고 새 테이블만 지운다. 옛 `interview_turns`·`member_states`·순위 제출은 그대로 남는다(테스트로 down→up 확인). 개발 DB `moim_dev`도 0008로 올렸다.
- **새 API**(모두 cohort 구성원 본인만, 로그인·CSRF·action_id 필요):
  - `POST /api/groups/{id}/role-interview/start`
  - `POST /api/groups/{id}/role-interview/answer`(`message` 또는 `question_id`+`option_id`)
  - `POST /api/groups/{id}/role-interview/retry`(`seq`, `expected_attempt`)
  - `POST /api/groups/{id}/coverage/retry`(`assignment_revision`, `expected_attempt`)
  - 화면 데이터는 기존 `GET /api/groups/{id}/roles`의 `role` 키(ROLE_TODOS일 때만)로 간다: `role_name`, `role_description`, `rank_text`, `announcement`, `interview`(state·messages·question{question_id, kind, options[id,label]}·busy·failed·input_open), `coverage`(상태만).
- **세션 B가 읽을 연결 키(정확히)**: 현재 배정은 `role_assignments`에서 `group_id`이고 `state='CURRENT'`인 행의 `revision`이며, 이것이 **`source_assignment_revision`**이다. 이 키로 다음을 읽는다.
  - 내 역할: `role_assignment_members(group_id, assignment_revision, user_id)` → `slot_id`, `role_name`, `role_description`. `assigned_rank`는 AI 입력에 넣지 않는다.
  - 이름 없는 배정표: `AssignmentService.team_table(s, group_id, rev)` → [{slot_id, role, description}].
  - coverage: `team_coverages(group_id, source_assignment_revision)` 중 `state='SUCCEEDED'`이고 `stale_at is null`인 행의 `parts`. 내 결과물은 `parts` 중 `slot == 내 slot_id`인 것이다.
  - 인터뷰 입력: `role_interview_snapshots(group_id, user_id, source_assignment_revision)`. `role_interviews.state='DONE'`일 때만 있다.
    - `provider_input`={`current_level`, `weekly_available_minutes`(정수 분 또는 "UNKNOWN"), `tools`, `preferred_times`}: B3 허용 필드만 있고, UNKNOWN은 문자열 `"UNKNOWN"`이다.
    - `scope`는 인터뷰에서 묻지 않았다. coverage의 내 parts가 scope다.
    - `deadline_limits`={`blocked_dates`(ISO), `blocked_weekdays`(0=월)}는 서버 마감일 검증용이다. `private_constraints.raw`는 공급자에 보내지 않는다.
    - `member_state_revision`으로 원래 MemberState를 추적한다.
- **세션 C용 STALE 구조**: `role_assignments.state='STALE'`+`stale_at`·`stale_reason`, `team_coverages.stale_at`, `role_interviews.stale_at`, `role_interview_snapshots.stale_at`.
  - 인터뷰 서비스는 `stale_at`이 있으면 409(NOT_ASSIGNED)로 막고 진행 중 턴 결과를 버린다.
  - 재배정은 새 revision(`operation='REASSIGN'`)으로 만들면 된다(CURRENT 유일 인덱스가 하나만 허용).
  - **아직 하지 않은 것**: ProjectBrief를 수정해도 `GroupService.update_brief`는 `WAITING_ASSIGNMENT`만 INTERVIEW로 되돌린다. `ROLE_TODOS`·assignment·coverage·인터뷰는 STALE로 바꾸지 않는다(수행 11, 세션 C).
- **고친 3단계 경계 테스트**(수행 13의 일부를 앞당긴 것이며 검증을 약화하지 않았다), `tests/test_ranking.py`:
  - `test_nothing_is_assigned_drafted_or_hidden_before_or_after_everyone_submits` → `test_nothing_is_assigned_before_everyone_submits_and_nothing_is_drafted_after`. 이제 다음을 확인한다: 전원 제출 전 assignment·coverage·인터뷰 0개, 마지막 제출 직후 assignment 정확히 1개·coverage 호출 1회, 투두·숨겨진 기준 테이블·컬럼 없음, 인터뷰 AI 호출 없음. 이유: 4단계 계약은 마지막 제출 때 배정한다.
  - `test_progress_is_a_count_only_and_shown_to_every_cohort_member`: 마지막 제출 뒤 phase가 `WAITING_ASSIGNMENT`가 아니라 `ROLE_TODOS`다.
  - `test_duplicate_and_concurrent_submits_store_one_revision`: `submit_preferences`가 이제 `(stored, assignment_revision)`을 돌려줘서 `[0]`만 비교한다.
  - 4단계는 deprecated `interview/coach.py`·`turns.py`를 import하지 않으므로 해당 3단계 테스트는 그대로다. 의미 없는 입력 규칙만 같은 내용으로 `interview_ai.meaningful`에 옮겼다.
- **검증**:
  - 결정적: `tests/test_role_assignment.py` 45개. 범위는 배정(순수 알고리즘·고정 RNG·충돌·소진·중복 역할·SystemRandom), 1회 저장(재전송·재시작·동시 제출), 본인 역할만 노출, coverage(ID 검사 6종·실패·재시도·재시작), 인터뷰(AI 첫 질문·전체 흐름·5개 한도·필수 둘 다 모름·의미 없는 입력·이해 못 함·근거 없는 값·쓸 수 없는 질문 5종·공급자 실패·확인 단계 수정·오래된/위조 질문·타인 비차단·역할 불변), 비공개·로그·404·로그인·CSRF·XSS, 새로고침·재시작 복구, 제약 변환·시간 버튼, 마이그레이션 0008 down/up, DB 제약이다.
  - `test_ranking.py` 26개(위 3개 수정 포함).
  - **세션 A 검증 실행(2026-11-19)**: 전체 결정적 테스트 393개 통과(실패·건너뜀 0, 이전 348 + 4단계 45), 원본 HTML 해시 9/9, `prompts/validate-prompts.ps1` 14개 PASS, `git diff --check` 깨끗함. 교차 단계 전체 회귀는 세션 C가 단계 종료 때 다시 한 번 실행한다.
  - LIVE: 36회(호출 68~103), 모두 성공. 기록은 `docs/LIVE_TESTING.md`. 역할 5종(UX/UI 디자인·프론트엔드 개발·발표 자료·발표 자료 제작·서비스 기획)의 질문을 사람이 판정할 수 있게 남겼다. 1차에서 찾은 결함 2개(시간 범위 버튼, "다음 주 화요일"→매주)를 고치고 최종 코드로 2차를 다시 실행했다.
- **브라우저**: 개발 서버(포트 8001, 새 코드)와 Playwright Chromium으로 LIVE 2차 그룹의 두 구성원 chat을 1440×900으로 열었다.
  - 확인한 것: 패널 제목 `내 투두리스트`, rolebox `UX/UI 디자인 · 1순위로 배정`, 안내 말풍선, 질문·답·확인 말풍선, 콘솔 오류 0, localStorage·sessionStorage 비어 있음.
  - 캡처: `artifacts\screens\moim-chat.html\impl-role-todos-1.png`·`impl-role-todos-2.png`(DONE 상태).
  - 세션 C로 남긴 것: 인터뷰 진행 중 화면 캡처, 그리고 순위 제출 → 배정 → 인터뷰를 브라우저에서 처음부터 끝까지 클릭하는 흐름(디자인·브라우저 검증).
  - 포트 8000에는 이전 코드의 개발 서버가 여전히 떠 있다(건드리지 않음).
- **다음 세션이 알아야 할 사실**:
  - 오른쪽 패널의 투두 요소(`내 투두 N` pill, count, `+ 직접 추가하기`, `투두 확정하기`)는 여전히 `hidden`이다. 세션 B가 초안이 생기면 보이게 한다. 인터뷰 DONE 뒤 빈 상태 문구는 "인터뷰를 마쳤어요. 이 내용으로 투두 초안을 만들 거예요."이며 초안 생성은 아직 없다.
  - 확인 단계의 `맞아요`는 `role_interviews.state='DONE'`과 snapshot을 같은 트랜잭션에 쓴다. 세션 B는 DONE을 초안 시작 조건으로 쓰면 된다. coverage가 FAILED면 초안을 만들 수 없으므로 초안 쪽에서도 상태를 확인해야 한다.
  - `RoleInterviewService.role_view`가 화면 데이터의 단일 진입점이다. 투두 데이터도 같은 `role` 키 아래에 두면 GET 한 번으로 양쪽이 함께 그려진다(`chat.js`는 `d.role`만 본다).
  - LIVE용 개발 DB에 일회용 사용자 `live4…a/b/c`(그룹 2개)가 남아 있다.
- **남은 OPEN(세션 A 범위)**: LIVE에서 AI가 매번 다섯 항목을 모두 물었다. "도구·선호 시간·제약은 필요할 때만"이 실제로는 거의 줄지 않았다. 정책 위반은 아니지만 사용자 판정 대상으로 남긴다. 역할별 질문 품질 차이도 사람(사용자)의 판정이 필요하다.

### 세션 B 완료 (수행 7~10: 투두 초안, 수정, 확정, 그룹 조회 API)

- 선행 게이트: 3단계 완료 기록, D-B4 승인, 위 `세션 A 완료`를 확인했다. 커밋하지 않았다.
- **코드**: `src/moim/roles/todo_ai.py`(순수 로직: 증거 enum, 공급자 계약, 프롬프트 3개, 서버 검사), `src/moim/roles/todos.py`(`TodoService`), `interview/routes.py`(새 API), `main.py`(연결·재시작 처리), `roles/interview.py`(`role_view`에 `todos` 키), `static/chat.js`·`templates/chat.html`(오른쪽 패널).
- **초안**(수행 7): 인터뷰 `DONE` + 그 assignment revision의 coverage `SUCCEEDED`일 때만 시작한다(`POST …/todos/start`, 화면이 자동 호출, action_id 1회). Timely 1회, 서버 검사를 통과해야만 저장한다.
  - 공급자 입력(B3만): ProjectBrief(결과물 `d1`…, milestone `m1`…), 내 역할, 이름 없는 팀 표(slot·역할·그 slot의 coverage parts·`is_me`), 내 parts(=scope), snapshot의 수준·주간 시간·도구·선호 시간("UNKNOWN" 그대로), 기간(`first_day`=max(시작일, 오늘)), 업무량 한도(`weekly_budget_minutes`, 7일 기간별 `windows`, `max_minutes_per_todo`=예산÷3, `max_todos_per_window`=3). 제약 원문·마감 제한 날짜·순위·대화·이름·user_id는 보내지 않는다.
  - 서버 검사(`check_draft`): 개수 4~8(예산이 아주 작으면 최소 2까지), 제목 4~60자·중복 금지·일반 제목 금지(역할명과 일반 단어를 빼면 남는 게 없거나 프로젝트·parts와 겹치는 말이 없으면 거절), 내 parts만·모든 parts 덮기, 기간 안, 같은 날 최대 2개, 기간의 40% 이상에 퍼짐, part 안에서 단계 순서(RESEARCH→DRAFT→REVIEW→SUBMIT), milestone 표시 투두는 그 날짜 이전·기간 안 milestone 전에 투두 1개 이상, 7일 기간별 예상 분 합계 ≤ 주간 시간(UNKNOWN이면 300분, 아는 값은 최소 60분), 결과물 4~100자, 완료 기준 1~4개(10~120자, 날짜 표현 금지), 증거 1~3개(enum), 예상 30~480분.
  - 인터뷰에서 막은 요일·날짜에 걸린 마감은 **코드가** 가장 가까운 이전 날(없으면 이후)로 옮긴 뒤 검사한다(제약은 공급자에 가지 않는다).
  - 실패: 저장하지 않고 단계 FAILED + 왼쪽 오류 말풍선 + `다시 시도`. 고정 템플릿 대체 없음. 저장 중 예외도 전체 롤백 후 `SAVE_FAILED`로 재시도 가능.
- **증거 enum**: `todo_ai.EvidenceType`(DOCUMENT, SLIDES, IMAGE, DESIGN_FILE, SOURCE_CODE, LINK, SPREADSHEET, VIDEO, TEXT_NOTE) 한 곳에서만 정의. `UPLOAD_FORMATS_ACTIVE = False`이며 업로드 형식은 GATE D 뒤에 정한다.
- **직접 수정**(수행 8): `POST …/todos/edit` {op: update(제목·마감)|add(제목, 마감 선택)|delete, key, draft_revision}. 최대 8개(원본 MAX_TODOS), 마감은 `first_day`~기한, 제목 1~60자. AI 호출 없음. 기대 draft_revision이 다르면 409 `CONFLICT`, AI 단계 진행 중이면 409 `BUSY`, 확정 상태면 409 `CONFIRMED`. 본문에 다른 필드(역할·user_id 등)가 있으면 400(스키마 extra 금지). 사용자가 고른 마감은 인터뷰 제약일이어도 막지 않는다(본인 선택).
- **자연어 수정**: `POST …/todos/revise` {message}. 의미 없는 입력은 422로 저장·호출 없음. 새 스키마 `ReviseOutput`{understood, changes[{op: update|add|delete, ref(t1…), title, due_date, part(add만)}], reply}(extra 금지: 역할·담당자·기준 필드가 오면 SCHEMA_INVALID). 서버(`check_revision`)가 ref 존재·중복, 기간, 인터뷰 제약일, 일반 제목, 내 parts, 최대 8개, 업무량(기준 없는 투두는 `max_minutes_per_todo` 가정, 최대 120분)을 검사한다. 규칙 위반은 초안을 그대로 두고 이유 말풍선(단계는 SUCCEEDED, `error_code=NOT_APPLIED_*`), 공급자 실패·형식 오류는 FAILED + `다시 시도`(현재 draft 기준으로 다시 실행).
- **숨겨진 기준 재생성**: 제목이 바뀐 투두와 추가된 투두(`criteria_for_title != title`)만 `투두 확정하기` 때 한 번의 CRITERIA 호출로 다시 만들고 검사한다. 성공하면 같은 트랜잭션에서 확정까지 하고, 실패하면 확정하지 않는다(이전·고정 기준 사용 없음). 마감만 바뀐 투두는 기준을 다시 만들지 않는다(완료 기준에 날짜를 쓰지 못하게 검사하므로 마감과 무관하다. 에이전트 판단, 아래 OPEN).
- **확정**(수행 9): `POST …/todos/confirm` {draft_revision} → 즉시 확정(201) 또는 기준 생성 후 확정(202). 같은 action 재전송·두 번째 클릭은 200(멱등). `todo_confirmations`(revision 1, 2, …)·`todo_confirmed_items`에 보이는 필드와 숨겨진 기준을 함께 복사한다. DB 트리거가 UPDATE(확정 행의 `stale_at` 제외)·DELETE를 막는다. `확정 풀기`는 `POST …/todos/unlock`(같은 버튼, 문구만 바뀜). 재확정 전까지 그룹 API는 이전 revision을 돌려준다. `task_id`는 서버가 uuid4로 만들고 재확정에도 유지되며, 삭제된 투두 행은 `deleted_at`으로 남아 id가 재사용되지 않는다. 확정 직후 원본의 `역할과 투두가 정해졌어요` 오버레이를 보이고 누르면 그룹 화면(`/groups/{id}`)으로 간다. 체크 칸은 원본 모양 그대로 비활성(5단계).
- **그룹 조회 API**(수행 10): `GET /api/groups/{id}/todos` → `{"assignments": [{"role_name", "todos": [{"title", "due_date"}]}]}`. cohort 구성원만(아니면 404). CURRENT assignment의 각 구성원 최신 확정(stale 아님)만, slot 순서, 이름·user_id·slot·task_id·revision·순위·기준 없음. 렌더링은 5단계.
- **화면**: 오른쪽 패널에 원본 투두 줄(체크 칸·제목 입력·마감 알약+달력·×), `+ 직접 추가하기`, `투두 확정하기`/`확정 풀기`, `내 투두 N` pill과 개수를 표시한다. 달력·SVG는 DOM으로 만들고(`innerHTML` 없음) 텍스트는 `textContent`. 왼쪽에는 초안 안내·자연어 요청·AI 답·오류가 기존 말풍선으로 이어지고, 초안이 있으면 입력창이 자연어 수정 입력이 된다. 브라우저에는 `key`(draft 안 번호 `k1`…)·제목·마감만 간다.
- **새 테이블·마이그레이션** `migrations/versions/0009_todos.py`(새 테이블만, downgrade는 이것만 지움):
  - `todo_drafts`(group, user, `source_assignment_revision` PK; FK → `role_interview_snapshots`, `team_coverages`; `member_state_revision`, `draft_revision`, `next_item_no`, `mode` EDITING|CONFIRMED, `confirmed_revision`, `stale_at`)
  - `todo_items`(`task_id` PK, draft FK, `item_no`, `position`, `title`, `due_date`, `origin` AI|USER, `part_id`, `phase`, `milestone_id`, 숨겨진 `deliverable`·`completion_criteria`·`evidence_types`·`estimated_minutes`·`criteria_for_title`(함께 null/not null), `deleted_at`)
  - `todo_ai_steps`(DRAFT|REVISE|CRITERIA, action_id 유일, 진행 중 하나만, `base_draft_revision`·`result_draft_revision`, model·request_id·latency·tokens)
  - `todo_actions`(직접 수정·확정·풀기 멱등), `todo_confirmations`, `todo_confirmed_items`(불변 트리거 `moim_todo_confirmed_immutable`)
  - 개발 DB `moim_dev`도 0009로 올렸다.
- **세션 C가 알아야 할 사실**:
  - STALE 대상 컬럼: `todo_drafts.stale_at`, `todo_confirmations.stale_at`(트리거가 이 컬럼만 허용). `todo_items`·`todo_confirmed_items`에는 stale 컬럼이 없다(draft·확정 revision 단위로 판단). `stale_at`이 있으면 `TodoService`의 수정·확정·재시도는 409 `STALE`, 그룹 API는 그 확정을 빼고, 진행 중 AI 결과는 버린다(FAILED `STALE`). 재배정 뒤에는 새 assignment revision에 새 draft가 생긴다(draft PK가 revision 포함).
  - ProjectBrief 변경 때 "확정 투두는 읽기 전용 유지"(B8)는 아직 구현하지 않았다(수행 11).
  - 브라우저 캡처(1440×900: 투두 초안·수정 후·확정 뒤)는 `artifacts/screens`에 저장하지 않았다. 이번 세션은 내장 브라우저로 개발 서버(포트 8001, 새 코드)에서 초안 화면 → 제목 수정 → 달력으로 마감 변경 → `투두 확정하기`(LIVE 기준 호출) → 완료 오버레이 → 그룹 화면 → 새로고침 뒤 확정 상태 복구 → `확정 풀기`를 직접 확인했다. 콘솔 오류 없음(수정 전 버그 1건, 아래), localStorage·sessionStorage 비어 있음. 저장 캡처와 처음부터 끝까지의 흐름은 세션 C 몫이다.
  - 확인용 포트 8001 개발 서버는 이 세션이 띄웠다가 껐다. 포트 8000(이전 코드)은 건드리지 않았다.
  - LIVE용 일회용 사용자 `live541c4d9…`(그룹 A·B)가 개발 DB에 남아 있다. A 화면설계 구성원은 확정 뒤 `확정 풀기`한 상태(확정 revision 1 존재), B 요구사항정리 구성원은 확정 상태, B 프론트개발은 초안 FAILED 상태다.
- **고친 3단계 경계 테스트**: `tests/test_ranking.py::test_nothing_is_assigned_before_everyone_submits_and_nothing_is_drafted_after`가 "todo·criteria 이름의 테이블·컬럼이 없다"를 검사했는데 0009가 그 테이블을 만들었다. 같은 의미를 유지해 "그 그룹의 todo 테이블 5개에 행이 0개이고 todo AI 호출이 없다"로 바꿨다(약화 아님: 마지막 제출 직후에도 초안이 없음을 행 단위로 검사).
- **검증**:
  - 결정적: `tests/test_todos.py` 48개(초안 조건·B3 입력·잘못된 초안 14종·공급자 실패·재시작·저장 실패·제약일 이동·업무량 고정 상한·milestone·일반 제목·증거 enum 단일 정의, 직접 수정·한도·충돌·멱등, 역할·담당자 변경 차단·교차 사용자·외부인 404, 진행 중 단계 대기, 자연어 수정 적용·규칙 위반 4종·업무량·이해 못 함·의미 없는 입력·재전송·실패 재시도, 확정 멱등·불변 revision·확정 풀기→재확정 task_id 유지·삭제 id 비재사용·이전 revision 기준 불변·기준 재생성 실패 시 확정 차단·마감 누락·저장 실패 롤백·DB 트리거, 비공개·그룹 API 세 필드·로그, 로그인·CSRF, XSS·innerHTML 없음, 새로고침·재시작 복구, 한 구성원 실패가 남을 막지 않음, 0009 down/up).
  - 전체 결정적 테스트·원본 해시·프롬프트 검증 결과는 아래 `세션 B 검증 실행`에 적었다.
  - LIVE: 66회(호출 104~169), `docs/LIVE_TESTING.md` `4단계 세션 B LIVE 기록`. 역할 3종 × 조건 2종(초보·주 2시간 / 숙련·주 10시간). 최종 코드에서 초보 3명·숙련 2명 초안 통과, 숙련 프론트개발 1명은 3회 연속 서로 다른 이유(milestone 뒤 마감, enum 밖 증거, 출력 형식)로 저장되지 않았다(숨기지 않음). 품질 판정표 ①~⑥은 사용자가 채운다.
  - LIVE로 찾아 고친 것: ① 초보 조건 초안이 주간 시간을 넘김(같은 원인 3회 → 기간별 합 대신 투두당 분·기간당 개수 한도를 입력에 추가, 최종 코드에서 초보 3/3 통과) ② 자연어로 투두를 둘로 나누면 추가 투두를 120분으로 가정해 거절(예산 기준 크기로 고침, 재실행 적용) ③ 브라우저에서 제목 저장이 안 됨(`chat.js`의 내 함수 `send`가 기존 보내기 버튼 변수에 가려짐 → `saveEdit`로 이름 변경, 재확인) ④ `request_id`를 잘못된 필드 이름으로 읽어 비어 저장됨(`provider_request_id`로 고침) ⑤ 재시도 때 이전 시도의 사용량 컬럼이 남음(재시도 시 비움). 목록은 마감일 순으로 보이게 바꿨다.
- **남은 OPEN(세션 B 범위)**:
  - 투두 품질 판정(①~⑥)은 사용자 몫이다. 숙련·결과물 3개 역할(투두 8개)에서 초안 실패가 잦았다(최종 코드 3/3 실패). 사용자가 초안을 받으려면 `다시 시도`를 여러 번 눌러야 할 수 있다. 후보 대안(승인 불필요한 기술 결정이지만 LIVE가 더 필요): 그 경우 출력 길이를 줄이도록 투두 상한을 7로 낮추기, milestone 표시 규칙 단순화.
  - 자연어 수정이 milestone 표시 투두를 milestone 뒤로 옮길 수 있다(사용자 요청이라 막지 않음). 막을지는 제품 판단이다.
  - 마감만 바뀐 투두는 숨겨진 기준을 다시 만들지 않는다(위). 승인 결정 문구("수정·추가된 투두")를 마감 변경까지 포함으로 읽어야 하면 바꿔야 한다.
  - 첫 투두 마감이 오늘로 잡히는 경우가 있다(`first_day`=오늘). 서버 검사는 허용한다.

### 세션 B 검증 실행 (2026-11-19)

- 전체 결정적 테스트 441개 통과(실패·건너뜀 0, 세션 A 393 + `test_todos.py` 48). 최종 코드(아래 버그 수정 포함)로 실행했다.
- 원본 HTML 해시 9/9 일치(`tools/check_design_hashes.py`), `prompts/validate-prompts.ps1` 14개 PASS, `git diff --check` 깨끗함.
- 마이그레이션 0009 upgrade → downgrade(0008) → upgrade는 `test_migration_0009_down_and_up_keeps_the_stage_4_interview_data`로 확인했다(세션 A의 0008 down/up 테스트도 0009를 거쳐 통과).
- 교차 단계 전체 회귀는 세션 C가 단계 종료 때 다시 한 번 실행한다.

### 세션 C 완료 (수행 11~13, 단계 종료 검증)

- 선행 게이트: 3단계 완료 기록, D-B4 승인, 위 `세션 A 완료`·`세션 B 완료`를 확인했다. 커밋하지 않았다(세션 A 커밋 `ec7d3c7` 위에 세션 B·C 변경이 함께 있다).
- **재배정**(`src/moim/roles/reassign.py`, 마이그레이션 `0010_reassignment.py`의 `role_reassignments`, 화면 없음):
  - API: `POST /api/groups/{id}/reassignments` {action_id, assignment_revision, target_user_id}(그룹장만, 구성원 403), `GET /api/groups/{id}/reassignments`(내게 온 요청 `incoming`, 내가 한 요청 `outgoing`: request_id·state·assignment_revision만), `POST /api/groups/{id}/reassignments/{request_id}/respond` {action_id, accept}(대상자 본인만, 나머지 404). 요청·동의 본문에 역할 필드는 없다(넣으면 400).
  - 규칙(에이전트 해석, 아래 OPEN): "같은 입력"(같은 cohort·후보 revision·순위 제출)에서 **대상자만** 옮긴다. 새 역할은 대상자 본인의 1→2→3순위 중 자리가 남은 역할(떠나는 역할 제외), 없으면 남은 역할 중 서버 RNG(`draw_audit`). 다른 구성원의 역할·slot은 그대로다. 남은 자리가 없으면(다른 사람을 옮겨야 하면) 요청 단계에서 409 `NO_FREE_ROLE`. 그룹당 열린 요청 하나, 요청·응답 `action_id` 멱등, 동의는 그룹 lock 아래 한 번만(동시 3건 → 1건 성공). 거절은 `DECLINED`.
  - 결과: 새 revision `operation='REASSIGN'`, cohort 전원 `assignment_revision` 갱신, 새 coverage PENDING(백그라운드 Timely 1회). 역할이 그대로이고 인터뷰를 마친 구성원은 MemberState·snapshot을 새 revision으로 복사해 다시 묻지 않는다(B8 "MemberState 유지"). 재배정된 사람과 인터뷰를 마치지 않은 사람은 새 인터뷰를 한다.
- **STALE 전파**(`mark_assignment_stale`): 이전 revision `STALE`+`stale_reason`, 그 revision의 `team_coverages`·`role_interviews`·`role_interview_snapshots`·`todo_drafts`·`todo_confirmations`에 `stale_at`. 지우지 않는다. 열린 재배정 요청은 `SUPERSEDED`. STALE coverage는 Timely를 부르지 않고 진행 중 결과는 버린다(`coverage._cas`에 `stale_at is null`).
  - `REASSIGNED`: 이전 확정은 그룹 조회에서 빠진다(새로 확정하면 다시 보인다).
  - `BRIEF_CHANGED`(`GroupService.update_brief`가 같은 트랜잭션에서 그룹 assignment lock을 잡고 `stale_for_new_brief` 호출): `WAITING_ASSIGNMENT`·`ROLE_TODOS` 전원이 `INTERVIEW`(순위 미제출, `ranking_reset`)로 돌아가 새 후보에서 다시 고른다. 이전 assignment가 CURRENT가 아니므로 전원 제출 시 새 `INITIAL` 배정이 된다. **B8 읽기 전용**: 마지막 확정 투두는 지우지 않고 수정·풀기도 막힌 채(409) 그 구성원이 새 assignment에서 다시 확정할 때까지 그룹 조회(`TodoService.group_view`)에 그대로 나온다. 응답은 세 필드 그대로라 "읽기 전용" 표시는 없다.
  - cohort는 활성화 뒤 바뀌지 않고 순위 제출은 `ROLE_TODOS`에서 409라 바뀔 수 없다(테스트). 둘이 바뀌는 경로는 ProjectBrief 변경뿐이다.
- **브라우저 흐름에서 찾아 고친 결함**: 열린 chat 화면이 재배정으로 새 revision이 생기면 페이지당 하나였던 인터뷰 시작 `action_id`를 다시 보내 `uq_role_interview_turns_action`에 걸려 500이 났다(투두 초안 시작 id도 같은 구조). `chat.js`는 assignment revision이 바뀌면 새 id를 만들고, 서버(`interview.start`·`todos.start`)는 다른 revision에 쓴 id면 409 `ACTION_REUSED`로 거절한다. 회귀 테스트를 더했고 고친 코드로 브라우저에서 다시 확인했다(콘솔 오류 0).
- **수행 12 점검**: `static/`·`templates/`에 `innerHTML`·`outerHTML`·`insertAdjacentHTML`·`document.write`·`|safe`·Timely·키 문자열이 없다(grep). Timely 클라이언트는 `main.py`에서만 만들고 모든 AI 호출(coverage·인터뷰·초안·자연어 수정·기준)은 서버 서비스에서만 한다.
- **수행 13 문서**: `docs/ARCHITECTURE.md`에 4단계 절(모듈·연결 키·API·재배정·STALE), `docs/SECURITY.md` 4-5(4단계 인가·개인정보·STALE·로그), `docs/LIVE_TESTING.md` 세션 C 기록, `docs/ROADMAP.md` 현재 상태에 4단계 "완료 대기". `docs/PRODUCT.md`는 바꾸지 않았다. D-B4와의 대조 결과는 아래 OPEN. 3단계 경계 테스트는 세션 A·B가 이미 4단계에 맞게 고쳤고(위), 이번에 남은 것은 없었다(`test_ranking.py`의 ProjectBrief STALE 테스트는 `WAITING_ASSIGNMENT`만 다루어 그대로 통과).
- **검증**:
  - 결정적: `tests/test_reassign.py` 15개(순수 규칙·자리 없음·중복 역할 용량, 그룹장 요청+대상자 동의·권한 403/404·역할 필드 400·재전송·열린 요청 하나, 새 revision과 STALE 전파 전체·삭제 없음·그룹 조회 제외·coverage 재생성·인터뷰 입력 복사, 거절, 그룹장 자신이 대상, 동시 동의, STALE coverage 호출 없음, 개인정보·로그인·CSRF·로그, 새로고침·서버 재시작 복구, ProjectBrief 변경 B8(전원 순위 재제출·읽기 전용 유지·새 INITIAL 배정·재확정 뒤 교체), 배정 뒤 순위 변경 불가, 0010 down/up, 재사용 action id).
  - 전체 결정적 테스트·해시·프롬프트·마이그레이션 결과는 아래 `세션 C 검증 실행`.
  - LIVE: 18회(호출 170~187, 누적 187), 모두 성공. 기록은 `docs/LIVE_TESTING.md` `4단계 세션 C LIVE 기록`. 이 세션은 AI 프롬프트·출력 검증 코드를 바꾸지 않았고 최종 코드로 브라우저 흐름을 LIVE로 걸었다.
  - 브라우저(Playwright Chromium, 개발 서버 포트 8001, 개발 DB `moim_dev` 0010): 두 사람이 칩으로 순위 제출 → 배정 안내(`화면 설계 · 2순위로 배정`) → AI 인터뷰(버튼 4번 + `맞아요`) → 초안 4개 → 제목 직접 수정 → 자연어 "마감을 조금 더 여유 있게 뒤로 미뤄 줘"(4개 마감 이동) → `투두 확정하기` → 완료 오버레이 → 새로고침 뒤 `확정 풀기` 복구 → 그룹 API 세 필드 → 재배정 API(동의) → 그룹 API에서 이전 확정 빠짐 → 그룹장 새 초안(인터뷰 입력 복사). localStorage·sessionStorage 비어 있음, 고친 코드에서 콘솔 오류 0.
  - 캡처(1440×900, `artifacts\screens\moim-chat.html\`): `impl-assigned.png`(배정 안내), `impl-interview.png`(인터뷰 중), `impl-todo-draft.png`(투두 초안), `impl-todo-edited.png`(수정 후), `impl-todo-confirmed.png`(확정 직후 오버레이), `impl-todo-confirmed-reload.png`(확정 뒤 새로고침), `impl-after-reassign-target.png`(재배정 뒤 대상자). 원본과 나란히 보는 `artifacts\screens\stage4-chat.html`. 디자인 눈 확인은 사용자가 한다.
  - 개발 서버는 이 세션이 띄웠다가 껐다. 포트 8000은 건드리지 않았다. 개발 DB에 일회용 사용자 `e2ec2e735f…` 2명과 그 그룹(assignment revision 3 CURRENT)이 남아 있다.
- **세션 C 범위 사용자 판단 결과**:
  - 재배정 규칙의 해석(2026-11-19 사용자 확인 "Both OK"): D-B1 B4는 "그룹장 요청 + 대상자 동의, 같은 입력으로 새 revision"까지만 정했다. 구현은 동의하지 않은 사람을 옮기지 않으려고 대상자만 본인 순위대로 옮기고, 자리가 없으면 거절한다(맞바꾸기 없음). 다른 해석(전체 재추첨 등)이 필요해지면 제품 결정이다.
  - 재배정 뒤 역할이 그대로인 구성원도 이전 확정 투두가 STALE이 되어 다시 확정해야 한다(B4 문구대로). 대화 기록(인터뷰 말풍선)은 새 revision에 복사하지 않아 그 화면 왼쪽에는 배정 안내와 투두 대화만 보인다.
  - ProjectBrief 변경 뒤 읽기 전용으로 남은 확정 투두를 그룹 응답에서 구분하는 표시가 없다(세 필드 제한). D-D1은 정하지 않았으며 5단계에서 개인정보 범위를 넓히지 않는 기술 세부로 정하고, 제품 의미가 달라지면 승인받는다.
  - D-B4 대조: 나머지는 구현과 일치한다. 세션 B의 OPEN(마감만 바뀐 투두는 숨겨진 기준을 다시 만들지 않음, 자연어 수정이 milestone 표시 투두를 milestone 뒤로 옮길 수 있음, 숙련·결과물 많은 역할의 초안 실패율)은 그대로 남아 있다. D-B4 문구는 바꾸지 않았다.

### 세션 C 검증 실행 (2026-11-19)

- 전체 결정적 테스트 456개 통과(실패·건너뜀 0, 세션 B 441 + `test_reassign.py` 15, 최종 코드). 첫 전체 실행은 내가 같은 테스트 DB에서 `test_reassign.py`를 동시에 돌려 마이그레이션 테스트끼리 간섭해 2건이 실패했다(`auth_sessions` 없음). 동시 실행 없이 다시 돌린 결과가 앞의 숫자다.
- 원본 HTML 해시 9/9 일치, `prompts/validate-prompts.ps1` 14개 PASS, `git diff --check` 깨끗함.
- 마이그레이션 upgrade → downgrade → upgrade: 0010은 `test_migration_0010_down_and_up_keeps_reassigned_revisions`, 0009·0008은 세션 A·B 테스트가 0010을 거쳐 통과. 개발 DB도 `alembic upgrade head`(0010).
- 교차 단계 회귀(`prompts/VALIDATION.md`): 전체 결정적 테스트에 1~3단계 테스트가 모두 포함된다.

### 사용자 보정 (2026-11-19)

- 중간 일정 날짜 칸: 브라우저 기본 `input[type=date]` 대신 프로젝트 기간과 같은 달력(`.range` 버튼 + `.cal` 팝업)을 쓴다. 고를 수 있는 날은 프로젝트 기간(없으면 오늘 이후)으로 제한한다(`group-create.js`, `group_create.html` CSS만 변경, 새 스타일 없음). 사용자 지시에 따른 원본 대비 차이다.
- 역할 후보 수: 프롬프트가 6~8개를 요구해 2명 팀에도 8개짜리 잘게 쪼갠 역할이 나왔다. 이제 `max(3, 인원)`~`+2`(3~8 안, D-B1 B5 범위 그대로)로 요청·검사하고, 한 사람이 하나씩 맡는 굵은 역할 단위를 요구한다. Timely 입력에 인원 수(숫자만)를 추가했다. 이미 SUCCEEDED인 기존 그룹 후보는 바뀌지 않는다(불변 규칙). 새 ProjectBrief revision부터 적용된다.
- 검증: 전체 결정적 테스트 통과, 렌더한 폼에서 달력 열기·날짜 선택·닫힘 확인. LIVE 호출은 하지 않았다.
- 2/2 제출 뒤 역할 배정이 안 되던 문제: 포트 8000 서버가 4단계 코드 이전(01:22)에 `--reload` 없이 떠 있어 배정 단계가 없는 코드로 제출을 받았다. 배정은 마지막 제출 트랜잭션에서만 돌기 때문에 그 그룹은 영원히 대기했다. 서버 시작 시 `RankingService.assign_waiting()`이 cohort 전원이 제출했는데 CURRENT 배정이 없는 ACTIVE 그룹을 같은 그룹 잠금·`assign_if_ready`로 한 번 배정하고 coverage를 백그라운드로 시작한다(두 번 뽑지 않음, 테스트 2개 추가). 개발 DB의 해당 그룹은 8001 서버 재시작 때 복구되었다.
- 그 뒤 8000에서 만든 2인 그룹(`a9db4833…`)도 옛 프롬프트로 후보 8개가 나왔다(8000이 여전히 옛 코드). 8000 서버를 종료하고 `.env`를 읽어 `--reload`로 다시 띄웠다(현재 코드 제공 확인). 그 그룹의 SUCCEEDED 후보는 불변 규칙대로 그대로이며, 새 그룹을 만들거나 ProjectBrief를 수정해야 새 규칙으로 다시 만들어진다. 새 프롬프트의 LIVE 확인 호출은 실행 권한 확인에서 막혀 하지 못했다(OPEN: 모델이 `max_count`를 넘기면 `CANDIDATES_INVALID`로 실패하고 다시 시도 버튼이 나온다).

## 5단계 실행·첨부·M2 (2026-11-19)

- 선행 게이트: GATE D(D-D1) 승인, vision 확인(GATE D LIVE 191~205)을 확인했다. M2.review는 활성(D-A8 범위)이다. 커밋하지 않았다.
- **코드**: `src/moim/execution/`(`files`·`review_ai`·`service`·`routes`), `templates/group.html`(원본 group 본문 구조: 진척도·팀 활동·팀원 카드·팀원별 할 일·팀원 완료 기록·내 할 일·업로드·완료한 할 일), `static/group-exec.js`(DOM·`textContent`만), `models.py`·`migrations/versions/0011_execution.py`, `main.py`(연결·재시작 처리), `auth/middleware.py`(업로드 본문 상한), `roles/todos.py`(`shown_confirmations` 분리, 동작 같음), `wemeet_ai/m0/client.py`(`file` 파트 거절도 `VISION_NOT_SUPPORTED`). 구조·API·테이블은 `docs/ARCHITECTURE.md` 5단계 절, 보안 값은 `docs/SECURITY.md` 4-6.
- **화면 규칙**:
  - 진척도 = 보이는 확정 투두 중 완료 수(구성원 색 막대). 팀 활동 = 날짜별 완료 기록 수(프로젝트 시작~마감, 오늘 표시, 최근 4주 합계). 팀 기여도 = 최근 4주 활동 + 완료×3(원본 식).
  - 팀원 카드의 작은 글씨는 확정 역할명(확정 전에는 기존대로 `그룹장`/`팀원`). 카드를 누르면 그 팀원의 할 일(완료·미완료·오늘 마감·마감 지남)과 기록 필터.
  - 내 할 일: 체크 칸 = 즉시 수동 완료. 마감 표시는 `MM/DD 마감`, 오늘은 `오늘 마감`(강조), 지난 마감도 강조. 확정 투두가 없으면 빈 안내 "역할과 할 일을 확정하면 여기에 보여요."와 링크 문구 `내 AI와 대화 시작하기`, 있으면 원본 문구 `AI와 역할·할 일 다시 나누기`(둘 다 `/groups/{id}/chat`).
  - 업로드: 원본 폼 그대로. `어떤 할 일인가요?`는 내 미완료 투두 + `기타`. 진행 막대는 실제 업로드 진행률. 성공 문구는 원본 그대로이고, 검토가 있으면 같은 `up-msg`에 "AI가 자료를 확인하고 있어요…" → 결과 한 줄(D3 문구), 실패면 `다시 시도`(기존 `.goal-retry` 버튼 모양).
  - 완료한 할 일 `되돌리기`: 첨부가 있으면 브라우저 기본 확인창 "첨부 자료도 삭제되며 복구할 수 없어요. 되돌릴까요?"(D5), 취소하면 변경 없음.
- **에이전트가 정한 기술 세부(가역)**:
  - 이미지 안전 상한 한 변 12,000px·총 4천만 픽셀, 압축 형식 항목 2,000개·해제 합계 200MB·항목당 100MB·압축비 150. 애니메이션 이미지·암호화 PDF는 저장·공유만 하고 M2 미지원으로 처리.
  - PDF는 문서 메타데이터를 지운 사본을 `file` 파트로 보낸다(GATE D에서 직접 판독 확인, 쪽 이미지 렌더보다 토큰이 적음). 파일명은 `evidence-N.pdf`.
  - M2는 파일이 하나 이상 있는 투두 기록에서만 돈다. 제목·설명만 있는 제출과 체크 칸 완료는 검토하지 않는다.
  - 안내 한 줄은 공급자 `user_message`를 160자로 자른 것. PASS는 고정 문구만.
  - 확정 풀기 뒤 재확정하면 같은 `task_id`의 완료는 유지된다(완료는 task 단위). 이전 revision 기준으로 보낸 요청은 409.
  - ProjectBrief 변경 뒤 읽기 전용 확정 투두: 실행 화면에서 따로 표시하지 않고 완료·첨부 허용(4단계에서 넘긴 항목을 개인정보 범위를 넓히지 않는 쪽으로 정함).
  - HWPX는 실제 한글 파일로 만든 결정적 추출 테스트가 없어 M2 미지원 그대로다(D4 조건).
- **원본과 다른 점(디자인 사실로 기록)**: 원본의 대기 중 투두(모래시계, 다른 팀원 투두 대기)는 의존 관계 데이터가 없어 구현하지 않았다. ⋯ 메뉴의 `초대코드 복사`·`할 일 다시 나누기`·`그룹 나가기`는 추가하지 않았다(나가기는 6단계). `기타`로는 파일을 첨부할 수 없다("파일은 할 일을 골라야 첨부할 수 있어요.", 5단계 프롬프트 수행 3). 파일명에 `/`가 있으면 마지막 조각만 남는다.
- **검증**:
  - 결정적: `tests/test_execution.py` 43개(화면 데이터에 숨겨진 기준·private 원문 없음, 확정 전 구성원 투두 없음, 외부인·로그아웃, 체크 완료·되돌리기·감사 기록, 정확히 하나의 제출자·task·revision 바인딩과 남의 task 거절, 재전송 멱등·중복 완료, 재확정 뒤 revision 충돌과 새로고침 복구, 기타·빈 제출·길이, 로그인·CSRF, 위험 파일 14종(확장자 위장·HTML·SVG·EXE·바이너리 TXT·깨진 PDF·빈 파일·매크로·압축 폭탄·해상도·픽셀 폭탄·가짜 HWP), 크기·개수·본문·그룹 합계, 11형식 수용, 경로 조작·XSS 파일명, 다운로드 헤더·교차 그룹·나간 사람, 남의 검토·되돌리기 404, 첨부 되돌리기 재확인·영구 삭제, M2 입력 경계(심어 둔 private fixture)·이미지 2048px·EXIF 제거·로그, M2가 기준·완료를 바꾸지 않음(PASS·REVIEW_NEEDED·NEEDS_INPUT 문구), 모순·근거 없는 결과 실패와 사용자 재시도, 공급자 실패 표시, 미지원·혼합(고정 문구)·한도 초과, PDF 메타데이터 제거·DOCX 텍스트·문서 속 이미지 안내, M2 비활성, 재시작 복구, 검토 중 되돌리기, 활동·진척도, 템플릿·JS 금지 문자열, 0011 down/up).
  - 전체 결정적 테스트·해시·프롬프트: 아래 `5단계 검증 실행`.
  - LIVE: 7회(호출 206~212), `docs/LIVE_TESTING.md` `5단계 M2.review LIVE 기록`. 7/7 성공·기대 판정 일치·결정적 검사 통과.
  - 브라우저(Playwright Chromium, 개발 서버 포트 8001 `WEMEET_OFFLINE=1`, 개발 DB `moim_dev` 0011, `capture_screens.py --execution` fixture): 팀원 카드 → 팀원별 할 일, 파일 업로드(파일명 `시안_<b>x</b>.png`) → "AI가 자료를 확인하고 있어요…" → 공급자 차단으로 실패 문구와 `다시 시도`(숨기지 않음) → 내 기록 줄에 같은 문구, 파일명은 글자로만 표시(삽입된 요소 0), 체크 칸 완료, 첨부 있는 완료 되돌리기 확인창, 새로고침 뒤 복구. 콘솔 오류 0, localStorage·sessionStorage 비어 있음.
  - 캡처(1440×900, `artifacts\screens\moim-group.html\`): `original.png`, `impl.png`(실행 화면), `impl-member.png`(팀원별 할 일), `impl-upload.png`(업로드 뒤 `up-msg`), `impl-feed-open.png`(내 기록 상세의 AI 줄), `impl-full.png`(전체 페이지). 나란히 보기는 `artifacts\screens\index.html`. 사용자의 요청에 따라 에이전트가 2026-11-19에 원본·구현과 추가 상태를 직접 열어 디자인 확인을 완료했다.
  - 개발 서버 8001은 이 세션이 띄웠다(종료 예정). 포트 8000은 건드리지 않았다. 개발 DB의 fixture 그룹(`shot…` 사용자)에는 확정 투두·기록이 있다. 확정 투두는 DB 트리거로 지울 수 없어 다음 `capture_screens.py` 실행은 그 사용자들을 `oldshot…`으로 바꾸고 로그아웃시킨 뒤 새로 만든다(개발 전용).
- **후속 품질 메모(5단계 완료를 막지 않음)**:
  - ~~팀원 이름 옆 확정 역할·투두·완료 기록 표시~~ → 사용자 승인 완료(D-E1, 2026-11-19, "네, 그대로 승인").
  - M2 품질: 행위 확인형 기준("팀 공유 폴더에 올렸다")은 자료로 확인할 수 없어 M2가 본인 진술을 근거로 PASS를 낸다(LIVE 212). 4단계 기준 생성을 자료로 확인 가능한 기준 쪽으로 바꿀지는 품질 판단이다.
  - HWPX M2 지원은 실제 한글 파일 표본으로 추출 테스트를 만든 뒤에 켤 수 있다.

### 5단계 검증 실행 (2026-11-19)

- 전체 결정적 테스트 501개 통과(실패·건너뜀 0, 9분 35초, 최종 코드, 동시 실행 없음). 첫 전체 실행에서 2단계 경계 테스트 `test_groups.py::test_group_page_matches_the_original_structure`가 "5단계 전에는 실행 영역이 없다"를 검사해 실패했다. 같은 의미를 유지해 그룹 화면에는 실행 영역 6개가 있고 대시보드에는 여전히 없음을 검사하도록 고쳤다(약화 아님). 같은 파일의 정적 파일 검사 목록에 `group-exec.js`를 더했다.
- 디자인 완료 처리 직전 `tests/test_execution.py tests/test_groups.py`를 다시 실행해 통과했다. 샌드박스 안 첫 실행은 로컬 PostgreSQL 포트(54329) 연결이 `Permission denied`로 차단돼 테스트 setup에서 종료됐고, 같은 명령을 로컬 DB 연결 허용 상태로 재실행하자 코드 변경 없이 통과했다.
- 원본 HTML 해시 9/9 일치, `prompts/validate-prompts.ps1` 14개 PASS, `git diff --check` 깨끗함.
- 마이그레이션 0011 upgrade → downgrade(0010) → upgrade는 `test_migration_0011_down_and_up`, 0008~0010 테스트도 0011을 거쳐 통과. 개발 DB `moim_dev`도 0011.
- 교차 단계 회귀: 전체 결정적 테스트에 1~4단계 테스트가 모두 포함된다.

## 6단계 마이페이지·계정 생명주기 (2026-11-19)

- 선행 게이트: GATE D(D-D1) D6·D7 승인 기록을 확인했다. 작업 중 원본과 다른 화면 세 가지(이메일 줄 제거, 나가기·탈퇴 재확인 방식, 사이드바 링크 복원·헤더 유지)를 사용자에게 물어 D-F1로 승인받았다(`docs/PRODUCT.md`).
- **코드**: `src/moim/account/`(`service.py`·`lifecycle.py`·`routes.py`), `templates/mypage.html`(원본에서 스크립트로 생성: CSS·마크업 그대로, 데이터 자리만 교체), `static/mypage.js`, `static/group-leave.js`, `migrations/versions/0012_account_lifecycle.py`, 현재 활성 구성원 게이트(`groups.service.active_cohort_query`를 순위·인터뷰·배정·후보·재배정·그룹 조회가 사용), 실행 화면의 나간 사람 기록(`who: "left"` → "나간 팀원"), `auth/errors.py`(오류 본문에 `impact`), `auth/throttle.py`(`REAUTH_USER`), `execution/files.py`(삭제 결과 반환, 그룹 폴더 삭제), dashboard·group 사이드바 `마이페이지` 링크, group ⋯ 메뉴(모든 구성원, `프로젝트 정보 수정`은 그룹장만). 구조는 `docs/ARCHITECTURE.md` 6단계 절, 보안 값은 `docs/SECURITY.md` 4-7.
- **생명주기 흐름**:
  - 그룹 나가기: ⋯ → `그룹 나가기` → 서버 영향 미리보기 → 브라우저 확인창(취소=변경 없음) → 한 트랜잭션: 내 private 대화·MemberState·인터뷰·AI 수정 요청·미확정 투두 삭제, snapshot 내용 비움, 열린 재배정 요청 정리, 탈퇴 → 그룹장이면 가장 먼저 합류한 남은 사람에게 승계 → 남은 사람이 없으면 그룹 전체(행·파일) 삭제 → 배정 전이면 남은 cohort로 배정 재확인. 완료 기록·첨부는 "나간 팀원"으로 남는다.
  - 계정 삭제: `계정 관리` → `탈퇴하기` → 패널에 영향 요약 → 현재 비밀번호 → `회원 탈퇴` → 한 트랜잭션: 모든 그룹 나가기(초대방 포함, 닫힌 내 초대방 삭제) → 모든 그룹의 내 기록·첨부·투두·남은 개인 데이터 삭제 → 팀 보관 행을 새 id tombstone으로 이동 → 계정·세션 삭제(아이디 즉시 해제) → 랜딩.
  - 비밀번호 변경: 현재 비밀번호 확인 → 새 Argon2id 해시 → 모든 세션 폐기 → 이 기기만 새 세션·CSRF.
- **삭제 영향과 복구 가능성**: 나가기·계정 삭제·그룹 삭제는 복구할 수 없다(백업 없음). 실패는 전부 롤백되며 부분 성공을 응답하지 않는다. 파일은 커밋 뒤 삭제(실패 시 로그, 접근 불가). 보관되는 것: 감사 `lifecycle_events`·`execution_events`(메타데이터), 배정 기록·cohort 이력(나간 사람은 그대로, 삭제 계정은 tombstone id), 나간 사람의 완료 기록·첨부(계정 삭제 시 삭제).
- **에이전트가 정한 기술 세부(가역)**: 이름 규칙은 가입 규칙(30자, 제어 문자 금지)+원본 마이페이지의 2자 이상, 입력칸 `maxlength`는 원본 12 → 30(가입 허용 길이와 맞춤, 화면 변화 없음). 재인증 잠금 15분 10회. 비밀번호 변경 응답이 새 CSRF 토큰을 본문으로 준다(쿠키는 HttpOnly). 로그아웃 뒤 이동은 원본 주석대로 랜딩(`/`). 그룹 목록 점 모양은 원본처럼 a·c·q 순환. 배정 기록이 가리키지 않는 나간 사람의 순위는 삭제. 재배정 새 revision에는 나간 사람을 옮기지 않는다(옛 revision 행은 그대로). 마감이 지난 그룹은 `D+N`.
- **원본과 다른 점(디자인 사실)**: 이메일 줄 없음·사이드바 작은 글씨는 아이디(D-F1), 탈퇴 패널 안 영향 문단 한 줄(D-F1, 기존 `.leave p`), 그룹 ⋯ 메뉴의 `초대코드 복사`·`할 일 다시 나누기`는 여전히 없음(5단계 기록), 나가기 확인은 브라우저 기본 확인창(D-F1). 그룹 목록 빈 상태는 기존 `.grp` 줄에 "참여 중인 그룹이 없어요."
- **검증**:
  - 결정적: `tests/test_lifecycle.py` 21개(마이페이지 로그인·내 정보만·XSS 이스케이프·이메일 없음, 그룹 진행 수, 이름 규칙·CSRF·추가 필드 400·로그, 비밀번호 변경 재인증·세션 회전·새 해시·로그인, 재인증 잠금과 잠금 중 정답 거절, 나가기 미확인 무변경·영향, IDOR 404, 나간 사람 접근 종료·private 삭제·기록 보존·"나간 팀원"·재추첨 없음, 승계, 마지막 구성원 그룹 삭제+무관한 팀 백업 덤프 동일, 배정 전 나가기 뒤 배정, 재배정 요청 정리, 탈퇴 비밀번호·확인, 탈퇴 전역 삭제·tombstone·삭제 id가 DB에 0행·아이디 재사용·로그, 먼저 나간 그룹 기록 삭제, 혼자 남은 그룹 삭제·대기방 이탈, 초대방 승계·닫힌 방 삭제, 중간 실패 롤백(행·파일), 트리거 범위, 정적 파일 금지 문자열, 원본 마크업 유지, 0012 down/up과 tombstone 시 downgrade 거절).
  - 전체 결정적 테스트·해시·프롬프트: 아래 `6단계 검증 실행`.
  - 캡처(1440×900, 이 worktree의 `artifacts\screens\`): 9쌍 전체를 새로 찍었고 `moim-mypage.html\`에 `original.png`·`impl.png`(내 정보), `original-grp.png`·`impl-grp.png`(참여 중인 그룹), `original-acc.png`·`impl-acc.png`(탈퇴 패널 열림, 영향 문단), `moim-group.html\`에 `original-menu.png`·`impl-menu.png`(⋯ 메뉴)와 `impl-leave-confirm.txt`(확인창 문구, 취소 뒤 그대로인 것 확인). 나란히 보기: `artifacts\screens\index.html`. 사용자 눈 확인 완료("Design OK").
  - 개발 서버: 이 worktree 코드로 포트 8001(`WEMEET_OFFLINE=1`, 확인 뒤 종료), 개발 DB `moim_dev`를 0012로 올렸다(추가만, tombstone 없을 때 downgrade 가능). 캡처 시드가 이전 `shot…` 사용자를 `oldshot…`으로 바꿨다(개발 전용). 이 과정에서 캡처 스크립트의 이름 길이 버그(`oldshot`+12자 > 16자, 처음 실행된 경로)를 고쳤다.
- **남은 제한**: `docs/SECURITY.md` 4-7(백업 없음, 파일 삭제 실패 시 고아 파일, 대기방 스스로 나가기·수동 위임 화면 없음, 검색 기능 없음).

### 6단계 검증 실행 (2026-11-19)

- 전체 결정적 테스트 522개 통과(종료 코드 0, 기존 501 + `test_lifecycle.py` 21). 중간에 `test_execution.py` 43개와 `test_lifecycle.py`를 따로 반복 실행했다. 첫 lifecycle 실행의 실패 3개는 테스트 도우미가 감사 로그 `lifecycle_events`(의도적으로 보관)를 그룹 행으로 센 것이었고 도우미만 고쳤다.
- 원본 HTML 해시 9/9 일치, `prompts/validate-prompts.ps1` 14개 PASS, `git diff --check` 깨끗함(편집 스크립트가 만든 CRLF를 LF로 되돌린 뒤).
- 마이그레이션 0012 upgrade → downgrade(0011) → upgrade와 tombstone이 있을 때 downgrade 거절은 `test_migration_0012_down_and_up`. 개발 DB `moim_dev`도 0012.
- 브라우저(Playwright Chromium, 개발 서버 8001, 일회용 계정): 가입 → 이름 변경(`<b>`는 글자로 표시) → 틀린/맞는 현재 비밀번호 → 세션 회전 뒤 다음 저장도 CSRF 통과 → 탈퇴는 옛 비밀번호 거절·새 비밀번호로 완료 → 랜딩 → `/mypage`는 로그인으로. localStorage·sessionStorage 비어 있음. 콘솔에는 일부러 틀린 비밀번호의 401 응답 2건만.

## 7단계 운영 준비 (2026-11-19~20)

- 판정: **CONDITIONAL PASS**. 코드·도구·절차 쪽 검증은 모두 통과했고 고위험 미해결 결함은 없다. 백업 보관 기간·삭제 전파는 D-G1(2026-11-20)로 승인·구현·리허설까지 끝났다. 남은 조건은 배포 대상 결정(BLOCKED, 외부 계정·비용) 하나이며, 그 환경에서 TLS·비밀 저장소·로그/경보를 확인하기 전에는 운영 출시를 PASS로 보지 않는다.
- **D-G1 구현(2026-11-20, 사용자 "추천안 승인")**: `account/ledger.py` 삭제 기록(DB·첨부 밖, 하루 파일, 7일 자동 삭제, 내부 id·시각만). 그룹 나가기·계정 삭제·`되돌리기`가 같은 트랜잭션 안에서 커밋 직전 마지막 단계로 한 줄을 fsync로 쓰고, 쓸 수 없으면 삭제를 되돌리고 503 `DELETION_UNAVAILABLE`(기존 오류 자리에 "지금은 처리할 수 없어요. 잠시 후 다시 시도해 주세요."), 커밋이 실패하면 취소 줄을 붙인다(fail closed). `LifecycleService.replay`와 `tools/ops_db.py replay-deletions`(복원 뒤, 앱 시작 전), `ops_db.py prune`(백업 머리표가 있는 7일 지난 파일만), 운영 시작 점검 `DELETION_LEDGER_DIR_*` 3개. 승인 문장은 나가기·탈퇴였고, 같은 장치로 D5 `되돌리기`(첨부 영구 삭제)도 복원 뒤 다시 지우게 넓혔다(노출을 줄이는 쪽의 가역적 기술 선택, 제품 동작 변화 없음). 리허설: 백업 뒤 나가기 1·탈퇴 1 → 복원하면 되살아남(삭제 계정 id가 23개 표, canary 7행) → 재처리 2건 → 0개 표·canary 0 → 두 번째 실행 변화 없음. 증거 `artifacts\ops\rehearsal-replay-2026-11-20.txt`(worktree 밖 main 쪽 폴더).
- **고친 것(코드)**:
  - `execution/files.py`·`wemeet_ai/m2/evidence.py`: `Image.open(..., formats=...)`로 선언 형식 디코더만 쓰게 했다. 전에는 `.png`로 올린 TIFF·EPS·GIF 바이트가 그 디코더를 먼저 거쳤다. Pillow `>=13.0,<14`(12.3.0 공개 취약점 42건).
  - `auth/middleware.py`: 본문 상한이 없던 경로에 상한(`/api/account` 8KB, 기본 64KB). FastAPI가 인증 전에 본문을 파싱하기 때문(독립 검토의 중간 등급 발견).
  - `main.py`: 핸들러가 없으면 `/api/jobs` 라우터를 붙이지 않는다(404). `tests/test_jobs.py`의 기대값을 422 → 404(제출·조회·재시도 모두, 라우트 없음)로 바꿨다(약화 아님).
- **새 코드**: `observability.py`(요청 id 미들웨어·`X-Request-ID`·경로 템플릿 접근 로그·JSON 형식), `preflight.py`(운영 시작 점검), `tools/ops_db.py`(역할 분리·백업·복원·검증·고아 정리), `tools/rehearse_restore.py`, `tools/drill_ai_failure.py`, `tools/load_sample.py`, `constraints.txt`(깨끗한 venv에서 해석한 정확한 버전), `pyproject.toml` extra `ops`(cryptography, 백업 도구 전용).
- **에이전트가 정한 가역적 기술 결정**: 운영 구성(프록시 TLS + uvicorn 1프로세스 + `--proxy-headers`), owner/app 역할 분리, 백업 형식(AES-256-GCM 조각), 경보 기준값, RLS 도입 안 함, 악성 파일 검사 첫 출시 도입 안 함(근거는 `docs/SECURITY.md` 4-8), 성능 변경 없음.
- **검증**:
  - 기준선: 시작 시 전체 결정적 테스트 522개 통과(이 worktree, 변경 전).
  - 깨끗한 환경: 추적·새 파일만 복사한 폴더에 새 venv(`pip install -e ".[test,visual,ops]"`, FastAPI 0.165.0·Pillow 13.0.0이 새로 해석됨)로 전체 결정적 테스트 — 결과는 아래 `7단계 검증 실행`.
  - 복원 리허설 PASS, 장애 훈련 PASS, 부하 표본(10·40 가상 사용자, 오류 0) — 수치는 `docs/OPERATIONS.md` 5·6·8절, 증거는 이 worktree 밖 `C:\Users\user\Desktop\moim-design\artifacts\ops\`.
  - 보안 스캔: pip-audit(남은 것 pytest 테스트 전용 1건, 오탐 판정), bandit(`src/` 중간·높음 0, 도구의 중간 16건은 개발 시드 상수 SQL 오탐). 근거는 `docs/SECURITY.md` 4-8.
  - 원본 HTML 해시 9/9, 프롬프트 검증 14개, `git diff --check` 깨끗함(`evidence.py`는 baseline CRLF였고 수정하면서 LF로 정상화).
  - 1440×900 캡처: 깨끗한 환경 코드의 개발 서버(포트 8003, 오프라인 스위치)로 9쌍 전부, 모두 1440×900. 이 worktree의 `artifacts\screens\index.html`. 화면 코드는 바꾸지 않았다(디자인 변화 없음). 개발 DB `moim_dev`의 `shot…` 시드가 다시 만들어졌다.
- **환경 영향**: 공용 `.venv`에 잠시 Pillow 13·cryptography를 설치했다가 원래대로 되돌렸다(Pillow 12.3.0, cryptography 없음). 병합 뒤 공용 venv는 `pip install -e ".[test,visual,ops]" -c constraints.txt`로 맞춘다. 로컬 클러스터에 만든 리허설·훈련·부하 DB와 역할은 모두 지웠다.
- **달랐던 사실**: 활성화 한 번의 팀 목표·역할 후보 호출은 같은 요청의 백그라운드 작업이라 순서대로 실행된다(팀 목표가 느리면 후보도 늦게 시작). 리허설 검사 스크립트의 결함 두 개(같은 fixture 문장, 완료 필드 이름)는 고쳤고 제품 결함은 아니었다.

### 7단계 검증 실행

- 깨끗한 환경 1차(추적·새 파일만 복사한 폴더, 새 venv): 548 통과·2 실패. (1) `test_original_htmls_are_untouched_by_this_stage`는 복사 폴더가 Git 저장소가 아니라 `git status`가 128로 끝난 것(환경 원인, 해시 검사 자체는 9/9 통과). (2) `test_reassign.py::test_privacy_login_csrf_and_logs`는 새 `test_operations.py`의 로깅 테스트가 루트 로거 수준을 INFO로 바꾸고 되돌리지 않아, 뒤 테스트의 준비 단계 INFO 로그(정책상 허용된 `user_id`)가 잡힌 순서 의존 문제. 제품 누출이 아니며 테스트가 수준을 복원하게 고쳤다.
- 최종: 같은 새 venv로 이 worktree(Git 저장소)에서 전체 결정적 테스트 **550개 통과**(실패·건너뜀 0, 10분 8초). 기존 522 + `test_operations.py` 17 + `test_ops_tools.py` 11.
- D-G1 뒤(2026-11-20): 같은 venv로 이 worktree 전체 결정적 테스트 **565개 통과**(실패 0). 550 + `test_deletion_ledger.py` 14 + 운영 시작 점검 삭제 기록 1. 원본 HTML 해시 9/9(`tools/check_design_hashes.py`), `git diff --check` 깨끗함. 복원 리허설(재처리 포함) PASS.

## D-H1 완료 방식·그룹 삭제·대시보드 (2026-11-20)

- **결정**: `docs/PRODUCT.md` D-H1(D-D1 D2·D3 개정). 헌법 문장은 바꾸지 않았다(1절의 "PASS의 완료 반영 방식" 위임 범위).
- **코드**: `migrations/versions/0013_completion_votes.py`(`execution_records.completion`·`completed_by`, `marks_done = completion='DONE'` 검사, 할 일당 열린 시도 하나 unique index, `execution_votes`, 이벤트 `VOTE`·`SETTLED`), `execution/votes.py`(집계·결정 규칙), `execution/service.py`(시도 생성·M2 결과 반영 `_decide`·투표·재시작 처리·`dashboard`), `execution/routes.py`(`POST …/records/{id}/vote`, `GET /api/dashboard`), `account/lifecycle.py`(나가기 뒤 `settle_group`, `delete_group`·미리보기·재실행), `account/routes.py`(`GET/POST /api/groups/{id}/delete`), `account/ledger.py`(`GROUP_DELETED`), `templates/group.html`(체크 칸 → 안내 문구, ⋯ `그룹 삭제`, 상태 배지·투표 줄 스타일), `static/group-exec.js`, `static/group-leave.js`, `templates/dashboard.html`·`static/dashboard.js`(오른쪽 패널·팀 소식·카드 줄), 로고 링크 3곳.
- **화면**: 내 할 일의 체크 칸은 누르면 올리기 폼을 그 할 일로 맞추는 버튼이다. AI·팀원 확인 중인 할 일은 원본 `대기 중` 모양(`todo.waiting`). 팀원 완료 기록 줄에 `확인 요청`·`AI 확인 중`·`반려` 배지, 상세에 AI 사유 한 줄과 `동의`/`거절` 버튼(`goal-retry` 스타일). 새 디자인 시스템은 만들지 않았다.
- **테스트**: `tests/test_completion_votes.py` 12개 추가, `tests/test_execution.py`의 D-D1 D2 전제 테스트를 D-H1 의미로 바꿨다(체크 완료 테스트 → 파일 없는 완료 거절·PASS 완료, 보류 → 투표 등).
- **남은 것**: 원본과 다른 화면(체크 칸 대신 안내 문구, 투표 줄, `그룹 삭제` 메뉴, 패널의 `팀원 확인 요청`)의 사용자 눈 확인. M2 LIVE 호출은 하지 않았다(로직만 바뀜).

## 더블클릭 로컬 실행기 (2026-11-20)

- 결과: 루트 `start-moim.cmd`(설치·DB·마이그레이션·서버·브라우저)와 `setup-api-key.cmd`(Timely 키를 숨김 입력으로 `.env`에 저장), 사용 안내 `README.md`. 스크립트는 `tools/launcher/`(`start.ps1`, `set-api-key.ps1`, `common.ps1`, `ensure_db.py`). 제품 코드·원본 HTML 변경 없음. `.gitattributes`에 `*.cmd`·`*.ps1` CRLF 추가(PowerShell 5.1이 한글을 읽도록 `.ps1`은 UTF-8 BOM).
- 에이전트가 정한 가역적 결정: 키는 `.env`(Git 제외)에 저장하고 실행기가 환경변수로 불러온다. 앱이 `.env`를 읽지 않는 방침은 그대로다. 로컬 DB는 폴더 안 `.local\pgdata`에 `initdb`(scram-sha-256, 무작위 비밀번호)로 만들고 127.0.0.1에서만 받는다. 포트는 `.env` URL의 값을 쓰고, 포트를 다른 PostgreSQL이 쓰고 저장소가 없으면 그 DB를 그대로 쓴다. 서버 포트는 8000부터 빈 곳.
- 검증: `.git`·`.venv`·`.local`·`.env` 없는 새 복사본에서 실행 → venv·패키지 설치, `.env` 생성, initdb, PostgreSQL 시작, `moim_dev` 생성, `alembic upgrade head`, `/healthz` 200, `/` 200. `.env` 키 덮어쓰기 helper 확인. 숨김 입력 프롬프트 자체는 대화형이라 직접 실행하지 않았다.
- 달랐던 사실: `pg_ctl start` 출력을 파이프로 받으면 PostgreSQL이 핸들을 물려받아 끝나지 않았다 → 별도 프로세스로 대기하도록 고침.

## 다음 한 단계

**다음 한 단계: 사용자의 1440×900 캡처 눈 확인 → 이 브랜치(`claude/rag-screen-eval-design-28a860`, 8A~8D 포함)를 `main`에 병합.**

이전 메모(8A): 다음 한 단계는 `prompts/08B-rag-understanding.md`였다.

이전 메모(7단계): 사용자가 배포 대상(호스팅·도메인·비밀 저장소·로그/경보)을 정하면, 그 환경에서 `docs/OPERATIONS.md` 2·7절의 BLOCKED 항목과 삭제 기록 저장소(`MOIM_DELETION_LEDGER_DIR`, DB·첨부와 다른 내구성 저장소)를 검증한다.

이전 메모: 1~6단계 MVP 범위가 끝났다. 7단계(`prompts/07-production-readiness.md`)는 실제 배포를 결정할 때만 진행한다(MVP 완료 조건 아님).

이전 메모(6단계 시작 전): 다음 단계는 6단계 마이페이지와 계정 생명주기였다. 아래는 5단계 시작 전 메모다(처리 결과는 위 `5단계 실행·첨부·M2` 절): 이전 다음 단계는 5단계 실행과 투두별 M2 평가였다. 5단계로 넘긴 기술 세부는 이미지 해상도·총 픽셀 안전 상한, 압축 해제 상한, HWPX 추출 결정적 테스트, `TimelyClient`의 `file` 파트 거절 분류, `task_id`·revision 노출과 ProjectBrief 변경 뒤 읽기 전용 확정 투두 표시였고, 위 5단계 절에 처리 결과와 후속 품질 메모를 기록했다.

이전 메모(세션 B 시작 전): 4단계 세션 B(`prompts/04-split/04B-todos-confirm.md`, 수행 7~10)의 입력 연결 키는 위 `4단계 진행 > 세션 A 완료 > 세션 B가 읽을 연결 키`에 있다. 아래 목록은 4단계 시작 전에 적어 둔 메모이며, 세션 A에서 처리된 항목(배정 입력 기준, `ROLE_TODOS` 추가, 배정 뒤 인터뷰)은 위 절이 현재 사실이다.

4단계 구현 전 메모(2026-11-19, 세션 A 이전):

- 배정은 `preference_submissions`의 최신 revision 중 `source_candidate_revision`이 현재 ProjectBrief revision인 것만 입력으로 쓰고, `member_progress.status = WAITING_ASSIGNMENT`인 cohort 전원이 모였을 때만 실행한다(`interview/ranking.py`의 집계 쿼리와 같은 기준). 후보 id(`r1`…)는 revision 안에서만 유일하다.
- **배정 뒤 인터뷰(D-B3)**: 배정이 끝난 각 사용자가 본인에게만 보이는 AI와 배정 역할에 맞는 짧은 인터뷰를 한 뒤 투두 초안을 만든다. 경험·주간 가능 시간·도구·일정·환경 제약·선호 시간만 묻고 배정 역할을 바꾸지 않으며 순위 원문은 AI 입력에 넣지 않는다. 항목·턴·종료 조건이 제품 의미를 바꾸면 사용자 승인이 필요하다. `interview/coach.py`·`turns.py`와 보존 테이블은 재사용할 수 있지만 D-B2의 옛 질문 항목(필수 4개)·턴 한도(10)·종료 버튼 규칙은 D-B3로 개정됐으므로 그대로 쓰지 말고 4단계에서 다시 정한다. 다시 연결할 때 `coach.py`·`turns.py` 테스트를 새로 쓴다.
- 투두 초안 공급자 입력은 D-B1 B3를 따른다(`constraints`·순위 원문·대화 원문 금지). `member_progress.status`는 지금 `INTERVIEW | WAITING_ASSIGNMENT` 두 값뿐이다(`ROLE_TODOS`와 배정 뒤 인터뷰 상태는 4단계가 추가).
- `/api/jobs`는 여전히 인증이 없고 핸들러도 없다. 4단계가 범용 작업 API를 쓰려면 `auth/deps.py`의 `require_user`·`verify_csrf`와 소유자 검사를 함께 넣는다(전용 상태 테이블을 쓰면 필요 없다).

## OPEN

`docs/PRODUCT.md` 5절을 본다. GATE B(D-B1)·GATE D(D-D1)와 6단계 화면 차이(D-F1)는 승인 완료이고, 제품 OPEN 항목은 남아 있지 않다. BLOCKED 항목은 없다. (2026-11-19 MVP 마감 보정: 해결된 3단계 인터뷰 메모와 "1단계 전에 확인" 메모를 지우고 현재 사실로 바꿨다.)

7단계 OPEN/BLOCKED (2026-11-20):

- **해결(APPROVED D-G1, 2026-11-20): 백업 보관 기간과 삭제 전파.** 롤링 7일, 장애 복구 전용, 복원 뒤 삭제 기록으로 나가기·탈퇴·되돌리기 재처리. 구현·테스트·리허설 완료(위 `7단계 운영 준비` 절).
- **BLOCKED: 배포 대상.** 호스팅·도메인·TLS 인증서·비밀 저장소·로그 수집·경보 채널. 외부 계정과 비용이 들어 사용자가 정한다. 정해지면 `docs/OPERATIONS.md`의 해당 절을 그 환경에서 검증한다.
- **OPEN: Timely 금액 기준 비용** — 요금표 확인 필요(토큰 추정은 `docs/OPERATIONS.md` 8절).
- 7단계에서 처리한 이전 이월 항목: DB 역할 분리(도구·리허설), constraints 고정(해시 고정은 미적용: pip-tools 필요), `/api/jobs`(비노출), 프록시 주소(uvicorn 설정 검증), 백업 없음(도구·리허설), 고아 파일(정리 도구). 남은 것: LIVE 회귀(반복·한국어·긴 구조화 출력)와 토큰 상한 근거, 캡처 스크립트 "구현 없음" 보고 강화, 다중 프로세스 작업 소유, 대기방 스스로 나가기·수동 위임 화면(제품 기능, 범위 밖).

이전 이월 기록(7단계 전):

- PostgreSQL 역할 분리(migration·제한된 앱·테스트 역할)와 새 환경 DB 준비 스크립트·실행 문서. 지금은 `moim` superuser 하나다(0B 보정 때 진행하다 되돌렸고 로컬 클러스터는 역할 `moim` 하나로 복구).
- 의존성 constraints/lock 고정, LIVE 회귀(반복·한국어·긴 구조화 출력·실제 Runtime 경로)와 토큰 상한 근거, 캡처 스크립트의 "구현 없음" 보고 강화.
- 범용 작업 API `/api/jobs`에는 인증·인가·CSRF가 없다. 등록된 핸들러가 없어 지금은 작업을 만들 수 없다(2~6단계는 전용 상태 테이블을 썼다). 핸들러를 등록하는 단계에서 `auth/deps.py`의 `require_user`·`verify_csrf`와 소유자 검사를 함께 넣는다(`docs/SECURITY.md` 4-1).
- 서버가 둘 이상이면 시작할 때 서로의 진행 중 작업을 실패로 바꾼다. MVP는 프로세스 하나를 전제한다.
- `docs/SECURITY.md` 4-7의 6단계 남은 제한(백업 없음, 파일 삭제 실패 시 고아 파일, 대기방 스스로 나가기·수동 위임 화면 없음).

설정 사실(확인됨):

- 앱은 `.env`를 자동으로 읽지 않는다. `MOIM_ENV`·`MOIM_DATABASE_URL`을 환경변수로만 읽고 없으면 `ConfigError`로 실패한다(의도된 방침, 1단계부터 그대로). 테스트는 `tests/conftest.py`가 `.env`의 `MOIM_TEST_DATABASE_URL`을 읽는다.

환경 사실(2026-11-19 확인):

- 프로젝트 `.venv`는 정상이다(`C:\Python312` 3.12.8 기반, `include-system-site-packages = false`). 테스트는 `.\.venv\Scripts\python.exe -m pytest`로 실행한다.
- PATH의 맨 앞 `python`은 MOIM과 무관한 다른 도구의 가상환경(`hermes-agent`, 없는 `C:\python_cs`를 가리킴)이라 맨 `python` 명령은 실패한다. MOIM 문제는 아니며 `.venv` 경로를 직접 쓴다.
- 시스템 Python(`C:\Python312`)에는 과거 `wemeet-ai-core 0.10.1`이 설치돼 있다. `.venv`는 시스템 패키지를 보지 않고 pytest는 `src`를 맨 앞에 두므로 MOIM 실행·테스트에 영향이 없다. 다른 작업에서 쓰는지 알 수 없어 지우지 않았다.

달랐던 사실:

- 크리퍼 `test_unified_storage.py`의 DB 역할 권한 검사는 이 환경의 `moim` 역할이 superuser여서 실패한다. MOIM의 앱 DB 역할 분리는 위 7단계 항목이다.
