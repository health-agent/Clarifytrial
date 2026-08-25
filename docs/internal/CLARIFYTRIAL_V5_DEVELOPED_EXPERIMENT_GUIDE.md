# ClarifyTrial 실행과 실험 안내

모든 명령은 저장소 최상위 폴더에서 PowerShell로 실행한다. 결과는 별도로 지정한
`runs` 폴더에 저장되며, 큰 외부 원본은 `.research-cache`에 저장된다.

## 1. 설치와 자동 검사

Python 3.11 이상이 필요하다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c constraints\research.txt -e ".[dev,retrieval-bm25,codex-subscription]"
.\.venv\Scripts\python.exe -m nltk.downloader punkt
.\.venv\Scripts\python.exe -m pytest -q
```

의학적 의미 검색까지 다시 실행하려면 큰 기계학습 패키지가 포함된 `retrieval` 선택
항목을 추가한다.

```powershell
.\.venv\Scripts\python.exe -m pip install -c constraints\research.txt -e ".[dev,retrieval,codex-subscription]"
```

## 2. 가장 빠른 전체 화면 실행

준비된 합성 환자에서 관련 시험 검색, 조건 판단, 부족 정보 선택, 합성 답 공개,
재판정과 최종 목록을 한 화면에 보여 준다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-full-ui --auto
```

`--auto`를 빼면 각 합성 답을 공개하기 전에 Enter 입력을 기다린다. 기본값은 수치와
날짜를 코드로 계산하므로 외부 언어모델을 부르지 않는다.

공개 시험 1,931건을 준비한 뒤 모집 중·모집 예정 시험 589건 검색부터 연결하려면 다음
명령을 사용한다.

```powershell
.\.venv\Scripts\clarifytrial.exe prepare-team-trials

.\.venv\Scripts\clarifytrial.exe run-full-ui `
  --broad-corpus .research-cache\team-trials\trials.jsonl `
  --broad-search-top-k 200 `
  --auto
```

## 3. 별도 환자와 시험 파일 실행

기본 예제 파일을 사용한 일반 실행은 다음과 같다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials examples\general_screening\trials.jsonl `
  --answers examples\general_screening\answers.json `
  --provider deterministic `
  --output runs\general-screening
```

입력 파일의 역할은 다음과 같다.

| 파일 | 내용 |
|---|---|
| `patient.json` | 현재 확인된 환자 사실, 아직 필요한 정보와 환자 상황 |
| `trials.jsonl` | 한 줄에 한 시험씩 저장한 구조화 조건 |
| `answers.json` | 실험용으로 미리 정한 숨은 답; 실제 대화에서는 생략 가능 |

`--answers`를 생략하면 터미널에서 답을 직접 입력한다. 실행 폴더에는 최종 결과,
단계별 기록과 이어서 실행할 수 있는 세션 파일이 생긴다.

중단된 실행은 이전 세션 파일을 지정해 이어서 진행한다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials examples\general_screening\trials.jsonl `
  --resume runs\general-screening\session.json `
  --provider deterministic `
  --output runs\general-screening-resumed
```

입력과 설정이 달라지면 이전 세션을 재사용하지 않는다.

## 4. 공개 시험 기반 평가자료 다시 만들기

```powershell
.\.venv\Scripts\clarifytrial.exe prepare-team-trials

.\.venv\Scripts\clarifytrial.exe select-team-evaluation-trials `
  --trials .research-cache\team-trials\trials.jsonl `
  --output runs\team-trial-expansion\selection.json

.\.venv\Scripts\clarifytrial.exe build-public-protocol-benchmark `
  --output runs\public-protocol-benchmark-rebuild

.\.venv\Scripts\clarifytrial.exe audit-public-protocol-benchmark
```

마지막 명령은 같은 원본과 설정으로 다시 만든 시험 조건과 합성 환자가 저장소의
평가자료와 같은지 확인한다. 현재 자료는 10개 질환, 공개 시험 50건, 구조화 조건
202개와 합성 환자 50명으로 구성된다.

## 5. 전체 흐름 평가

다음 명령은 개발에 사용하지 않은 합성 환자 30명에게 네 가지 정보 선택 방법을 같은
조건으로 실행한다. 외부 언어모델은 사용하지 않는다.

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

프로그램 안에서 비교하는 네 방법은 다음과 같다. 명령줄의 `--arm`은 이 가운데 실행할
방법 하나를 고르는 선택 항목이다.

| 명령줄 이름 | 실제 뜻 |
|---|---|
| `no_questions` | 추가 정보를 확인하지 않음 |
| `fixed_order` | 입력 파일에 적힌 순서대로 확인 |
| `immediate_coverage` | 현재 가장 많은 미완료 시험에 연결된 정보부터 확인 |
| `clarifytrial` | 남은 확인 횟수 안의 정보 조합을 계산 |

`--include-unavailable-scenario`는 환자마다 답 하나를 받을 수 없게 한 실행을 추가한다.
`--include-patient-choice-scenario`는 새 검사와 추가 방문을 거절한 실행을 추가한다.
`--approve-synthetic-actions`는 평가자료에 미리 선언된 합성 선택만 자동으로 적용한다.

## 6. 확인 횟수 0회부터 5회까지 비교

앞 명령의 `--action-budget 3`을 `--budget-sweep`으로 바꾼다.

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

각 확인 횟수의 결과 폴더와 `frontier` 폴더가 생긴다. `frontier`에는 실제 후보 확정,
제외 후보 정리와 최종 상태 일치가 JSON, CSV, Markdown과 그림으로 저장된다.

이미 끝난 환자와 비교 방법을 건너뛰려면 같은 명령에 `--resume`을 붙인다.

## 7. 연구 보고서 만들기

```powershell
.\.venv\Scripts\clarifytrial.exe build-report `
  --workflow runs\public-protocol-budget-sweep\budget-3\summary.json `
  --budget-frontier runs\public-protocol-budget-sweep\frontier `
  --output runs\public-protocol-report
```

보고서에는 다음 내용이 들어간다.

- 질문 전후 최종 상태 변화
- 처음에는 보이지 않을 실제 참가 가능 후보 수
- 추가 확인 후보로 남긴 수와 질문 뒤 확정한 수
- 처음에는 남았지만 결국 제외된 후보 수
- 답을 얻지 못한 횟수와 같은 정보 반복
- 새 검사, 추가 방문과 환자 선택
- 외부 언어모델 호출, 토큰과 실행 오류

## 8. 새 시험 최종 평가

기존 평가와 겹치지 않는 최종 시험 15건과 합성 환자 25명을 구조화 규칙만으로
실행한다.

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

언어모델을 쓰는 비교에는 제공자, 모델과 생각량을 지정하고 비용이 발생하는 실행을
확인하는 옵션을 붙인다.

```text
--provider codex-subscription --model gpt-5.6-sol --effort medium --confirm-model-run
```

조건 판단 모델만 사용할 때는 `--agent-architecture single_judge`, 조건 판단과 질문
문장 역할을 코드가 나누어 부를 때는 `--agent-architecture code_routed_agents`를 쓴다.
각 실행은 서로 다른 출력 폴더에 저장한다.

세 결과를 한 표로 묶는 명령은 다음과 같다.

```powershell
.\.venv\Scripts\clarifytrial.exe compare-agent-architectures `
  --workflow runs\independent-new-trial-final-rules-v2\summary.json `
  --workflow runs\independent-new-trial-sol-single-judge-v2\summary.json `
  --workflow runs\independent-new-trial-sol-code-routed-v2\summary.json `
  --output docs\internal\results\independent-new-trial-agent-evaluation-v1
```

## 9. TREC 검색 다시 실행

TREC와 TrialGPT 공개 검색 파일을 먼저 준비한 뒤 각 연도에 대해 실행한다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-trialgpt-retrieval `
  --dataset .research-cache\TrialGPT\dataset\trec_2021 `
  --cache .research-cache\TrialGPT\retrieval-cache `
  --corpus trec_2021 `
  --output runs\trialgpt-retrieval-2021
```

2022년은 경로와 `--corpus`를 `trec_2022`로 바꾼다. 의학적 의미 검색은 큰 모델 파일과
연산 장치가 필요할 수 있다. 같은 단어 검색만 연결을 확인하려면 `--bm25-only`를 쓴다.
두 실행은 같은 성능 결과가 아니므로 구분해 기록한다.

## 10. 실행 전 상태 확인

전체 흐름 자료, 합성 환자와 저장된 결과가 서로 맞는지 확인한다.

```powershell
.\.venv\Scripts\clarifytrial.exe audit-final-evaluation-readiness `
  --trial-set data\public_protocol_benchmark_v1\trial_set.json `
  --patient-pairs data\public_protocol_benchmark_v1\patient_pairs.json `
  --workflow runs\public-protocol-budget-sweep\budget-3\summary.json `
  --output runs\public-protocol-readiness
```

이 확인은 입력 파일, 식별자와 결과 연결 상태를 검사한다. 임상 정확도를 새로 측정하는
명령은 아니다.

## 11. 결과를 읽는 기준

- `deterministic` 제공자는 합성 답과 구조화 규칙을 사용하는 코드 실행이다. 토큰이
  0인 것이 정상이다.
- 95.3%는 추가 정보를 세 번 확인했을 때 프로그램이 합성 환자의 기대 상태에 도달한
  비율이다.
- 589건 검색의 150/150은 미리 정한 평가 시험 연결 점검이다.
- 새 시험 75/75는 객관적으로 구조화한 일부 조건의 결과다.
- 실제 후보 확정과 제외 후보 정리를 항상 함께 본다.
- 외부 언어모델을 실행했다면 호출 수, 토큰, 실패와 다시 시도한 횟수를 함께 기록한다.
