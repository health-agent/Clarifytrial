# ClarifyTrial

환자 자료가 불완전할 때 임상시험 후보를 성급하게 버리지 않고, 제한된 확인 횟수로
어떤 정보를 먼저 확인할지 고르는 학생 연구 프로젝트다. 각 시험에는 두 결과를 따로
낸다.

1. **참가 가능성이 남아 있어 후보로 계속 볼 것인가?**
2. **현재 확보한 자료만으로 참가 조건을 확인할 수 있는가?**

예를 들어 3개월 전 혈액검사 수치는 조건에 맞지만 시험에서 최근 14일 이내 결과를
요구한다면, 후보는 유지하고 현재 상태는 `최근 검사 결과 대기`로 표시한다. 최근
결과가 들어오면 그 정보와 연결된 시험만 다시 판단한다.

연구의 기본 입력은 환자 사실, 시험 조건, 부족한 정보와 확인 방법을 정해진 칸에 넣은
JSON 파일이다. 자유롭게 작성된 환자 기록을 이 형식으로 정리하는 기능도 선택적으로
사용할 수 있다.

## 한 사례가 처리되는 순서

```text
환자 상태와 임상시험 조건 입력
→ 관련 시험 검색
→ 시험 조건별 환자 상태와 근거 확인
→ 후보 유지 여부와 현재 확인 가능 여부를 따로 판단
→ 부족한 정보 가운데 먼저 확인할 항목 선택
→ 기록 조회·환자 질문·공식 결과 확인·새 검사 중 확인 방법 선택
→ 새 정보를 반영해 관련 시험만 다시 판단
→ 현재 확인된 시험과 추가 확인 후보를 나누어 설명
```

![ClarifyTrial 전체 흐름](docs/internal/diagrams/clarifytrial-workflow.svg)

[상세 실행 그림](docs/internal/diagrams/clarifytrial-detailed-workflow.svg)

## 여러 정보가 동시에 부족할 때

한 환자에게 후보 시험 5개가 있고 다음 정보 5개가 부족하다고 가정한다.

```text
최근 혈액검사
현재 복용약
과거 수술 여부
병리검사 결과
치료 시작일
```

확인 기회가 세 번이라면 다섯 정보를 모두 물을 수 없다. 최근 혈액검사 하나로 시험
4개의 판단을 끝낼 수 있고 과거 수술 여부는 시험 1개에만 영향을 준다면, 최근
혈액검사를 먼저 확인한다. 한 번 확인할 때마다 남아 있는 시험과 조건을 다시 계산해
다음 항목을 고른다.

현재 방식은 남은 확인 횟수 안에서 선택할 수 있는 정보 조합을 계산하고, 가장 많은
시험의 판단을 끝낼 수 있는 조합을 우선한다. 평가용으로 가려 둔 환자 답은 질문을
고르는 과정에서 사용하지 않는다.

## 같은 정보도 확인 방법이 다르다

부족한 정보마다 가능한 확인 방법을 구분한다.

- 현재 병원 기록 확인
- 다른 병원의 기존 기록 요청
- 환자에게 질문
- 이미 받은 공식 검사 결과 확인
- 새로운 검사 또는 평가
- 담당 의료진의 판단

같은 정보를 기존 기록에서 얻을 수 있다면 새 검사보다 기록 확인을 먼저 고른다.
환자의 시간 긴급성, 이동과 비용 부담, 새로운 검사나 절차에 대한 선호가 입력되면
확인 순서에 반영한다. 입력하지 않은 항목은 기본 규칙으로 처리한다.

새 검사나 별도 절차가 필요한 경우에는 필요한 이유와 다른 방법을 먼저 보여 주고
선택을 기다린다. 환자 부담은 확인 순서만 바꾸며 참가 조건 자체는 바꾸지 않는다.

## 결과에 표시되는 내용

최종 결과는 세 묶음으로 나뉜다.

- 현재 자료로 참가 조건을 확인한 시험
- 참가 가능성은 있지만 추가 확인이 필요한 시험
- 명확한 조건 위반이 확인된 시험

각 시험에는 다음 내용을 함께 표시한다.

- 사용한 환자 정보와 해당 시험 조건
- 아직 부족한 정보
- 다음에 확인할 내용과 확인 방법
- 처음 검색 순위와 현재 목록에 놓인 이유
- 새 정보가 들어온 뒤 달라진 판단

## 모델과 코드가 나누어 맡는 일

모델은 복잡한 시험 조건 문장을 읽고, 조건별 판단 이유와 환자가 이해할 질문을
작성한다. 코드는 날짜와 수치를 비교하고, 여러 시험에 영향을 주는 정보를 계산하며,
환자 부담과 승인 규칙을 적용한다.

조건 판단, 다음 확인 문장 작성, 중요한 판단의 선택적 검토는 각각 입력과 출력 형식이
분리된 역할이다. 같은 모델을 역할별로 여러 번 사용할 수 있으며, 역할끼리 자유롭게
토론을 이어 가지 않는다. 코드로 계산할 수 있는 수치·기간 조건은 코드 결과를 적용하고
교정 전후를 실행 기록에 남긴다.

## 사용한 자료

| 자료 | 사용한 부분 | 현재 범위 |
|---|---|---|
| TREC Clinical Trials 2021·2022 | 수만 건 중 관련 시험을 찾는 검색 기능 | 정답이 붙은 공개 평가자료 두 개 |
| TrialGPT 조건 판단 자료 | 환자 기록과 참가 조건 하나를 보고 내린 판단 | 전문가 답이 붙은 1,015개 조건 |
| ClinicalTrials.gov | 실제 시험 원문에서 평가 조건 제작 | 개발 15건·80조건, 새 평가 15건·92조건 |
| ClarifyTrial 합성 환자 | 여러 정보가 부족한 상태에서 질문하고 다시 판단 | 개발 30명, 별도 평가 30명 |
| 환자 부담 합성 상황 | 이동·비용·시간에 따라 확인 방법을 바꾸는지 검사 | 360개 상황·1,800회 실행 |

실제 환자 기록과 개인식별정보는 사용하지 않는다. 합성 환자는 수치, 날짜, 검사와
치료 상태를 먼저 정한 뒤 기록으로 만든다. 미리 정한 상태표에 없는 사실을 모델이
추가하면 평가자료에서 제외한다. 자세한 출처와 이용 조건은
[DATA_SOURCES.md](DATA_SOURCES.md)에 정리돼 있다.

## 현재 확인한 결과

### 관련 시험 검색

전문가가 환자와 관련 있다고 표시한 시험을 수만 건의 검색 결과 상위 500개 안에
남기는지 확인했다.

| 공개 평가자료 | 관련 시험을 상위 500개 안에 남긴 비율 |
|---|---:|
| TREC 2021 | 83.59% |
| TREC 2022 | 81.55% |

TrialGPT가 공개한 검색 절차를 같은 자료에서 다시 실행했으며 두 해 모두 논문 수치와
소수점 첫째 자리까지 같았다. 이 결과는 후보 시험을 찾아오는 검색 단계만 설명한다.

### 한 번의 조건 판단

지시문 작성에 사용하지 않은 환자 33명과 참가 조건 654개에서 현재 판단은 전문가가
붙인 답 이름과 79.1% 일치했고, 같은 자료의 TrialGPT 공개 답은 88.8% 일치했다.

별도 개발 표본에서 남은 오답 51개 중 47개는 시험 추천이 반대로 바뀐 문제가 아니라
`판단 가능`과 `정보 부족` 중 어느 이름을 붙일지의 차이였다. 두 답은 모두 후보에
남는다. 후보를 유지할지 제외할지로 다시 계산한 개발 표본에서는 211개 중 207개가
전문가와 같았다. 답 이름만 맞추는 추가 조정은 여기서 중단했다.

### 질문하고 다시 판단

별도로 남겨 둔 합성 환자 30명에게 시험 5개씩 연결해 시험 판단 150개를 만들었다.
부족한 정보는 최대 세 번 확인했다. 질문 뒤 판단은 합성자료를 만들 때 정해 둔 최종
상태와 비교했다.

| 부족한 정보를 처리한 방법 | 세 번 안에 판단을 끝낸 시험 |
|---|---:|
| 추가 정보를 확인하지 않음 | 63/150개, 42% |
| 입력 파일에 적힌 순서대로 최대 세 개 확인 | 99/150개, 66% |
| 남은 횟수 안에서 가장 많은 시험 판단을 끝낼 정보를 매번 계산 | 115/150개, 77% |

마지막 방법은 입력 순서대로 확인한 방법보다 환자 16명에서 더 많은 판단을 끝냈고,
12명에서는 같았으며, 2명에서는 적었다.

![질문 순서 결과](docs/internal/diagrams/clarifytrial-question-policy-results.svg)

### 환자 부담에 맞는 확인 방법

별도 합성 환자 20명에게 이동·비용·시간 조건을 바꿔 240개 상황을 만들었다. 이 가운데
이동과 비용 제한이 있는 80개 상황의 결과는 다음과 같다.

| 측정 내용 | 모든 환자에게 같은 확인 순서를 적용 | 환자의 제한을 반영해 확인 방법을 선택 |
|---|---:|---:|
| 환자가 실제로 이용할 수 있는 방법만 사용해 판단을 끝낸 시험 | 81.0% | 88.5% |
| 새 검사나 추가 방문을 피해야 하는 환자에게 그런 방법을 선택한 횟수 | 65회 | 0회 |

![환자 부담 반영 결과](docs/internal/diagrams/clarifytrial-patient-burden-results.svg)

현재 수치는 공개 시험 조건과 합성 환자를 사용한 기능 평가다. 실제 임상 현장의
정확도나 업무 시간 개선을 측정한 결과로 사용하지 않는다.

## 현재 완성된 범위

- 정해진 JSON 형식의 환자와 시험 입력
- 관련 시험 검색과 처음 후보 순위 보존
- 조건별 판단과 근거 저장
- 후보 유지 여부와 현재 확인 가능 여부의 분리
- 여러 정보가 동시에 부족할 때 확인 순서 계산
- 환자 부담에 맞는 확인 방법 선택
- 직접 답변, 파일 답변, 중단과 재개
- 새 정보와 연결된 시험만 다시 판단
- 최종 목록과 판단 변화 저장
- 여러 환자 일괄 평가와 표·그림·보고서 생성
- 팀의 `topics[num, title]` 형식 합성 환자 입력 처리
- 반복되는 시험 조건 정리 결과의 재사용

자유롭게 쓴 환자 기록을 JSON으로 정리하는 기능도 연결되어 있지만, 기본 연구 평가는
처음부터 정해진 JSON 입력을 사용한다.

## 다음 평가 범위

현재의 3개 질환·15개 시험 평가는 기능을 정밀하게 확인하는 첫 자료로 유지한다. 다음
평가에서는 검색 범위와 정밀 채점 범위를 따로 넓힌다.

1. 팀에서 만든 ClinicalTrials.gov 시험 1,931건 자료를 넓은 후보 검색에 사용한다.
2. 모집이 끝난 시험은 현재 추천 대상에서 제외한다.
3. 정밀 평가 범위는 약 10개 질환과 50개 시험으로 넓힌다.
4. 새 질환에서는 약 35명의 합성 환자와 두 가지 정보 부족 상태를 추가한다.
5. 1,931개 시험 전체에 정답을 붙이지 않고, 검색된 시험 가운데 정밀 평가 대상으로
   고른 시험에만 조건별 정답과 질문 뒤 변화를 만든다.
6. 자료 범위를 고정한 뒤 현재 전체 프로그램으로 최종 통계와 모델 사용량을 다시 낸다.

이 확장은 시험 수만 늘리기 위한 작업이 아니다. 수치, 기간, 복약, 과거 치료,
임신·피임, 환자 답변, 기록 조회와 새 검사처럼 서로 다른 조건과 확인 방법이 포함되도록
질환과 시험을 고른다.

## 관련 자료

| 자료 | 내용 |
|---|---|
| [연구 요약](docs/internal/CLARIFYTRIAL_REPORT_PRESENTATION_PACKET.md) | 핵심 아이디어, 대표 사례와 결과 |
| [현재 상태](docs/internal/CURRENT_STATUS.md) | 완성된 범위와 다음 작업 |
| [검증 결과](docs/internal/CLARIFYTRIAL_VALIDATION_RESULTS.md) | 각 숫자의 실행 조건과 한계 |
| [현행 연구계획 v5](docs/internal/CLARIFYTRIAL_RESEARCH_PLAN_V5.md) | 연구 질문과 비교 기준 |
| [실험자료 정리](docs/internal/CLARIFYTRIAL_DATASETS.md) | 공개자료와 합성자료 구성 |
| [전체 자료 구성](docs/internal/README.md) | 연구 자료와 구현·재현 자료의 구분 |

## 개발자용 실행·코드 참고

프로그램 설치와 재실행에 필요한 명령을 정리했다.

### 설치와 자동 검사

Python 3.11 이상이 필요하다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,codex-subscription]"
.\.venv\Scripts\python.exe -m pytest -q
```

### 준비된 예제로 전체 흐름 실행

```powershell
.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials examples\general_screening\trials.jsonl `
  --answers examples\general_screening\answers.json `
  --provider deterministic `
  --output runs\general-screening
```

### 팀의 합성 환자 파일로 실행

`topics` 배열 안에 `num`과 `title`이 있는 JSON 파일을 받는다. 실제 모델을 호출하므로
확인 옵션이 필요하다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-challenge `
  --topics "C:\path\to\synthetic-patients.json" `
  --topic-id S001 `
  --candidate-search trialgpt `
  --trialgpt-corpus C:\path\to\trial-corpus.jsonl `
  --trialgpt-cache C:\path\to\retrieval-cache `
  --provider codex-subscription `
  --effort medium `
  --output runs\challenge-S001 `
  --confirm-model-run
```

모든 환자는 `--topic-id S001` 대신 `--all-topics`로 실행한다. 중단·재개, 직접 답변,
승인 대기, 시험 조건 캐시와 전체 평가 명령은
[쉬운 실험 안내](docs/internal/CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md)에 정리돼 있다.

### 전체 평가와 보고서

```powershell
.\.venv\Scripts\clarifytrial.exe run-workflow-evaluation `
  --provider deterministic `
  --split heldout `
  --action-budget 3 `
  --concurrency 4 `
  --output runs\full-workflow-evaluation

.\.venv\Scripts\clarifytrial.exe build-report `
  --question-policy runs\natural-question-policy-fully-missing-heldout-v1.json `
  --burden runs\patient-burden-v2\summary.json `
  --workflow runs\full-workflow-evaluation\summary.json `
  --retrieval runs\trialgpt-retrieval\trec_2021\hybrid\summary.json `
  --retrieval runs\trialgpt-retrieval\trec_2022\hybrid\summary.json `
  --output runs\research-report
```

### 코드 위치

| 위치 | 내용 |
|---|---|
| `src/clarifytrial/agents/` | 역할별 모델 호출과 출력 계약 |
| `src/clarifytrial/preparation/` | 환자 기록과 시험 조건 정리 |
| `src/clarifytrial/retrieval/` | 관련 시험 검색 |
| `src/clarifytrial/interactive/` | 질문 순서, 확인 방법과 환자 부담 규칙 |
| `src/clarifytrial/workflow/` | 여러 시험 판단과 새 정보 반영 |
| `src/clarifytrial/app/` | 일반 JSON·대회 입력, 직접 답변, 세션 재개와 평가 |
| `src/clarifytrial/reporting/` | 최종 목록과 연구 보고서 생성 |
| `tests/` | 조건 판단, 질문 순서와 전체 흐름 검사 |

전체 명령과 내부 문서는 [문서 색인](docs/internal/README.md)에서 찾을 수 있다.
