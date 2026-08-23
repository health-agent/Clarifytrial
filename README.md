# ClarifyTrial

현재 버전은 불완전한 환자 자료에서 후보 시험을 성급하게 버리지 않으면서, 제한된
확인 횟수로 어떤 정보를 먼저 확인할지 고르는 학생 과제 코드다. 각 시험에는 다음
두 결과를 따로 낸다.

1. **참가 가능성이 남아 있어 후보로 계속 볼 것인가?**
2. **현재 확보한 자료만으로 참가 조건을 확인할 수 있는가?**

예를 들어 3개월 전 혈액검사 수치는 조건에 맞지만 시험에서 최근 14일 이내 결과를
요구한다면 후보는 유지하고 현재 상태는 `최근 검사 결과 대기`로 표시한다. 최근
결과가 들어오면 그 정보와 연결된 시험만 다시 판단한다.

연구의 기본 입력은 정해진 칸에 환자 사실, 시험 조건, 부족한 정보와 확인 방법을
넣은 JSON 파일이다. (자유롭게 작성된 진료기록을 이 형식으로 바꾸는 기능도 있긴함)

## 한 사례가 처리되는 순서

```text
정해진 형식으로 환자 상태와 임상시험 조건 입력
→ 관련 시험 검색
→ 조건별 환자 상태와 근거 확인
→ 후보 유지와 현재 확인을 따로 판단
→ 부족한 정보 가운데 먼저 확인할 항목 선택
→ 기록 조회·환자 질문·공식 확인·새 검사 중 확인 방법 선택
→ 새 정보를 반영해 관련 조건만 다시 판단
→ 현재 확인된 시험과 추가 확인 후보를 나누어 설명
```

![ClarifyTrial v5 에이전트 구조](docs/internal/diagrams/clarifytrial-workflow.svg)

[상세 실행 그림](docs/internal/diagrams/clarifytrial-detailed-workflow.svg) ·
[수정 가능한 Mermaid 원본](docs/internal/diagrams/clarifytrial-performance-agent-architecture.mmd)

## 여러 정보가 동시에 부족할 때

한 환자에게 후보 시험 5개가 있고 다음 정보 5개가 부족하다고 가정한다.

```text
최근 혈액검사
현재 복용약
과거 수술 여부
병리검사 결과
치료 시작일
```

확인할 수 있는 횟수는 3번이다. 최근 혈액검사 하나로 시험 4개의 판단을 끝낼 수 있고
과거 수술 여부는 시험 1개에만 영향을 준다면 최근 혈액검사를 먼저 확인한다. 한 번
확인할 때마다 남아 있는 시험과 조건을 다시 계산해 다음 항목을 고른다.

ClarifyTrial의 정보 선택 방법은 남은 횟수 안에서 확인할 수 있는 정보 조합을 모두 살펴보고, 판단을 끝낼
수 있는 시험이 가장 많은 조합을 우선한다. 평가용으로 숨겨 둔 환자 답은 정보를
고르는 과정에서 보지 않는다.

## 환자에게 보여 주는 결과

결과는 세 묶음으로 나뉜다.

- 현재 자료로 참가 조건을 확인한 시험
- 참가 가능성은 있지만 추가 확인이 필요한 시험
- 명확한 조건 위반이 확인된 시험

각 시험에는 사용한 환자 근거, 해당 시험 조건, 부족한 정보와 다음 확인 방법을 함께
표시한다. 추가 확인 후보를 현재 참가 가능으로 확정하지 않는다.

## 확인 방법과 환자 부담

부족한 정보마다 가능한 확인 방법을 구분한다.

- 현재 병원 기록 확인
- 다른 병원의 기존 기록 요청
- 환자에게 질문
- 이미 받은 공식 검사 결과 확인
- 새로운 비침습 검사 또는 평가
- 새로운 침습 절차나 치료 변경
- 담당 의료진의 판단

같은 정보를 기존 기록에서 얻을 수 있다면 새 검사보다 기록 확인을 먼저 고른다.
환자의 시간 긴급성, 이동과 비용 부담, 새로운 검사나 절차에 대한 선호는 선택적으로
입력할 수 있다. 입력하지 않은 항목은 `답 없음`으로 남기고 표시된 기본 규칙으로
처리한다.

새 검사, 침습 절차와 치료 변경은 자동으로 실행하지 않는다. 필요한 이유와 다른
경로를 보여 주고 환자나 의료진의 결정을 기다린다. 환자 부담 입력은 확인 순서만
바꾸며 참가 조건 자체를 바꾸지 않는다.

## 에이전트 구성

에이전트는 서로 다른 인공지능 제품을 뜻하지 않는다. 같은 모델을 역할별 지시문과
입력으로 여러 번 부를 수 있다. 각 역할의 대화 기록과 출력 형식은 분리한다.

| 역할 | 맡은 일 | 호출 시점 |
|---|---|---|
| 진행 관리 | 환자 상태, 남은 후보와 확인 횟수를 관리하고 다음 실행과 종료를 결정 | 기본은 코드, 비교할 때만 모델 호출 |
| 검색·판단 | 후보를 찾고 조건별 임상 상태와 현재 근거의 충분성을 판단 | 처음과 관련 정보 변경 뒤, 바뀐 시험을 한 묶음으로 호출 |
| 다음 확인 | 코드가 고른 정보와 확인 방법을 환자가 이해할 질문이나 요청문으로 작성 | 정보가 부족할 때 |
| 선택적 검토 | 중요한 제거·확정의 약한 근거, 기록 충돌과 설명 불일치를 다시 확인 | 정해진 조건에 걸릴 때만 |

환자 기록 정리와 시험 조건 정리는 반복 흐름에 들어가기 전에 한 번 수행하는 준비
호출이다. 같은 모델을 여러 역할에 쓸 수 있지만 역할별 입력, 대화 기록, 허용 도구와
출력 형식은 분리한다. 에이전트끼리 자유 토론을 이어 가지 않는다.

### 모델이 맡는 일

- JSON에 함께 담긴 복잡한 참가 조건 문장 해석
- 조건별 판단 이유 작성
- 환자 질문과 결과 설명 작성

### 코드가 맡는 일

- 날짜와 수치 비교
- 숨은 답과 공개 자료 분리
- 여러 사실의 영향 범위와 남은 횟수 계산
- 환자 부담과 사람 승인 규칙 적용
- 새 정보 뒤 관련 조건과 두 결과 갱신

코드로 계산할 수 있는 숫자 조건에서는 모델이 다른 답을 내도 코드 결과를 적용하고
교정 전후를 실행 기록에 남긴다.

## 사용한 자료

| 자료 | 사용한 부분 | 현재 범위 |
|---|---|---|
| TREC Clinical Trials 2021·2022 | 수만 건 중 관련 시험을 찾는 검색 기능 검사 | 정답이 붙은 공개 평가자료 두 개 |
| TrialGPT 조건 판단 자료 | 환자 기록과 참가 조건 하나를 보고 내린 판단 검사 | 전문가 답이 붙은 1,015개 조건 |
| ClinicalTrials.gov API v2 | 실제 공개 임상시험 문서에서 계산 가능한 조건 준비 | 개발 15건·80조건, 새 평가 15건·92조건 |
| ClarifyTrial 합성 환자 | 여러 정보가 부족한 상태에서 질문하고 다시 판단 | 개발 30명과 새 평가 30명 |
| 환자 부담 합성 상황 | 이동·비용·시간에 따라 확인 방법을 바꾸는지 검사 | 360개 상황·1,800회 실행 |

실제 환자 기록과 개인식별정보는 사용하지 않는다. 합성 환자는 수치, 날짜, 검사와
치료 상태를 먼저 정한 뒤 문장으로 만든다. 상태표에 없는 생활습관이나 임상 사실을
모델이 덧붙이면 자료 검사에서 거부한다. 자세한 출처와 이용 조건은
[DATA_SOURCES.md](DATA_SOURCES.md)에 있다.

## 현재 예비결과

### 관련 시험을 찾아오는 검색

수만 건의 임상시험 문서에서 전문가가 환자와 관련 있다고 표시한 시험을 검색 결과
상위 500개 안에 남기는지 검사했다. TrialGPT가 사용한 검색 절차를 같은 공개
평가자료에서 다시 실행했다.

| 공개 평가자료 | 전문가가 관련 있다고 표시한 시험을 검색 결과 상위 500개 안에 남긴 비율 |
|---|---:|
| 2021년 공개 평가자료(TREC) | 83.59% |
| 2022년 공개 평가자료(TREC) | 81.55% |

두 해 모두 TrialGPT 논문에 실린 수치와 소수점 첫째 자리까지 같았다. 즉, 관련
시험을 후보로 가져오는 검색 능력은 사실상 같은 수준으로 재현됐다. 이 검사는 검색
단계만 본 것이다. 각 참가 조건을 올바르게 판단하는지, 최종 추천이 맞는지는
포함하지 않는다.

### 한 번의 조건 판단

지시문을 만들 때 사용하지 않은 환자 33명과 참가 조건 654개로 한 번의 조건 판단을
검사했다. 현재 조건 판정 지시문은 전문가 정답과 79.1% 일치했다. 같은 평가자료에
들어 있는 TrialGPT의 공개 답은 전문가 정답과 88.8% 일치했다. 숫자만 보면 현재
조건 판정 지시문의 일치율이 낮다.
그러나 별도 개발 실험에서 틀린 51개 중 47개는 추천을 잘못한 문제가 아니라,
`판단 가능`이라고 부를지 `정보 부족`이라고 부를지의 차이였다. ClarifyTrial에서는
두 답 모두 후보에 남긴다. 실제로 후보를 남길지 뺄지로 채점하면 개발 표본 211개 중
207개인 98.1%가 전문가와 같았다. 이 범위에서는 거의 모두 맞춘 셈이다. 따라서
TrialGPT의 답 이름을 그대로 흉내 내 점수만 높이는 조정은 하지 않았다. 어떤 답을
같은 뜻으로 묶느냐에 따라 통계가 크게 달라지기 때문이다. 다만 98.1%는 개발 표본을
사후에 다시 계산한 값이며 최종 임상 성능을 뜻하지 않는다.

### 질문하고 다시 판단

질문 규칙을 정한 뒤 새로 만든 합성 환자 30명, 시험 판단 150건에서 확인 횟수 3회를
허용했다. 채점 정답은 합성 환자를 만들 때 미리 정한 전체 사실로 계산했다. 실제
검사나 절차를 시행한 실험은 없다.

| 부족한 환자 정보를 처리한 방법 | 추가 정보를 최대 세 번 확인한 뒤, 모든 환자 정보를 알 때와 같은 판단에 도달한 시험 비율 |
|---|---:|
| 추가 정보를 확인하지 않고 처음 환자 자료만 사용 | 42% |
| 처음 빠진 정보 목록의 앞 세 항목을 적힌 순서대로 확인 | 75% |
| 가장 많은 시험에 연결된 정보부터 세 항목을 확인 | 87% |
| 남은 세 번 안에 가장 많은 시험 판단을 끝낼 정보 조합을 매번 다시 계산 | 89% |

![질문 순서 예비결과](docs/internal/diagrams/clarifytrial-question-policy-results.svg)

추가 확인을 다섯 번까지 허용하면 입력 파일에 적힌 순서대로 확인한 경우와, 가장 많은
시험 판단을 끝낼 정보를 매번 계산한 경우 모두 모든 환자 정보를 알 때의 판단에
도달했다. 도달할 때까지 확인한 정보는 전자가 환자당 평균 4.53개, 후자가 3.63개였다.
연구진이 만든 환자와 AI가 먼저 검토한 시험 조건으로 얻은 초기 결과다. 적용 범위는
이 합성 평가자료 안으로 제한한다.

핵심 값 다섯 개를 처음 입력에서 완전히 지운 조건도 같은 30명에게 실행했다. 질문
세 번 뒤 가장 많은 시험 판단을 끝낼 정보를 매번 계산한 경우는 88.7%, 처음 빠진
정보 목록의 앞 세 항목을 확인한 경우는 75.3%였다. 전자는 같은 확인 횟수에서 최종
판단에 필요했던 정보를 모두 골랐고, 총 88개 정보를 확인한 뒤 그중 어떤 시험의
판단도 더 바꾸지 못한 정보는 9개였다. 이 필요 여부는 정해 둔 합성 답을 실행 뒤에만
사용해 계산했다.

### 환자 부담에 맞는 확인 방법 선택

개발에 사용하지 않은 합성 환자 20명에서 부담 조건을 바꾼 상황 240개를 만들었다.
이 가운데 이동·비용 부담을 입력한 상황 80개의 결과는 다음과 같았다.

| 무엇을 측정했는가 | 모든 환자에게 같은 비용표를 적용해 확인 방법을 선택 | 환자의 이동·비용·시간 제한을 반영해 확인 방법을 선택 |
|---|---:|---:|
| 이동·비용 제한 때문에 사용할 수 없는 확인 방법을 제외하고도, 모든 환자 정보를 알 때와 같은 판단에 도달한 시험 비율 | 81.0% | 88.5% |
| 환자가 새 검사나 추가 방문을 피해야 한다고 입력했는데도 그런 방법을 선택한 횟수 | 65회 | 0회 |

![환자 부담을 반영한 확인 방법 예비결과](docs/internal/diagrams/clarifytrial-patient-burden-results.svg)

이동·비용 부담과 이용 가능한 기록은 연구진이 합성 상황으로 정했다. 0회는 기존
기록이나 환자 답변을 사용하거나, 더 확인할 수 없으면 대기 상태로 남긴 경우다. 현재
확인한 범위는 규칙이 이 합성 입력에 맞게 움직였는지까지다.

## 지금까지 완성한 범위

정해진 형식의 JSON 입력, 관련 시험 검색, 조건 판단, 부족한 정보의 확인 순서, 환자
부담에 맞는 확인 방법, 새 정보가 들어온 뒤 다시 판단하는 흐름까지 구현했다. 자유롭게
작성된 기록을 JSON으로 바꾸는 선택 기능도 합성 사례 한 건에서 끝까지 작동시켰다.
숫자·기간 조건으로 참가 조건 불충족이 확인되면 현재 값이 기준보다 얼마나 높거나
낮은지도 같은 조건 안에서만 표시한다.

이 흐름은 새 자료를 넣는 일반 실행 명령과, 저장소의 합성 자료로 전 과정을 재현하는
체험 화면으로 나뉜다. 일반 실행 명령은 새 환자 JSON과
시험 JSON 또는 JSONL을 받아 검색, 판단, 질문, 답변, 재판정과 최종 결과를 처리한다.
답변은 터미널에 직접 입력하거나 JSON 파일로 줄 수 있고, 중간에 끝낸 실행은 저장된
세션에서 이어갈 수 있다. 한 정보를 얻지 못하면 같은 질문만 되풀이하지 않고 다른
정보를 확인한다. 새 검사처럼 별도 선택과 승인이 필요한 방법은 실행하지 않고 멈춘 뒤,
환자 선택과 담당자 승인을 각각 기록하면 이어서 진행한다. 체험 화면은 저장소의 합성
환자와 공개 시험 15개를 써서 같은 과정을 한 화면에 재현한다.

직접 입력한 문장은 기본적으로 환자가 말한 내용으로 저장한다. 공식 결과를 요청한
화면에서 입력했더라도 그 사실만으로 공식 자료가 되지는 않는다. JSON에 자료 종류,
자료 위치, 확인 상태와 실제 날짜가 들어온 경우에만 그 구분을 사용한다. 실행 기록에는
문장 입력, 직접 작성한 JSON, JSON 파일 가운데 어떤 방식으로 받은 자료인지도 남긴다.

아직 다음 내용은 확인하지 않았다.

- 의사 또는 임상시험 담당자와의 일치도
- 후보 검색부터 최종 추천까지를 한 숫자로 합친 성능
- 실제 환자의 부담이나 업무 시간 개선

자유 형식 기록 해석은 입력 규격 밖의 선택 기능이므로 현재 에이전트의 완성 조건으로
두지 않는다.

새 평가의 92조건은 공개 시험 원문에서 만든 연구용 평가 조건이다. 과거 Solar 합성
데모 수치와 84%는 현재 v5 결과표에서 제외했다.

## 빠른 실행

Python 3.11 이상이 필요하다. 다음 명령은 외부 모델 없이 오래된 혈액검사 사례에서
질문한 뒤 다시 판단하는 흐름을 실행한다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,codex-subscription]"
.\.venv\Scripts\python.exe -m pytest -q
```

새 환자와 시험 파일로 전체 흐름을 실행하는 기본 명령은 다음과 같다. 예제의 답변
파일을 빼면 필요한 내용을 터미널에서 직접 묻는다. 실행을 중단하면 출력 폴더의
`session.json`을 `--resume`에 넣어 이어갈 수 있다. 앞에서 얻지 못한 정보를 다시
확인하려면 `--retry-unavailable`을 붙인다. 환자 선택이나 담당자 승인을 기다리는
상태라면 각각 `--approve-patient-choice`, `--authorize-clinician`을 붙여 재개한다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials examples\general_screening\trials.jsonl `
  --answers examples\general_screening\answers.json `
  --provider deterministic `
  --output runs\general-screening

.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials examples\general_screening\trials.jsonl `
  --resume runs\general-screening\session.json `
  --provider deterministic `
  --output runs\general-screening-resumed
```

기본 명령은 전달한 시험 파일 안에서 가벼운 검색을 한다. 준비된 TrialGPT 검색 자료가
있다면 같은 명령에서 수만 건 검색 결과를 사용하고, 그중 구조화 조건이 들어 있는
시험만 실제 판단 대상으로 넘길 수 있다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials path\to\structured-trials.jsonl `
  --candidate-search trialgpt `
  --trialgpt-corpus path\to\trial-corpus.jsonl `
  --trialgpt-cache path\to\retrieval-cache `
  --output runs\general-screening
```

입력 필드의 전체 설명은 [범용 예제](examples/general_screening/README.md)와 다음 명령이
만드는 JSON Schema에서 확인할 수 있다.

```powershell
.\.venv\Scripts\clarifytrial.exe export-schemas --output runs\schemas
```

전체 경로를 한 화면에서 보려면 다음 명령을 실행한다. 기본 합성 환자 한 명의 표준
JSON을 읽고, 시험 15개 검색, 후보 5개 조건 판단, 최대 세 번의 확인과 재판정, 최종
결과와 역할별 호출량을 보여 준다. GPT-5.6 Sol `medium`을 실제로 호출한다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-full-ui --confirm-model-run
```

각 합성 답변을 적용하기 전에 Enter를 기다린다. 멈추지 않고 끝까지 보려면 `--auto`를
추가한다. 다른 합성 환자는 `--patient-id`로 고를 수 있다.

외부 모델 없이 작은 연결만 확인하려면 다음 명령을 사용한다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-example `
  --case examples\stale_lab `
  --output runs\stale_lab
```

질문 순서 기능만 빠르게 보려면 다음 축소 명령을 실행한다. 환자 한 명과 미리 연결된
시험 5개만 사용하며 검색과 역할별 모델 호출은 실행하지 않는다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-text-demo `
  --patient-id natural-breast_cancer-11 `
  --input-state fully-missing `
  --action-budget 3 `
  --auto `
  --output runs\text-ui-demo.json
```

30명의 질문 순서를 한꺼번에 다시 계산하는 명령은
[쉬운 실험 안내](docs/internal/CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md)에 있다.

추가 정보를 확인하지 않는 방법, 입력 목록 순서대로 확인하는 방법, 가장 많은 시험
판단을 끝낼 정보를 매번 계산하는 방법을 같은 30명에게 전체 프로그램으로 실행하려면
다음 명령을 사용한다. 환자별 실행은 병렬로 처리되며 사례별 결과와 전체 요약을 함께 저장한다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-workflow-evaluation `
  --provider deterministic `
  --split heldout `
  --action-budget 3 `
  --concurrency 4 `
  --output runs\full-workflow-evaluation
```

이미 만든 질문 순서, 환자 부담, 전체 흐름과 검색 결과를 한 보고서로 합치는 명령은
다음과 같다. 표, Markdown 요약과 SVG 그림이 같은 폴더에 만들어진다. 전체 흐름
표에는 후보 유지·제외, 현재 확정 여부, 잘못된 제외, 정보가 부족한데 확정한 경우,
질문 뒤 판단이 끝난 시험 수와 환자별 비교 결과가 함께 들어간다.

```powershell
.\.venv\Scripts\clarifytrial.exe build-report `
  --question-policy runs\natural-question-policy-fully-missing-heldout-v1.json `
  --burden runs\patient-burden-v2\summary.json `
  --workflow runs\full-workflow-evaluation\summary.json `
  --retrieval runs\trialgpt-retrieval\trec_2021\hybrid\summary.json `
  --retrieval runs\trialgpt-retrieval\trec_2022\hybrid\summary.json `
  --output runs\research-report
```

자유 형식 기록을 JSON으로 정리하는 선택 연결 기능의 합성 예제는
[examples/natural_screening](examples/natural_screening)에 있다. 이 실행은 실제
모델을 호출하므로 확인 옵션이 필요하고, 숨은 합성 답은 별도 파일에서만 읽는다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-natural-screening `
  --request examples\natural_screening\request.json `
  --candidate-search local-bm25 `
  --trial-sources examples\natural_screening\trial_sources.json `
  --hidden-answers examples\natural_screening\hidden_answers.json `
  --output runs\natural-screening `
  --provider codex-subscription `
  --confirm-model-run
```

전체 명령과 평가 재현 순서는
[쉬운 실험 안내](docs/internal/CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md)와
[관련 시험 검색·평가 구현계획](docs/internal/CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md)에
정리돼 있다.

## 코드 위치

| 위치 | 내용 |
|---|---|
| `src/clarifytrial/agents/` | 세 고정 역할과 선택적 검토의 호출 경계 |
| `src/clarifytrial/preparation/` | 자연어 환자 기록과 시험 조건 정리 |
| `src/clarifytrial/retrieval/` | 관련 시험 검색과 조건 판단에 필요한 환자 문장 표시 |
| `src/clarifytrial/interactive/` | 평가용으로 숨긴 답, 질문 순서와 환자 부담 규칙 |
| `src/clarifytrial/workflow/` | 여러 시험을 판단하고 새 정보 뒤 다시 판단하는 전체 흐름 |
| `src/clarifytrial/app/` | 일반 JSON 입력, 직접 답변, 세션 재개와 세 방식 전체 평가 |
| `src/clarifytrial/ui/` | 15개 시험 검색부터 역할별 호출·질문·최종 결과까지 보여 주는 통합 터미널 화면 |
| `src/clarifytrial/reporting/` | 최종 목록·기준 차이와 실험 결과표·그림·Markdown 보고서 생성 |
| `src/clarifytrial/datasets/` | 공개자료 준비, 합성 환자와 자연어 평가자료 |
| `tests/` | 조건 판단, 자료 분리, 질문 순서, 승인 규칙과 전체 흐름 검사 |

## 문서 안내

| 문서 | 역할 |
|---|---|
| [현재 상태](docs/internal/CURRENT_STATUS.md) | 완료한 요구사항, 현재 결과와 남은 외부 검증 |
| [현행 연구계획 v5](docs/internal/CLARIFYTRIAL_RESEARCH_PLAN_V5.md) | 연구 질문, 비교 범위와 주장 기준 |
| [모델 호출과 코드 실행 구조](docs/internal/CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md) | 역할별 입력·출력, 호출 조건과 세부 흐름 |
| [근거문헌과 공개 코드](docs/internal/CLARIFYTRIAL_AGENT_SOURCE_INDEX.md) | 각 단계에서 참고한 논문과 가져온 범위 |
| [실험자료 정리](docs/internal/CLARIFYTRIAL_DATASETS.md) | 공개자료의 정답, 합성자료 생성 방법과 한계 |
| [검증 결과](docs/internal/CLARIFYTRIAL_VALIDATION_RESULTS.md) | 실행 조건, 결과, 비용과 채택·기각 결정 |
| [보고서·발표 정리본](docs/internal/CLARIFYTRIAL_REPORT_PRESENTATION_PACKET.md) | 쉬운 전체 설명, 핵심 결과 그림, 대표 사례와 발표 문안 |
| [전체 문서 색인](docs/internal/README.md) | 문서별 역할과 읽는 순서 |

환자 정보, 외부 자료와 의료 출력에 관한 규칙은 [AGENTS.md](AGENTS.md),
[DATA_SOURCES.md](DATA_SOURCES.md), [MEDICAL_DISCLAIMER.md](MEDICAL_DISCLAIMER.md)를
따른다.
