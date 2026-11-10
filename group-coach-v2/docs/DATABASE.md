# 새 DB 설치·운영

새 DB는 `wemeet_coach_v2`, 스키마는 `wemeet_coach`입니다. 기존 `wemeet_goals`는 건드리지 않습니다. 같은 PostgreSQL 클러스터를 쓸 수 있지만 장애 시험을 위해 전체 클러스터를 중지하지 마세요.

## 초기 설치 — 관리자 1회 작업

`python -m tools.provision --admin-env <관리자가 등록한 환경변수 이름>`은 지정한 관리 접속으로 새 DB/역할을 준비하고, 새 COACH 환경변수를 등록하고, 새 DB에만 마이그레이션합니다. Windows 로컬 설치용입니다. 기존 주소를 런타임 대체값으로 쓰지 않습니다. 관리 환경변수 이름을 명시해야 하며 비밀번호를 명령줄 인자로 넘기지 않습니다.

역할은 NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOINHERIT입니다. 필요한 테이블 SELECT/INSERT와 현재 포인터·실행 상태·투표의 제한 UPDATE만 허용합니다. 계획 스냅샷 갱신·삭제와 DDL은 거부합니다. 회원 인증을 대신하는 DB RLS 정책은 아니므로 운영 SW의 권한 검사는 별도로 필요합니다.

이후 스키마 변경은 관리자 프로세스에서 `python -m alembic upgrade head`로 수행합니다. `WEMEET_COACH_ADMIN_DATABASE_URL`이 새 DB를 가리켜야 합니다. 일반 `start.ps1`은 관리자 설정을 제거하고 앱 권한으로 실행합니다.

## 관계

`rooms → members/events/proposals → votes → goals → plans → tasks → baselines`

개인 영역은 `private_states`, `personal_goals.private_conditions`, owner가 지정된 이벤트·작업·계획입니다. `operations`는 실행 상태·입력 스냅샷·실제 사용량을 담습니다. `actions`/고유 제약은 중복 코드 동작을 막습니다. 데이터는 회원정보를 복제한 서비스 DB가 아니라 코칭과 목표 기록입니다.

## 백업과 복원

PostgreSQL의 `pg_dump -Fc -d wemeet_coach_v2 -f <보호된경로/backup.dump>`를 관리 계정으로 실행하세요. 호스트·포트·사용자는 환경/pgpass로 지정하고 비밀번호를 출력하지 마세요. 백업에는 개인 대화가 포함됩니다. Git·SW 전달 wheel·공개 다운로드에 넣지 않습니다.

복구 파일 암호화 키도 별도 비밀 저장소에 보관합니다. DB 백업만으로 SAVE_PENDING의 아직 커밋되지 않은 응답이 복원되지는 않습니다. `runtime/recovery/`와 당시 복구 키를 안전하게 함께 보존하세요. 암호화 키를 잃으면 복구 파일을 읽을 수 없습니다.

복원은 새 격리 DB에서 `pg_restore --no-owner`로 먼저 검증하고 목표→계획→과제→기준 ID·개인 범위를 확인한 뒤 운영 전환하세요. 이 어댑터는 안전을 위해 DB 이름 `wemeet_coach_v2`를 검사하므로, 임시 복원 DB의 구조 검사는 관리자 SQL로 수행합니다. 기존 DB 위에 덮어쓰지 마세요.

## 장애

- 연결 실패: AI 호출 전에 입력 저장을 실패시킵니다. SQLite 대체 저장 없음.
- 모델 실패: 메시지와 이전 성공 결과 보존. 새 오류 이벤트/실행 기록 저장.
- 응답 수신 후 커밋 실패: Fernet 암호화 패킷 보존, 동일 action_id 재시도는 저장만 수행.
- 결과 수신 여부 불명: UNKNOWN으로 남기고 자동 유료 재호출 금지.
- 동시 입력 변경: 응답을 STALE로 보존, 현재 승인 포인터를 자동 변경하지 않음.

운영 전에는 백업 스케줄·보관기간·키 회전·인증·접근 감사 정책을 SW/운영 담당자가 확정해야 합니다. 로컬 검증을 운영 서비스 완성으로 간주하지 않습니다.
