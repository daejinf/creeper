# MOIM — 내 PC에서 실행하기 (Windows)

PowerShell 명령을 직접 칠 필요 없이 **더블클릭**으로 실행합니다.

## 0. 처음 한 번만: 프로그램 2개 설치

| 프로그램 | 받는 곳 | 주의 |
|---|---|---|
| Python **3.12** | https://www.python.org/downloads/ (3.12.x, Windows installer 64-bit) | 설치 첫 화면에서 **Add python.exe to PATH** 체크 |
| PostgreSQL **16** | https://www.postgresql.org/download/windows/ | 설치 중 묻는 비밀번호·포트는 아무거나 괜찮습니다. MOIM은 이 폴더 안에 자기 DB를 따로 만듭니다 |

## 1. API 키 넣기 — `setup-api-key.cmd` 더블클릭

1. 팀에서 받은 **Timely API 키**를 복사합니다.
2. `setup-api-key.cmd`를 더블클릭합니다.
3. 창에 마우스 **오른쪽 클릭**(또는 Ctrl+V)으로 붙여넣고 Enter. 입력한 글자는 보안상 화면에 안 보이는 게 정상입니다.
4. "저장했습니다 (끝 4자리 ...xxxx)"가 나오면 끝입니다. 끝 4자리로 맞게 들어갔는지 확인하세요.

키는 이 폴더의 `.env` 파일에 `TIMELY_API_KEY=...` 한 줄로 저장됩니다.

- `.env`는 Git에 올라가지 않습니다(`.gitignore`). **키를 코드, 채팅, 스크린샷, 깃허브에 올리지 마세요.**
- 키를 바꾸려면 `setup-api-key.cmd`를 다시 실행하면 덮어씁니다.
- 직접 고치고 싶으면 `.env`를 메모장으로 열어 `TIMELY_API_KEY=` 뒤에 붙여넣어도 됩니다(따옴표·공백 없이).
- `.env`의 값이 비어 있으면 Windows 사용자 환경변수 `TIMELY_API_KEY`를 대신 씁니다.
- 키가 없어도 서버는 켜지지만 AI 기능(역할 후보, 인터뷰, 투두 초안, 자료 검토)은 실패합니다.

## 2. 실행 — `start-moim.cmd` 더블클릭

처음 실행할 때 자동으로 해 주는 것(몇 분 걸림):

1. Python 가상환경(`.venv`) 생성과 패키지 설치
2. `.env` 생성 (로컬 DB 비밀번호는 무작위로 생성)
3. API 키가 없으면 그 자리에서 입력 받기
4. 이 폴더 안에 로컬 DB(`.local\pgdata`, 포트 54329, 이 PC에서만 접속) 생성·시작, 테이블 준비
5. 서버 시작 후 브라우저 자동 열기 (`http://127.0.0.1:8000/`, 사용 중이면 8001…)

두 번째부터는 설치를 건너뛰고 바로 켜집니다. 코드를 새로 받아 의존성이 바뀌면 알아서 다시 설치합니다.

**끄기:** 검은 창을 닫거나 Ctrl+C. (DB는 백그라운드에 남아 있어도 괜찮고, 다음 실행 때 그대로 씁니다.)

## 문제가 생기면

| 메시지 | 해결 |
|---|---|
| Python 3.12를 찾지 못했습니다 | 위 0번대로 Python 3.12 설치 (3.13 등 다른 버전은 안 됨) |
| PostgreSQL을 찾지 못했습니다 | PostgreSQL 16 설치 |
| initdb 실패 | 폴더 경로에 한글·공백이 있으면 `C:\moim` 같은 영문 경로로 옮겨 다시 실행 |
| 기본 DB 포트 사용 중 | 자동 생성된 로컬 설정(`.env.example` 복사본 포함)이면 빈 포트를 찾아 `.env`의 개발·테스트 DB URL을 함께 변경합니다. 직접 지정한 DB 설정은 보호를 위해 자동 변경하지 않습니다 |
| AI 기능만 실패 (AUTH_FAILED 등) | `setup-api-key.cmd`로 키를 다시 넣고 `start-moim.cmd` 재실행 |
| 창이 바로 꺼짐 | 폴더에서 주소창에 `cmd` 입력 → `start-moim.cmd` 입력해 실행하면 메시지가 남습니다 |

처음부터 다시 하고 싶으면 서버를 끄고 `.venv`, `.local`, `.env`를 지운 뒤 실행하세요. (`.local`을 지우면 로컬 DB의 모든 데이터가 사라집니다.)

## 개발자용

- 실행 스크립트: `tools/launcher/start.ps1`, `tools/launcher/set-api-key.ps1` (공통 `common.ps1`, DB 생성 `ensure_db.py`).
- 앱 자체는 여전히 `.env`를 읽지 않습니다. 실행 스크립트가 `.env`를 환경변수로 불러온 뒤 `uvicorn --factory moim.main:app_factory`를 띄웁니다.
- 테스트, 운영 배포는 `docs/HANDOFF.md`, `docs/OPERATIONS.md`를 보세요.
