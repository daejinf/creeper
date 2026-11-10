# 대화 기록을 이어 쓰기

현재 저장·복구 계약은 [PostgreSQL 통합 안내](POSTGRES_INTEGRATION.md)로 통합했습니다.

`prepare_module_input`은 계속 순수 함수로 사용할 수 있습니다. PostgreSQL에서 읽은 공개 또는 본인 범위 RecordView를 전달하면 다음 입력을 준비합니다. 이 작업은 AI를 호출하거나 데이터를 저장하지 않습니다. 직접 입력한 요청으로 A~F 중 하나만 실행하는 방법도 유지합니다.
