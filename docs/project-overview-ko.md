# ClarifyTrial 프로젝트 한눈에 보기

![ClarifyTrial 전체 워크플로](assets/clarifytrial-workflow.png)

## 이 프로젝트는 무엇인가

임상시험 코디네이터 또는 임상의가 환자 한 명에게 여러 임상시험을 검토할 때,
현재 정보만으로 판단할 수 없는 조건을 찾아 필요한 정보부터 확인하고, 답변과
관련된 부분만 다시 판단하는 연구용 시스템이다.

```text
환자 임상요약 + 실제 임상시험 기준
→ 후보 임상시험 검색
→ 기준별 최초 판단과 근거
→ 공통 부족정보 정리 및 우선순위 계산
→ 질문 또는 EHR 조회
→ 관련 기준만 재판정
→ 추천 순위, 설명, 남은 불확실성 출력
```

환자에게 최종 의료 결정을 내리는 제품이 아니다. 시스템이 만든 결과는
코디네이터와 임상의의 검토를 돕는 연구용 초안이며, 모든 화면과 결과에
`MEDICAL_DISCLAIMER.md`의 면책 고지를 포함한다.

## 가장 중요한 설계

### 1. 모든 판단을 한 상태에 저장한다

이전 데모는 각 에이전트의 JSON 출력을 다음 에이전트에 전달하는 방식이었다.
ClarifyTrial은 `PatientSession`에 구조화 환자 정보, 임상시험별 기준 판단,
부족정보, 질문·답변 이력, 변경된 기준과 최종 추천을 함께 저장한다.

따라서 어떤 정보 때문에 어떤 임상시험의 판단이 바뀌었는지 추적할 수 있다.

### 2. 같은 질문은 한 번만 한다

여러 임상시험이 모두 ECOG, 임신 여부, 장기이식 이력처럼 같은 정보를
요구하더라도 trial별로 반복 질문하지 않는다. 부족정보를
`global_missing_variable_pool`에서 합치고, 한 번 얻은 답을 관련 trial에
공통 적용한다.

### 3. 모든 부족정보를 묻지 않는다

`Next-Best-Action Planner`는 질문 횟수 제한 안에서 다음 요소를 이용해
확인할 정보를 고른다.

- 여러 trial과 criterion에 미치는 영향
- 판정을 바꿀 가능성
- 확인 난이도와 사용자 부담
- EHR 또는 임상노트에서 얻을 수 있는지 여부
- 안전에 민감한 정보인지 여부

초기 MVP에서는 이 점수를 해석 가능한 Python 규칙으로 계산한다. 학습 기반
정책은 비교 데이터가 쌓인 뒤의 연구 항목이다.

### 4. 답변과 관련된 기준만 다시 판단한다

환자가 ECOG에 답했다면 나이, 성별, 바이오마커를 전부 다시 생성하지 않는다.
ECOG와 연결된 `criterion_id`만 찾아 재평가한다. 이것이 표적 재평가다.

### 5. LLM과 판정 규칙을 분리한다

LLM은 긴 기준 구조화, 환자 문장 정규화, 의미 매칭, 질문 표현과 설명을 맡는다.
Python은 부족정보 중복 제거, 질문 제한, 상태 갱신, 선정·제외 효과, 최종 추천
우선순위와 사람 검토 전환을 맡는다.

## 이전 Health Agent 데모와 비교

| 항목 | 이전 6-call 데모 | ClarifyTrial 방향 |
|---|---|---|
| 실행 구조 | 정해진 호출을 순서대로 실행 | 상태에 따라 질문·조회·재판정·종료가 갈림 |
| 상태 | 앞 에이전트 JSON을 다음 호출에 포함 | 중앙 `PatientSession`에 전체 변화 저장 |
| 질문 | trial별 unknown에서 질문 생성 | 여러 trial의 공통 부족정보를 합치고 우선순위화 |
| 정보 획득 | 가상 답변을 같은 에이전트가 생성 | 숨은 정답, 실제 사용자 답변 또는 선택적 EHR 조회 |
| 재판정 | 전체 최종 판단을 다시 생성 | 답변과 연결된 criterion만 재평가 |
| 최종 결정 | LLM 결과를 Python이 형식 보정 | 결정 규칙 자체를 Python에 고정 |
| 연구 질문 | 주어진 후보를 얼마나 잘 판단하는가 | 어떤 정보가 적은 행동으로 추천을 가장 많이 개선하는가 |

## 데이터 사용 가능성 검증 결과

2026-07-11에 공식 문서, 공개 데이터와 실제 API 응답을 확인했다. 자세한 출처,
이용조건과 수집일은 [`DATA_SOURCES.md`](../DATA_SOURCES.md)에 기록한다.

| 데이터 | 확인 결과 | 정확한 역할 | 주의점 |
|---|---|---|---|
| ClinicalTrials.gov API v2 | 사용 가능 | 모집 상태, 질환, 지역, 선정·제외 기준 수집 | 정답 라벨이 아니며 실행별 query·data timestamp·NCT ID·hash 기록 필요 |
| TrialGPT Criterion Annotations | 사용 가능, 1,015개 criterion 행 | 조건별 전문가 판정과 근거 문장 평가 | 53명·103개 trial의 criterion subset이며 `conflict` 정답은 없음 |
| TREC Clinical Trials 2021·2022 | 사용 가능 | 환자별 trial 검색·순위와 trial 단위 적격/제외 평가 | 해당 연도 corpus와 qrels를 함께 써야 하며 조건별·질문 정답은 없음 |
| Masked incomplete-information set | 직접 생성 가능 | 부족정보 탐지, 질문, 답변 후 재판정 평가 | 기존 공개 데이터셋이 아니므로 생성 규칙과 수동 검증이 필요 |
| Synthea | FHIR R4 등 생성 가능 | EHR 조회 경로를 보여주는 선택적 데모 | trial별 바이오마커를 항상 포함하지 않아 핵심 정답지로 쓰지 않음 |

### TrialGPT 라벨 변환

TrialGPT의 공개 criterion 데이터는 inclusion과 exclusion에 서로 다른 문구를
사용하므로 ClarifyTrial 상태로 명시적으로 변환한다.

| TrialGPT | ClarifyTrial |
|---|---|
| inclusion `included` | `met` |
| inclusion `not included` | `unmet` |
| exclusion `excluded` | `met` |
| exclusion `not excluded` | `unmet` |
| `not enough information` | `unknown` |
| `not applicable` | `not_applicable` |

`conflict`는 TrialGPT에 없으므로 별도의 합성·수동 검토 세트에서만 평가한다.
TrialGPT의 `expert_eligibility`와 `expert_sentences`를 정답으로 사용하고,
`gpt4_eligibility`나 `gpt4_explanation`을 정답으로 사용하지 않는다.

### 불완전정보 세트 생성 원칙

1. `expert_sentences`가 비어 있지 않고 명시적 근거가 있는 행만 고른다.
2. 원본 note에서 해당 근거 문장을 가리고 원본은 숨은 전체 환자 상태로 보관한다.
3. 가린 문장에서 `missing_variable`, 숨은 답, 영향받는 criterion을 기록한다.
4. 질문 에이전트와 독립된 답변기가 숨은 답만 반환하게 한다.
5. 남은 문장만으로도 답을 추론할 수 없는지 표본을 수동 검토한다.
6. 같은 환자 note가 양쪽에 들어가지 않도록 `patient_id` 단위로 나눈다.
7. 자동 생성 개발 세트와 최종 비공개 평가 세트를 분리한다.

단순히 문장을 지웠다는 이유만으로 최초 정답을 무조건 `unknown`으로 두지 않는다.
나머지 문맥에서 추론 가능한 경우가 있기 때문이다.

## MVP 범위

### 반드시 보여줄 기능

- ClinicalTrials.gov에서 수집하고 query·data timestamp·NCT ID·원문 URL·hash를
  기록한 실행별 trial cache
- 환자 임상요약 정규화와 후보 trial 검색
- criterion별 `met / unmet / unknown / conflict / not_applicable` 및 근거
- 여러 trial의 missing variable 통합과 다음 정보 획득 행동 선택
- 최대 3회 질문, 답변 반영, 표적 재평가
- `likely_eligible / likely_ineligible / uncertain / needs_human_review`
- 추천 순위, criterion 근거, 남은 불확실성, 질문·답변 이력, 면책 고지
- 모든 LLM 요청의 모델·토큰·지연·비용·오류 기록

첫 연구용 규모는 TrialGPT criterion 1,015개 전체를 정적 매칭 평가에 사용하고,
그중 명시적 근거가 있는 50~100개 masked criterion 사례로 상호작용 평가를
시작한다. 발표 데모는 서로 다른 결과를 보여주는 대표 환자 3명으로 고정한다.

### 선택 기능

- 실행 시점의 ClinicalTrials.gov 실시간 새로고침
- Synthea FHIR에서 `Condition`, `Observation`, `Medication` 조회
- 긴 clinical note를 위한 RAG
- 웹 UI와 실제 병원 시스템 연결

Synthea/FHIR은 핵심 MVP를 통과한 뒤 붙인다. FHIR 연결 자체보다 질문 선택과
재판정 효과를 먼저 입증한다.

## 비교 실험

세 방식은 같은 환자, 후보 trial, criterion matcher와 결정 규칙을 사용한다.
질문 정책만 다르게 해야 공정한 비교가 된다.

| 방식 | 정보 획득 정책 |
|---|---|
| Fixed-input matching | 추가 질문 없이 최초 정보만 사용 |
| Ask-all | 발견한 missing variable을 모두 확인하고 부담·비용의 상한을 측정 |
| ClarifyTrial | 최대 3회 안에서 예상 효과가 큰 정보만 확인하고 표적 재평가 |

## 평가 지표

| 평가 대상 | 주 지표 | 의미 |
|---|---|---|
| criterion 판정 | macro-F1, accuracy | 소수 라벨을 포함해 조건 상태를 맞혔는가 |
| 근거 문장 | sentence precision/recall/F1 | 실제 근거 위치를 찾았는가 |
| missing variable | Recall within budget, precision | 제한된 행동 안에서 필요한 정보를 찾았는가 |
| trial 순위 | nDCG@10, Recall@10 | eligible trial을 앞쪽에 배치했는가 |
| 질문 정책 | 평균 행동 수, 행동당 unknown 감소 | 적은 질문으로 불확실성을 줄였는가 |
| 답변 효과 | 정답 방향 추천 변화율 | 답변 후 판단이 올바른 방향으로 바뀌었는가 |
| 운영성 | 호출 수, 입력·출력 토큰, 비용, 지연, 실패율 | 실제 실행 부담이 어느 정도인가 |

TREC의 미판정 trial을 자동으로 부적격 정답으로 바꾸지 않는다. 공식 qrels가
있는 patient-trial pair만 적격/제외 평가에 사용하고, 전체 목록은 검색 순위
평가 규칙에 맞게 처리한다. 현재 API record가 아니라 qrels와 같은 연도의
TREC corpus를 사용한다.

## API와 USD 70 계획

2026-07-11 기준 Solar Pro 3의 공개 표준 가격은 입력 $0.15/백만 토큰,
캐시 입력 $0.015/백만 토큰, 출력 $0.60/백만 토큰이며 VAT 별도다. 계정별
무료 크레딧은 예산 계산에서 가정하지 않고 실제 청구 사용량을 별도로 기록한다.

환자 한 명, 후보 trial 3~5개 기준 목표 호출 수는 다음과 같다.

- 환자 정보 정규화 1회
- criterion 매칭 trial별 batch 3~5회
- 답변 정규화와 표적 재평가 0~3회
- 최종 설명 1회
- 합계 약 5~10회, criteria parsing은 trial별 최초 1회만 수행 후 캐시

검색, 중복 제거, 우선순위 점수, 상태 갱신, 최종 규칙과 평가는 로컬 코드로
실행한다. 실제 호출 전 `RequestLog`에 입력·캐시·출력 토큰과 추정 비용 필드를
추가해야 한다.

| 용도 | 상한 |
|---|---:|
| 연결·데이터 adapter smoke test | $7 |
| 프롬프트와 출력 검증 개발 | $14 |
| 세 baseline 및 ablation 반복 | $28 |
| 고정 holdout 최종 실행 | $14 |
| 실패 재시도·가격 변동 예비비 | $7 |

## 선행연구와 차별점

- [TrialGPT](https://www.nature.com/articles/s41467-024-53081-z)는 검색,
  criterion 매칭과 trial 순위를 강하게 다루지만 고정 환자 note를 입력받는
  정적 흐름이다.
- [MediQ](https://arxiv.org/abs/2406.00922)는 불완전한 임상정보에서 질문하는
  능력을 평가하지만 임상시험 criterion과 여러 trial의 공통 상태를 다루지 않는다.
- [FollowupQ](https://arxiv.org/abs/2503.17509)는 의료 메시지와 EHR에서 후속질문을
  생성하지만 임상시험 추천 변화와 표적 재평가가 목적은 아니다.

ClarifyTrial의 연구 초점은 **여러 trial에 공통인 부족정보를 합치고, 제한된
행동 예산에서 다음 확인 대상을 선택하며, 얻은 답과 연결된 criterion만 다시
평가하는 것**이다. 다만 “최초”라고 주장하지 않고 정식 선행연구 검토 후
차별점의 범위를 확정한다.

## 현재 실제로 구현된 것

| 영역 | 상태 |
|---|---|
| Pydantic 공유 상태와 데이터 계약 | 구현됨 |
| 선정·제외 효과와 추천 우선순위 규칙 | 구현됨 |
| 공통 부족정보 중복 제거 | 구현됨 |
| 휴리스틱 기준 파싱·환자 추출·기준 매칭 | 데모 수준 구현 |
| 질문 큐 생성과 1단계 답변 정규화 | 데모 수준 구현 |
| 합성 데이터와 오프라인 데모 | 구현됨 |
| 테스트 102개 | 통과 |
| 외부 데이터 adapter와 평가 harness | 계획 검증 완료, 미구현 |
| Solar/LLM 연결 | 방향만 정의 |
| LangGraph 상태 저장·중단·재개 | 방향만 정의 |
| Synthea/FHIR 조회 | 선택 기능, 미구현 |
| 임상 성능 검증 | 수행하지 않음 |

## 수정된 구현 순서

### 1단계: 데이터 계약과 고정 평가 세트

- ClinicalTrials.gov run-cache adapter와 provenance manifest를 만든다. raw
  registry 전체 사본은 Git에 올리지 않는다.
- TrialGPT 1,015개 criterion loader와 라벨 변환기를 만든다.
- TREC qrels loader를 추가하고 criterion 평가와 ranking 평가를 분리한다.
- explicit-evidence 행으로 masked set 50~100개를 생성·검토한다.

### 2단계: 결정론적 전체 실행과 baseline

- Eligibility State Tracker의 상태 변경 함수를 완성한다.
- Fixed-input, Ask-all, ClarifyTrial을 같은 입력과 matcher로 실행한다.
- 질문 없이도 모든 정답과 비용을 재현할 수 있는 offline harness를 먼저 만든다.

### 3단계: 실제 LLM을 한 단계씩 교체

- 공통 Solar client, 스키마 검증, 재시도와 토큰·비용 로그를 추가한다.
- 환자 추출, criteria parsing, criterion matching, 질문 표현, 설명 순으로 교체한다.
- criteria와 trial parsing 결과는 캐시하고, 매칭은 trial 단위 batch로 호출한다.

### 4단계: 상호작용과 표적 재평가

- LangGraph는 검증된 상태 변경 함수를 감싸는 실행 껍질로 사용한다.
- 질문이 필요하면 중단하고 답변 뒤 같은 `PatientSession`에서 재개한다.
- 질문기와 숨은 답변기를 분리하고 영향받는 criterion만 다시 평가한다.

### 5단계: 선택적 EHR 정보 획득

- 핵심 비교 실험이 통과한 뒤 Synthea FHIR adapter를 붙인다.
- missing variable별로 EHR 조회, 직접 질문, 전문가 검토 중 행동을 선택한다.
- Synthea는 경로 선택 데모로 사용하고 임상 정확도 정답으로 사용하지 않는다.

### 6단계: 고정 holdout 최종 평가

- 세 baseline의 criterion, missing-info, ranking, 질문 수와 비용을 비교한다.
- 공개 개발 세트와 비공개 holdout을 `patient_id` 단위로 분리한다.
- API 실패, fallback, 지연과 실제 토큰을 결과와 함께 기록한다.
- 결과물에는 근거, 불확실성, 질문 이력과 의료적 면책 고지를 포함한다.

## 이 프로젝트에서 주장하지 않는 것

- 현재 휴리스틱이 임상적으로 정확하다고 주장하지 않는다.
- 합성·파생 데이터 점수를 실제 환자 성능으로 해석하지 않는다.
- TREC를 criterion 또는 질문 정답지라고 부르지 않는다.
- Synthea가 모든 trial-specific 임상 변수를 제공한다고 주장하지 않는다.
- 에이전트 수나 LangGraph 도입 자체를 연구 기여로 주장하지 않는다.

연구의 핵심은 **가치가 높은 정보를 적은 행동으로 얻어, 환자별 임상시험
추천을 근거와 함께 안전하게 갱신할 수 있는가**이다.
