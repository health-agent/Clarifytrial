# ClarifyTrial 지원서 제출 양식

## 프로젝트명

**ClarifyTrial: 부족정보 확인과 표적 재평가를 포함한 임상시험 추천 멀티에이전트 시스템**

## 한 문장 소개

임상시험 코디네이터와 임상의를 위해 환자 정보와 임상시험 기준을 근거와 함께
매칭하고, 부족한 정보를 확인한 뒤 환자별 임상시험 추천 순위와 설명을 제공한다.

## 연구 목표 및 접근 방법

1. Criteria & Patient Understanding Agent가 EHR 요약, clinical note 또는 의료진이 제공한 환자 정보를 표준 patient variable로 정규화한다.
2. Candidate Trial Retrieval Module이 질환, 바이오마커, 치료 이력, 연령과 지역을 이용해 ClinicalTrials.gov에서 관련 임상시험을 검색한다.
3. Criterion Matching Agent가 후보 임상시험의 선정·제외 기준을 근거와 함께 met, unmet, unknown, conflict로 판정한다.
4. Missing Information Controller가 unknown의 원인이 되는 missing variable을 통합하고 판정 영향도, 확인 경로, 사용자 부담과 확인 난이도를 기준으로 우선순위를 정한다.
5. Next-Best-Action Planner가 structured EHR 조회, clinical note 검색, 직접 질문 또는 전문가 확인 중 다음 정보 획득 행동을 선택한다.
6. Information Acquisition & Targeted Re-evaluation Agent가 새 정보를 PatientProfile에 반영하고 관련 criterion만 다시 평가한다.
7. Recommendation & Explanation Agent가 likely eligible, likely ineligible, uncertain, needs human review 판정과 추천 순위, criterion 근거와 남은 불확실성을 설명한다.
8. 전체 과정은 PatientSession 공유 상태 기반의 결정론적 controller가 관리하며, LLM은 criteria parsing, 자유답변 정규화, criterion matching, 질문과 설명 생성에 사용한다.

## 데이터 구성

| 데이터 | 사용 목적 |
|---|---|
| ClinicalTrials.gov | 실제 모집 임상시험과 inclusion/exclusion criteria 입력 |
| TrialGPT Criterion Annotations | criterion별 판정과 근거 평가 |
| Masked incomplete-information set | missing variable 탐지, 질문, 답변 반영과 재평가 평가 |
| TREC Clinical Trials 2021·2022 | 후보 검색과 최종 추천 순위 평가 |
| Synthea | 선택적 합성 FHIR 입력과 구조화 EHR 조회 시연 |

TrialGPT 환자 사례에서 특정 정보를 가린 masked set을 직접 만든다. 가린 정보를
환자 답변으로 사용해 unknown 탐지, 질문 생성, 답변 반영과 재평가를 연속해서
평가한다. TrialGPT는 criterion 단위 정답, TREC는 trial 단위 검색·순위
정답으로 사용한다.

## Baseline 및 평가 방법

| 비교 방식 | 내용 |
|---|---|
| Single-shot LLM matching | 추가 질문 없이 현재 환자 정보만으로 바로 추천 |
| Ask-all | 탐지한 모든 missing variable을 확인한 뒤 재평가 |
| ClarifyTrial | 영향이 큰 missing variable부터 확인하고 관련 criterion만 재평가 |

criterion 판정은 macro-F1 또는 accuracy, missing variable과 후보 검색은
Recall@k, 최종 추천 순위는 nDCG@k로 평가한다. 평균 질문·정보 획득 행동 수,
unknown 해소율, 세션당 API 호출 수·토큰·비용과 응답 시간도 함께 측정한다.

질문 횟수는 미리 제한하지 않는다. 각 정보 획득 뒤의 정확도와 추천 순위를
기록해 추가 질문의 성능 향상이 작아지는 지점을 찾고, 이를 적절한 종료 조건의
근거로 사용한다.

## 기대 산출물과 MVP 범위

### 필수 산출물

- 환자 정보와 실제 형식의 임상시험 criteria 입력
- criterion별 판정과 근거
- 부족한 정보 목록과 질문 또는 정보 조회 행동
- 답변 반영 전후의 targeted re-evaluation 결과
- 환자별 임상시험 추천 순위와 설명
- Single-shot, Ask-all, ClarifyTrial 비교 결과
- 질문 횟수에 따른 성능 변화와 API 사용량
- 재현 가능한 실행 코드, 데이터 출처와 의료적 면책 고지

### 최종 데모

환자 입력, 최초 매칭, 부족정보 확인, 질문 또는 EHR 조회, 답변 반영,
criterion 재평가, 최종 추천과 설명까지 하나의 흐름으로 보여준다.

### 선택 기능

Synthea 기반 FHIR 조회는 핵심 매칭·질문·재평가 흐름이 완성된 뒤 연결한다.
missing variable의 종류에 따라 구조화 EHR 조회와 코디네이터 질문 중 적절한
경로를 선택하는 기능을 시연한다.

평가 환자 수와 후보 임상시험 수는 임의의 숫자로 고정하지 않는다. 서로 다른
missing-information 유형을 포함한 환자 사례를 준비하고, 후보 수를 늘려가며
Recall@k와 matching 비용을 비교해 현실적인 실행 범위를 정한다.

## 예상 API 사용량 및 비용 계획

LLM API는 criteria parsing, 환자 정보 추출, criterion matching, 질문 문장,
자유답변 정규화와 최종 설명에 사용한다. 후보 필터링, missing variable 통합,
우선순위 계산, 상태 갱신, 재평가 대상 선택과 지표 계산은 로컬 코드로 처리한다.

요청 수와 비용은 환자 수, 후보 임상시험 수와 정보 획득 행동 수에 따라
달라지므로 고정 예산을 먼저 정하지 않는다. 소규모 실행에서 단계별 호출 수,
입력·출력·캐시 토큰, 세션당 비용과 응답 시간을 측정한 뒤 전체 실험 규모를
결정한다.

trial criteria와 공통 prompt는 캐싱하고, criterion matching은 trial 단위로
묶어 호출한다. 답변 뒤에는 관련 criterion만 다시 평가해 호출량을 줄인다.

## 최종 출력

최종 결과에는 임상시험별 참여 가능성, criterion별 판단 근거, 확인한 질문과
답변, 남은 부족정보, 환자별 추천 순위와 설명, 의료적 면책 고지를 포함한다.
