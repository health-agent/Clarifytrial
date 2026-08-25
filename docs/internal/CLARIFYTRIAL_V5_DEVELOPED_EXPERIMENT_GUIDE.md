# ClarifyTrial 실행과 실험 안내

## 1. 한 사례에서 하는 일

임상시험이 최근 혈액검사, 현재 복용약과 과거 수술 여부를 요구하지만 환자 자료에는
일부만 있다고 가정한다.

```text
처음 환자 자료로 조건 판단
→ 현재 확인된 시험과 추가 확인 후보 분리
→ 여러 시험에 영향을 주는 정보부터 확인
→ 답을 반영해 관련 시험만 다시 판단
→ 질문 전후에 바뀐 시험과 남은 정보 출력
```

시험마다 다음 상태 중 하나가 나온다.

| 상태 | 뜻 |
|---|---|
| 현재 확인 완료 | 지금 자료로 구조화한 참가 조건을 확인함 |
| 추가 확인 후보 | 참가 가능성은 남아 있지만 필요한 정보가 있음 |
| 참가 조건 불충족 | 구조화한 조건을 위반함 |

## 2. 입력 자료

기본 입력은 정해진 JSON이다.

- 환자 사실, 값, 단위, 자료 종류와 날짜
- 시험별 선정·제외 조건과 원문 위치
- 조건 사이의 `모두`, `하나 이상`, `일정 개수 이상` 관계
- 아직 필요한 정보와 확인 가능한 방법
- 환자가 새 검사·방문을 허용하는지와 이동·비용·시간 제한

자유 형식 문장을 JSON으로 옮기는 기능은 선택 단계다. 질문 순서 실험은 이미 구조화된
JSON에서 시작한다.

## 3. 준비된 예제 실행

공개 시험 50건에서 같은 질환의 시험 5건을 찾고, 질문 뒤 재판정까지 한 화면에 보려면
다음 명령을 사용한다. 외부 모델을 부르지 않는 기본 실행이다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-full-ui --auto
```

공개 시험 모음을 준비한 뒤에는 모집 중·모집 예정 시험 589건 검색부터 같은 화면에
연결할 수 있다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-full-ui `
  --broad-corpus .research-cache\team-trials\trials.jsonl `
  --broad-search-top-k 200 `
  --auto
```

별도 환자·시험 파일을 넣는 일반 실행은 다음과 같다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials examples\general_screening\trials.jsonl `
  --answers examples\general_screening\answers.json `
  --provider deterministic `
  --output runs\general-screening
```

터미널 화면에서는 후보 검색, 조건 판단, 다음 정보 선택, 답 공개, 재판정과 최종 목록이
순서대로 보인다.

## 4. 공개 시험 평가자료 다시 만들기

```powershell
.\.venv\Scripts\clarifytrial.exe prepare-team-trials

.\.venv\Scripts\clarifytrial.exe select-team-evaluation-trials `
  --trials .research-cache\team-trials\trials.jsonl `
  --output runs\team-trial-expansion\selection.json

.\.venv\Scripts\clarifytrial.exe build-public-protocol-benchmark `
  --output runs\public-protocol-benchmark-rebuild

.\.venv\Scripts\clarifytrial.exe audit-public-protocol-benchmark
```

평가자료에는 10개 질환의 공개 시험 50건, 구조화 조건 202개와 합성 환자 50명이 있다.
환자마다 정보 1개·2개·3개 또는 5개를 가렸다.

## 5. 검색부터 질문 뒤 재판정까지 평가

```powershell
.\.venv\Scripts\clarifytrial.exe run-workflow-evaluation `
  --trial-set data\public_protocol_benchmark_v1\trial_set.json `
  --patient-pairs data\public_protocol_benchmark_v1\patient_pairs.json `
  --generation-config configs\natural_evaluation_patient_generation_v2.json `
  --broad-corpus .research-cache\team-trials\trials.jsonl `
  --broad-search-top-k 200 `
  --provider deterministic `
  --split heldout `
  --action-budget 3 `
  --concurrency 4 `
  --include-unavailable-scenario `
  --include-patient-choice-scenario `
  --approve-synthetic-actions `
  --output runs\public-protocol-evaluation
```

네 가지 정보 선택 방법을 같은 30명에게 실행한다.

| 방법 | 뜻 |
|---|---|
| 추가 확인 없음 | 처음 자료만 사용 |
| 입력 파일 순서 | 부족 정보가 적힌 순서대로 확인 |
| 현재 영향 우선 | 지금 가장 많은 미완료 시험에 연결된 정보부터 확인 |
| ClarifyTrial | 남은 횟수 안에서 판단을 끝낼 정보 조합을 계산 |

`--include-unavailable-scenario`는 환자마다 답 하나를 얻을 수 없게 한다.
`--include-patient-choice-scenario`는 새 검사와 추가 방문을 원하지 않는 경우를 따로
실행한다.

## 6. 확인 기회 0회부터 5회까지 비교

앞 명령의 `--action-budget 3` 대신 `--budget-sweep`을 사용한다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-workflow-evaluation `
  --trial-set data\public_protocol_benchmark_v1\trial_set.json `
  --patient-pairs data\public_protocol_benchmark_v1\patient_pairs.json `
  --generation-config configs\natural_evaluation_patient_generation_v2.json `
  --broad-corpus .research-cache\team-trials\trials.jsonl `
  --broad-search-top-k 200 `
  --provider deterministic `
  --split heldout `
  --budget-sweep `
  --concurrency 4 `
  --include-unavailable-scenario `
  --include-patient-choice-scenario `
  --approve-synthetic-actions `
  --output runs\public-protocol-budget-sweep
```

`frontier` 폴더에 JSON, CSV, Markdown과 SVG 두 개가 생긴다. 각 비율에는 95% 신뢰구간이
함께 저장된다.

## 7. 보고서 생성

```powershell
.\.venv\Scripts\clarifytrial.exe build-report `
  --workflow runs\public-protocol-budget-sweep\budget-3\summary.json `
  --budget-frontier runs\public-protocol-budget-sweep\frontier `
  --output runs\public-protocol-report
```

보고서에는 다음 값이 들어간다.

- 589건 검색에서 평가 대상 시험을 찾은 수와 순위
- 질문 전후 최종 상태 일치
- 처음에는 보이지 않을 실제 참가 가능 후보 수
- 추가 확인 후보로 보존한 수와 질문 뒤 확정한 수
- 결국 제외될 후보를 정리한 수
- 새 검사, 추가 방문과 환자 선택
- 답을 얻지 못한 횟수와 같은 정보 반복
- 모델 호출, 토큰과 실행 오류

## 8. 준비 상태 확인

```powershell
.\.venv\Scripts\clarifytrial.exe audit-final-evaluation-readiness `
  --trial-set data\public_protocol_benchmark_v1\trial_set.json `
  --patient-pairs data\public_protocol_benchmark_v1\patient_pairs.json `
  --workflow runs\public-protocol-budget-sweep\budget-3\summary.json `
  --output runs\public-protocol-readiness
```

이 명령은 기존 공개 시험 기반 통합 점검의 자료 범위와 실행 연결 상태를 확인한다. 새
시험 최종 평가는 아래처럼 별도 자료와 결과로 관리한다.

## 9. 새 시험 최종 평가 결과 확인

구조화 규칙만 사용하는 실행은 다음과 같다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-workflow-evaluation `
  --trial-set data\independent_new_trial_benchmark_v1\final\trial_set.json `
  --patient-pairs data\independent_new_trial_benchmark_v1\final\patient_pairs.json `
  --generation-config configs\independent_new_trial_benchmark_v1.json `
  --provider deterministic `
  --split heldout `
  --arm clarifytrial `
  --agent-architecture rules_only `
  --action-budget 3 `
  --concurrency 4 `
  --output runs\independent-new-trial-final-rules
```

조건 판단 모델을 사용하는 실행은 아래 옵션을 추가하고 `--agent-architecture`를
`single_judge` 또는 `code_routed_agents`로 바꾼다. 각 실행은 서로 다른 출력 폴더에
저장한다.

```text
--provider codex-subscription --model gpt-5.6-sol --effort medium --confirm-model-run
```

세 실행이 끝나면 다음 명령으로 한 표에 묶는다.

```powershell
.\.venv\Scripts\clarifytrial.exe compare-agent-architectures `
  --workflow runs\independent-new-trial-final-rules-v2\summary.json `
  --workflow runs\independent-new-trial-sol-single-judge-v2\summary.json `
  --workflow runs\independent-new-trial-sol-code-routed-v2\summary.json `
  --output docs\internal\results\independent-new-trial-agent-evaluation-v1
```

같은 최종 평가자료에서 구조화 규칙만 사용한 실행, 조건 판단 모델만 부른 실행, 조건
판단과 질문 문장 역할을 부른 실행을 비교한다. 최종 보고서는 `report.md`, 숫자는
`summary.json`에 저장된다.

## 10. 중단 뒤 이어서 실행

같은 입력과 설정으로 다시 실행할 때 명령 끝에 `--resume`을 붙인다. 끝난 환자와 비교
방법은 다시 실행하지 않는다. 입력 파일이나 설정이 달라지면 이전 결과를 재사용하지
않는다.

## 11. 결과 해석

- 입력 파일 순서보다 높다고 새 질문 알고리즘이 우수하다고 말하지 않는다.
- 지금 가장 많은 시험에 연결된 정보를 고르는 강한 단순 방법과도 비교한다.
- 실제 참가 가능 후보 확정과 결국 제외될 후보 정리를 함께 본다.
- 환자 선택을 지켜 확정 수가 낮아지면 피한 검사·방문과 함께 보고한다.
- 외부 모델을 쓰지 않은 결과는 프로그램 규칙과 자료 연결 검증으로 설명한다.
- 공개 시험 50건의 전체 참가 조건을 구조화했다고 말하지 않는다.

현재 공개 시험 조건 기반 결과에서 ClarifyTrial과 강한 단순 방법은 30명 모두 같았다.
핵심 결과는 새 질문 계산의 우월성이 아니라, 보이지 않을 실제 후보 54개를 보존하고
세 번 안에 47개를 확정하면서 결국 제외될 후보 68개를 모두 정리한 것이다.

새 시험 최종 평가에서는 세 모델 호출 구조가 모두 75/75개를 맞혔다. 구조화 조건에서
모델 호출을 늘려도 결과가 좋아지지 않았으므로 코드를 기본값으로 둔다.
