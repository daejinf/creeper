# WE-MEET AI Prototype

새 M0/M1 개발 기준은 **[독립 AI 코어](ai-core/README.md)** 입니다. `ai-core/`는 기존 서비스와 분리되어 DB 없이 CLI와 최소 웹(8010)에서 실행됩니다. 아래 내용은 기존 프로토타입 설명입니다.

M0 대화 평가·의사결정 기반과 M1 합의 근거 기반 3단계 계획 흐름을 구현한 프로토타입입니다.

## 포함된 기능

- 자유로운 그룹 대화는 AI 호출 없이 저장하고, 5개 발언이 쌓이면 평가를 제안
- `AI 대화 평가`에서 전체 대화를 한 번에 분석해 목표·기간·빈도·인증·공개 범위 안건을 생성
- 구성원 제안, AI 추천·절충안, 과반수 투표와 재논의를 포함한 의사결정 보드
- 필수 안건과 비공개 개인 문답이 준비된 뒤 명시적으로 계획 초안을 생성
- 닉네임·아바타 기반 화면과 개인 원문을 숨기는 공개 범위 분리
- 주차별 계획, 첫 7일 과제, 적응 규칙을 포함한 3단계 계획
- DeepSeek Responses API provider와 명시적 테스트 환경에서만 사용하는 결정론적 mock provider
- Pydantic JSON Schema 및 날짜·구성원·근거·프라이버시 결정론적 검증
- 최대 2회 재시도 후 안전한 `FAILED` 처리
- 계획 버전, 승인 멱등성, AI 실행 로그, 50개 계획 + 30개 코칭 시나리오 평가셋
- 투표 전 비공개·확정 후 닉네임별 공개, 비공개 문답, 계획 검토 UI와 AI Lab

## 구조

```text
backend/   FastAPI, SQLAlchemy, Alembic, APScheduler, evaluation dataset
frontend/  React, TypeScript, Vite
```

## 로컬 실행

1. 환경 파일을 준비합니다.

```powershell
Copy-Item .env.example .env
```

2. PostgreSQL을 실행합니다.

```powershell
docker compose up -d postgres
```

3. 백엔드를 설치하고 마이그레이션한 뒤 실행합니다.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

4. 다른 터미널에서 프론트엔드를 실행합니다.

```powershell
cd frontend
npm install
npm run dev
```

브라우저에서 `http://localhost:5173`을 열고, API 문서는 `http://localhost:8000/docs`에서 확인합니다.

## DeepSeek 연결

일반 실행은 `AI_PROVIDER=deepseek`가 기본입니다. 일반 메시지 저장은 DeepSeek 연결과 독립적으로 동작합니다. `.env`에 `DEEPSEEK_API_KEY`가 없거나 연결이 실패하면 기존 대화·평가·투표는 유지하고 새 평가 및 계획 생성만 중단합니다. mock은 자동 대체되지 않으며 테스트할 때만 `AI_PROVIDER=mock`을 명시해야 합니다. 이 경우 UI에 항상 `TEST MOCK` 배지가 표시됩니다. 키와 원본 사용자 입력은 로그에 저장하지 않으며, 이메일과 휴대전화 번호는 모델 호출 전에 마스킹합니다.

실제 API로 평가 케이스를 실행하면 비용이 발생할 수 있으므로 로컬 자동화 검증은 명시적 mock 모드에서 수행할 수 있습니다.

## 검증

```powershell
cd backend
pytest

cd ..\frontend
npm run typecheck
npm run build
```

프로덕션에서는 APScheduler를 여러 웹 워커 안에서 동시에 실행하지 마세요. 이 프로토타입은 단일 프로세스를 전제로 하며, SW팀 이관 시 예약 작업을 별도 worker로 분리해야 합니다.

