# WE·MEET M0/M1 · Python AI 패키지 0.10.1

## 팀 개발환경 빠른 시작

필수 설치: Python 3.12.14, Node.js 24.14.0, npm 11.9.0. PostgreSQL을 로컬에 설치하지 않았다면 Docker Desktop도 필요합니다.

```powershell
git clone https://github.com/daejinf/creeper.git
cd creeper
.\setup.ps1
.\start.ps1
```

`setup.ps1`은 Python 가상환경과 프론트엔드 패키지를 잠금 파일 기준으로 설치합니다. `start.ps1`은 Docker가 있으면 PostgreSQL을 시작하고 FastAPI와 Vite 개발 서버를 함께 실행합니다. 비밀 키는 Git에 올리지 말고 로컬 `.env`에만 입력하세요.

SW팀 기본 연동은 독립 Python 패키지입니다. 코어에 DB·FastAPI·React를 넣지 않고, 웹/CLI는 같은 함수를 사용하는 보조 어댑터로 유지합니다.

1. [설치와 전체 실행 예제](ai-core/README.md)
2. [지원하는 공개 API·입출력·오류·저장 계약](ai-core/PUBLIC_API.md)
3. [SW 연결 순서](ai-core/SW_INTEGRATION.md)
4. [M2 계획 버전·과제 참조](ai-core/SW_M2_CONTRACT.md)
5. [최종 검증 기록](ai-core/VERIFICATION.md)

`examples/sw_m0_m1_end_to_end.py`는 키 없는 fixture로 공동 코칭부터 개인 계획·검증·M2 기준·저장 복원·부분 실패 재실행까지 보여줍니다. 실제 운영에서는 사용자 확인·인증·그룹 승인·DB·알림·활성화를 SW가 연결합니다.

현재 코어 테스트162개, 새 환경 설치·전체 인수 예제,4개 화면 너비의 C/D/E/F 흐름과 서버 재시작·작업16개 복원을 확인했습니다. 실제 최신 코칭1건과 저장 LIVE 계획3건의 코드 재검사를 구분해 기록합니다. 사람 의미 검토와 SW팀 직접 인수는 아직 대기입니다.

로컬 테스트 화면: http://127.0.0.1:8010/ · 일반 LIVE 사용 허용, 키는 서버 환경변수로만 읽습니다.

`backend/`·`frontend/`는 과거 서비스 프로토타입입니다. 새 AI 패키지는 이를 import하지 않습니다. 과거 안내는 [HISTORY](ai-core/HISTORY.md)와 [이전 프로젝트 문서](ai-core/LEGACY_README.md)에 보존했습니다.
