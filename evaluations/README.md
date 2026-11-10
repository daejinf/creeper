# 평가 도구 — 0.12.0

200개 시나리오는 합성 자료이며 사용자 데이터 학습본이 아닙니다. 정답·기대 행동을 모델 입력에 자동 주입하지 않습니다.

```powershell
python evaluations/check_data.py
python evaluations/evaluate.py --round 1
```

두 명령 모두 신규 HTTP0입니다. 두 번째는 3종 고정 사례(A / B·C / E 코칭·계획, 회차당 모듈5개) manifest만 만듭니다. 이전 0.11.0의116회는 별도 기록입니다.

```powershell
python evaluations/evaluate.py --round 1 --live
python evaluations/evaluate.py --round 2 --live
python evaluations/evaluate.py --round 3 --live
```

**명시적인 --live에서만 유료 호출**합니다. 회차1·2·3만 지원하며 완료된 회차는 다시 호출하지 않습니다. 각 회차 입력·소스 해시·실제 결과를 보존합니다. 새 폴더로 예산을 우회하지 마세요. 동시 실행은1개이며 전송·구조 보정 포함 공통HTTP30회가 한도입니다. 회차2·3은 SQLite에 보관한 대화에서 입력을 준비합니다. 평가의 확인값은 작성된 모의 확인입니다.

저장 결과를 현재 코드로 다시 검사하려면:

```powershell
python evaluations/evaluate.py --replay-path runtime/evaluation-012/round-2
```

이 작업은 REPLAY이며 신규HTTP0입니다. 원문이 없는 실패는 재검사 불가로 남깁니다. 현재 검사기에서 통과해도 과거 모델의 실패 원본을 성공으로 덮어쓰지 않습니다.

LIVE/REPLAY 원문과 SQLite는 runtime에 두며 Git에서 제외합니다. 개인 입력·추적은 공개 전달물에 포함하지 않습니다. API 완료·계약 검사·에이전트 내용 검토·사람 검토는 별도로 보고합니다. 소수 사례 통과를200개 LIVE 품질 합격으로 표시하지 않습니다. 최신 결과는 docs/VERIFICATION.md를 보세요.

## 실제 운영4회

`python evaluations/operations.py --session 1 --recheck --live` 등1~4를 사용합니다. 명시적인 LIVE이며 실제 비용이 발생합니다. 완료된 회차는 재호출하지 않고, 초기 실패 원본과 같은 회차의 재검사를 구분합니다. 사용자 추가 허용 후 별도 live-budget.json에 초기9회부터 포함해40회 상한을 둡니다. 최종 사용은26회입니다. 이전3회 평가 예산을 새로 초기화하지 않습니다. 실제 요청·결과는 저장소 어댑터 저장 전에 파일로 보존합니다.
