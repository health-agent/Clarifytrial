# ClarifyTrial 실행과 실험 안내

저장소를 처음 받은 팀원은 아래 순서로 프로그램을 실행하고, 현재 발표에 쓰는 결과표와
그림까지 다시 만들 수 있다. 모든 명령은 저장소 최상위 폴더에서 PowerShell로
실행한다.

실행 결과는 `runs`에 저장한다. 내려받은 공개 원본과 검색용 임시 파일은
`.research-cache`에 둔다. 두 폴더의 파일을 지우지 않으면 같은 자료를 다시 내려받거나
처음부터 계산하지 않아도 된다.

## 1. 설치

Python 3.11 이상이 필요하다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c constraints\research.txt -e ".[dev,retrieval-bm25,codex-subscription]"
.\.venv\Scripts\python.exe -m nltk.downloader punkt
```

아래의 합성자료 실행과 주 평가는 API 키 없이 돌아간다. `deterministic` 제공자는
구조화한 수치·날짜와 미리 저장한 합성 답을 코드로 처리한다. 외부 모델을 사용하는
실행에는 별도의 확인 옵션과 제공자 설정이 필요하다.

설치가 제대로 됐는지 확인한다.

```powershell
.\.venv\Scripts\clarifytrial.exe --help
```

## 2. 한 번의 전체 실행

가장 빠른 확인 방법은 준비된 합성 환자 한 명을 처음부터 끝까지 실행하는 것이다.
환자 정보, 후보 시험, 조건 판정, 다음 질문, 답변 반영과 최종 결과가 한 화면에
차례대로 나온다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-full-ui `
  --auto `
  --output runs\full-ui
```

`--auto`는 평가자료에 따로 저장한 합성 답을 자동으로 공개한다. 외부 모델은 부르지
않는다. 실행이 끝나면 `runs\full-ui`에서 최종 결과와 각 단계의 기록을 확인할 수 있다.

| 파일 | 내용 |
|---|---|
| `result.json` | 시험별 최종 상태, 근거와 남은 정보 |
| `trace.jsonl` | 검색부터 재판정까지 시간순 기록 |

## 3. 터미널에서 직접 답하기

아래 명령은 예제 환자와 시험을 읽고 필요한 정보를 터미널에서 묻는다. 실험용 답
파일을 지정하지 않았으므로 사용자가 직접 답한다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials examples\general_screening\trials.jsonl `
  --provider deterministic `
  --output runs\manual-screening
```

간단한 사실은 평소 문장으로 입력해도 된다. 공식 검사 결과처럼 값, 날짜와 출처를
함께 남겨야 할 때는 한 줄짜리 JSON을 입력한다.

```json
{"statement":"공식 HbA1c 결과는 6.4%였다.","concept":"hba1c","value":6.4,"unit":"%","event_date":"2026-08-20","recorded_date":"2026-08-20","source_type":"official_verification","source_location":"official-result#hba1c","verification_status":"verified"}
```

- 답을 모르면 `unknown`을 입력한다. 프로그램은 다음 정보나 다른 확인 방법을 찾는다.
- `quit`을 입력하면 현재 상태를 `session.json`에 저장하고 끝낸다.
- 같은 환자와 시험으로 이어서 실행할 때는 `--resume`에 저장한 세션을 지정한다.

`run-screening` 결과 폴더에는 `result.json`, `trace.jsonl`, `session.json`이 함께 생긴다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials examples\general_screening\trials.jsonl `
  --resume runs\manual-screening\session.json `
  --provider deterministic `
  --output runs\manual-screening-resumed
```

발표용 터미널 장면은 같은 실행에 준비된 합성 답을 넣어 재현한다. 최근 HbA1c 값
하나가 서로 다른 기준을 가진 두 시험에 함께 반영되는 예제다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials examples\general_screening\trials.jsonl `
  --answers examples\general_screening\presentation-answers.json `
  --provider deterministic `
  --output runs\presentation-demo-agent-loop-patient-aware-20260830

.\.venv\Scripts\python.exe scripts\render_presentation_terminal_demo.py `
  --input runs\presentation-demo-agent-loop-patient-aware-20260830\result.json `
  --output docs\internal\diagrams\clarifytrial-terminal-demo.svg

.\.venv\Scripts\python.exe scripts\render_presentation_web_demo.py `
  --input runs\presentation-demo-agent-loop-patient-aware-20260830\result.json `
  --output docs\internal\demo\clarifytrial-presentation-demo.html
```

HTML 데모는 16:9 한 화면에서 `현재 상태 읽기 → 다음 행동 결정 → 확인 도구 실행 →
상태 갱신 → 반복 또는 종료 판단`을 단계별로 보여 준다. 같은 정보를 얻는 기존 공식
결과와 새 검사를 실제 규칙이 비교하고, 새 검사·추가 방문을 원하지 않는 합성 환자의
제한에 맞는 경로를 고른 실행 기록을 사용한다.
`자동 재생`, `다음`, `처음`, `전체 화면`
버튼과 오른쪽 화살표·스페이스바·Home 키를 사용할 수 있다. 네트워크와 외부 모델을
부르지 않는 한 파일짜리 화면이므로 브라우저에서 열어 녹화하거나 마지막 장면을 캡처해
발표자료에 넣을 수 있다. 마지막 장면은
`docs/internal/demo/clarifytrial-presentation-demo-final.png`에도 저장돼 있다.

## 4. 공개 조건에서 질문 전후 변화 확인

공개 임상시험 50건의 조건 202개와 합성 환자 50명을 사용한다. 자료 제작과 조정에 쓴
환자 20명은 최종 통계에서 제외하고, 나머지 30명에게 같은 질환의 시험을 다섯 건씩
연결한다.

### 기본 문진 뒤 남은 시험

나이, 임신·수유 여부와 활동성 중증 감염을 처음부터 제공한 상태에서 질문을 한 번까지
허용한다.

```powershell
.\.venv\Scripts\python.exe scripts\run_public_protocol_common_facts_known.py `
  --trial-set data\public_protocol_benchmark_v1\trial_set.json `
  --patient-pairs data\public_protocol_benchmark_v1\patient_pairs.json `
  --output runs\public-protocol-common-facts-known-v1
```

환자-시험 조합 150개 가운데 28개는 처음 자료만으로 상태가 정리돼 있다. 세 기본 정보를 넣으면
제외되는 시험 68건과 참가 가능 후보 32건이 추가로 정리된다. 질문 전에 상태가 정해진
조합은 128개이고, 남은 미확정 조합은 22개다. 실제 질문은 14번 나왔고, 답을 받은 뒤
14건이 확인 완료로 바뀌었다. 8건은 확인 대기로 남았다. `direct-transition-summary.csv`에는 이
변화가, `question-category-counts.csv`에는 진단·병리, 과거 수술·치료, 검사 수치처럼
실제로 물은 정보의 종류가 저장된다.

### 질문 순서 민감도

아래 실행은 시작 정보와 질문 순서를 바꾸어 같은 환자 결과가 얼마나 달라지는지
확인한다.

```powershell
.\.venv\Scripts\python.exe scripts\run_public_protocol_policy_scale.py `
  --trial-set data\public_protocol_benchmark_v1\trial_set.json `
  --patient-pairs data\public_protocol_benchmark_v1\patient_pairs.json `
  --output runs\public-protocol-policy-scale-20260830
```

환자마다 가린 정보의 가능한 순서를 전부 계산한다. 정보가 3개면 6가지, 5개면 120가지
순서를 실행한다. 세 기본 정보를 먼저 제공했을 때 가능한 순서의 평균은 94.22%, 현재
규칙은 94.67%의 시험 상태가 합성 정답과 같았다. 차이는 0.44%포인트였다. 외부 모델
호출과 토큰 사용은 0이다.

주요 결과 파일은 다음과 같다.

| 파일 | 내용 |
|---|---|
| `policy-metrics.csv` | 확인한 정보 수 0~5개에서 각 방법의 시험 상태 일치율 |
| `paired-comparisons.csv` | 같은 환자에게 두 방법을 적용한 차이와 95% 범위 |
| `budget-auc.csv` | 확인 수 0~5의 전체 곡선을 한 값으로 묶은 결과 |
| `paired-budget-auc-comparisons.csv` | 환자별 전체 곡선 차이 |
| `disease-level-sensitivity-summary.csv` | 선택한 10개 질환에서 결과 방향이 같았는지 확인 |
| `known-age-policy-metrics.csv` | 나이를 시작부터 알고 있을 때의 민감도 점검 |
| `interpretation.md` | 표를 평이한 문장으로 정리한 결과 |

여러 시험이 같은 환자 정보를 얼마나 함께 쓰는지도 같은 시험 자료에서 계산한다.

```powershell
.\.venv\Scripts\python.exe scripts\build_public_shared_fact_report.py `
  --trial-set data\public_protocol_benchmark_v1\trial_set.json `
  --output-dir runs\public-protocol-shared-facts-v1
```

## 5. 환자 상황에 따른 확인 방법 평가

이 평가는 공개 조건 원본을 한 번 내려받아 사용한다.

```powershell
.\.venv\Scripts\clarifytrial.exe prepare-clinicaltrials-v5 `
  --cache .research-cache\clinicaltrials-v5
```

### 같은 답을 얻는 경로 선택

기존 공식 결과를 받아 오는 경로와 빠른 새 검사를 비교한다. 두 경로가 내놓는 합성
답은 같고, 대기시간·새 검사·추가 방문 조건만 다르다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-public-route-choice-benchmark `
  --source-cache .research-cache\clinicaltrials-v5 `
  --output runs\policy-scale-20260830\route-choice-controlled
```

결과는 `summary.json`에 저장된다. `profile_metrics`에는 환자 상황별 경로 선택 횟수가,
`same_final_judgment_masked_case_count`에는 경로만 바꿨을 때 최종 판단이 유지됐는지가
기록된다.

부담과 이동·비용을 줄이는 설정은 기존 공식 결과를 각각 85회 모두 골랐다. 시간이
급한 설정은 빠른 새 검사를 85회 모두 골랐다. 어떤 정보를 확인했는지와 최종 시험
상태는 40개 비교에서 같았다.

### 환자가 허용하지 않은 검사와 방문 제외

다음 실행은 모든 확인 방법을 남긴 경우와 환자가 허용하지 않은 새 검사·추가 방문을 먼저
제거한 경우를 같은 합성 상황에서 비교한다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-public-burden-benchmark `
  --source-cache .research-cache\clinicaltrials-v5 `
  --action-budget 3 `
  --output runs\policy-scale-20260830\burden-ablation-final
```

`summary.json`의 `mechanism_ablation`에서 새 검사, 추가 방문, 남은 확인 대기 시험,
사용할 수 있는 정보 안에서의 상태 일치와 합성 대기시간을 함께 확인한다. 새 검사와
추가 방문이 한 행동에 겹칠 수 있으므로 두 횟수를 더해 하나의 행동 수로 쓰지 않는다.

현재 결과에서는 새 검사 제안이 21회에서 0회, 추가 방문 제안이 56회에서 0회로 줄었다.
설정마다 남은 확인 대기 시험은 평균 0.88건에서 1.24건으로 늘었고, 시험 다섯 건을 모두
정리한 설정은 80개 중 42개에서 32개로 줄었다.

## 6. 발표 근거 묶음과 그림 만들기

발표 근거 묶음에는 주 평가, 확인 방법 평가와 여러 합성 연결 모양에서 규칙이 움직이는
방식이 들어간다. 아래 명령은 확인 횟수 1회, 2회, 3회를 세 작업으로 나눠 동시에
실행한다. PowerShell 7 이상이 필요하다.

```powershell
$repoRoot = (Get-Location).Path

1..3 | ForEach-Object -Parallel {
  Set-Location $using:repoRoot
  $checkCount = $_

  .\.venv\Scripts\clarifytrial.exe run-public-interactive-benchmark `
    --source-cache .research-cache\clinicaltrials-v5 `
    --action-budget $checkCount `
    --output "runs\policy-scale-20260830\budget-$checkCount\public-patients"

  .\.venv\Scripts\clarifytrial.exe run-public-grid-stress `
    --source-cache .research-cache\clinicaltrials-v5 `
    --action-budget $checkCount `
    --output "runs\policy-scale-20260830\budget-$checkCount\public-grid"

  .\.venv\Scripts\clarifytrial.exe run-interactive-stress `
    --structures-per-topology 200 `
    --seed 20260830 `
    --policy-seed 20260830 `
    --action-budget $checkCount `
    --output "runs\policy-scale-20260830\budget-$checkCount\structural-1800"
} -ThrottleLimit 3
```

구조 실험은 연결 모양 9종을 각각 200개씩 만든다. 확인 횟수마다 1,800개 구조의 가능한
정보 상태 57,600개를 계산하고, 12개 방법을 적용해 691,200회 비교한다. 세 확인 횟수를
합친 선택 방법 계산은 2,073,600회다.

현재 기본 규칙은 미확정 시험 수, 연결된 조건 수와 확인 부담을 차례로 본다. 가장
넓게 연결된 정보만 고르는 방법과 비교한 결과는 다음과 같다.

| 확인 횟수 | 공개 합성 환자에서 정리한 시험 차이 | 공개 합성 환자에서 확인 부담 차이 | 1,800개 연결 구조에서 정리한 시험 차이 | 연결 구조에서 확인 부담 차이 |
|---:|---:|---:|---:|---:|
| 1회 | -1.25%p | -8.4% | -0.19%p | -16.5% |
| 2회 | -0.62%p | -5.4% | +0.01%p | -23.6% |
| 3회 | +0.22%p | -3.7% | -0.06%p | -16.0% |

앞으로 확인할 정보 조합을 계산한 방법은 두 답 분포의 연결 구조에서 현재
규칙보다 전체 평균 1.2~2.0%포인트, 사슬 구조에서는 3.2~4.3%포인트 더 많은 시험 상태를
정리했다. 공개 조건의 가능한 값 조합에서는 기본 규칙보다 낮았다.
학습한 결정 순서는 익숙한 정보 분포에서 좋아졌으나 분포를 바꾼 1회 확인 실험에서는
순위가 뒤집혔다. 현재 기본 규칙은 정리 성능이 비슷하고 계산 근거가 그대로 보이며,
미리 정한 답의 분포를 사용하지 않는다.

필요한 실행이 모두 끝나면 발표에 쓰는 표를 한 폴더로 모은다.

```powershell
.\.venv\Scripts\python.exe scripts\build_policy_scale_tables.py `
  --run-root runs\policy-scale-20260830 `
  --burden-summary runs\policy-scale-20260830\burden-ablation-final\summary.json `
  --route-choice-summary runs\policy-scale-20260830\route-choice-controlled\summary.json `
  --public-protocol-scale runs\public-protocol-policy-scale-20260830 `
  --common-facts-known runs\public-protocol-common-facts-known-rebuild-v2 `
  --shared-fact-report runs\public-protocol-shared-facts-v1\shared-fact-report.json `
  --live-model-smoke-before-result runs\presentation-live-model-smoke-fixed-20260830\result.json `
  --live-model-smoke-after-result runs\presentation-live-model-smoke-code-routed-20260830\result.json `
  --deterministic-smoke-before-result runs\presentation-deterministic-smoke-20260830\result.json `
  --deterministic-smoke-after-result runs\presentation-deterministic-smoke-code-routed-20260830\result.json `
  --output docs\internal\results\presentation-evidence-v2
```

생성된 CSV를 발표용 SVG 그림으로 바꾼다.

```powershell
.\.venv\Scripts\python.exe scripts\render_presentation_evidence_figures.py `
  --input-dir docs\internal\results\presentation-evidence-v2 `
  --output-dir docs\internal\diagrams
```

그림 생성기는 필요한 표가 빠졌거나 열 이름이 맞지 않으면 파일을 만들기 전에 오류를
낸다. 정상 종료되면 다음 파일 여덟 개가 `docs\internal\diagrams`에 생긴다.

- `clarifytrial-shared-information-coverage.svg`
- `clarifytrial-gray-zone-rescue.svg`
- `clarifytrial-public-budget-curves.svg`
- `clarifytrial-public-input-sensitivity.svg`
- `clarifytrial-structural-topology-budget1.svg`
- `clarifytrial-patient-limit-tradeoff.svg`
- `clarifytrial-route-choice.svg`
- `clarifytrial-compact-architecture.svg`

## 7. 테스트

평가와 발표 파일을 바꾼 뒤에는 관련 검사를 먼저 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\test_public_protocol_policy_scale.py `
  tests\test_public_protocol_common_facts_known.py `
  tests\test_interactive_stress.py `
  tests\test_interactive_statistics.py `
  tests\test_burden_benchmark.py `
  tests\test_burden_preference_routing.py `
  tests\test_shared_fact_report.py `
  tests\test_presentation_evidence_packaging.py `
  tests\test_presentation_figures.py `
  tests\test_presentation_terminal_demo.py `
  tests\test_presentation_workflow_integration.py `
  -q
```

저장소 전체를 확인하는 명령은 다음과 같다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

테스트 개수는 코드와 함께 달라진다. 실행이 끝났을 때 실패가 0인지 확인한다.

## 8. 결과를 읽을 때 지킬 기준

- 주 평가의 통계 단위는 최종 평가 환자 30명이다. 환자 한 명에게 연결된 시험 5건과
  가능한 정보 순서 120개를 새 환자로 세지 않는다.
- 질문 순서 결과는 선택한 공개 조건과 합성 환자에서 측정한 값이다. 실제 환자 모집
  성과나 전체 임상시험 조건의 판정 정확도로 옮겨 쓰지 않는다.
- 나이를 시작부터 알고 있는 민감도 결과를 주 결과와 함께 확인한다. 기본 평가의 큰
  차이에는 여러 시험이 공통으로 요구한 나이를 먼저 확인한 효과가 많이 들어 있다.
- 환자 경로 실험의 시간, 비용과 부담은 프로그램 동작을 보기 위해 정한 합성값이다.
- `deterministic` 실행의 외부 모델 호출과 토큰 사용은 0이 정상이다.
- TREC 검색, TrialGPT 개별 조건 판정과 위 질문 순서 평가는 서로 다른 문제를 다룬다.
  한 점수로 합치지 않는다.

수치와 표본 범위는 [검증 결과](CLARIFYTRIAL_VALIDATION_RESULTS.md), 입력자료의 구성은
[실험자료](CLARIFYTRIAL_DATASETS.md)에 정리돼 있다.

## 9. 다른 입력으로 실행

### 공식 대회 형식

`topics` 배열에 `num`과 `title`이 있는 환자 파일은 `run-challenge`로 읽는다. 기본
검색은 ClinicalTrials.gov 공식 API를 사용한다. 아래 실행은 외부 모델을 부르므로 모델
사용을 확인하는 옵션이 필요하다.

```powershell
New-Item -ItemType Directory -Force runs | Out-Null
Invoke-WebRequest `
  -Uri "https://raw.githubusercontent.com/skku-aihclab/aihc-lab/main/files/notice/healthcare-agentic-ai-challenge-2026/synthetic-patients.json" `
  -OutFile runs\official-synthetic-patients.json

.\.venv\Scripts\clarifytrial.exe run-challenge `
  --topics runs\official-synthetic-patients.json `
  --topic-id S001 `
  --topic-settings examples\challenge\topic-settings.json `
  --output runs\challenge-S001 `
  --confirm-model-run
```

여러 환자를 한꺼번에 실행하려면 `--topic-id S001`을 `--all-topics`로 바꾼다. 검색
응답은 `runs\clinicaltrials-search-cache`에서 재사용한다. 최신 검색을 다시 받으려면
`--refresh-trial-search`를 붙인다. 환자별 이동, 비용, 새 검사와 방문 제한은
`--topic-settings` 파일에 따로 적는다.

### 공개 평가자료 다시 만들기

현재 저장된 시험 조건과 합성 환자를 같은 원본에서 다시 만들고 비교한다.

```powershell
.\.venv\Scripts\clarifytrial.exe prepare-team-trials

.\.venv\Scripts\clarifytrial.exe select-team-evaluation-trials `
  --trials .research-cache\team-trials\trials.jsonl `
  --output runs\team-trial-expansion\selection.json

.\.venv\Scripts\clarifytrial.exe build-public-protocol-benchmark `
  --output runs\public-protocol-benchmark-rebuild

.\.venv\Scripts\clarifytrial.exe audit-public-protocol-benchmark
```

마지막 명령은 10개 질환, 공개 시험 50건, 구조화 조건 202개와 합성 환자 50명이 저장된
평가자료와 같은지 확인한다. 새 임상 성능을 측정하는 명령은 아니다.
