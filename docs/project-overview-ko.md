# ClarifyTrial 프로젝트 개요

![ClarifyTrial 연구 계획](assets/clarifytrial-research-plan.png)

## 한 문장 목표

임상시험 코디네이터와 임상의가 환자에게 맞는 임상시험을 검토할 때, 모든
부족정보를 묻지 않고 **추천을 가장 크게 개선할 정보부터 확인한 뒤 관련
기준만 다시 판단하는 멀티에이전트 시스템**을 만든다.

## 연구 목표 및 접근 방법

1. Criteria & Patient Understanding Agent가 임상시험 기준과 환자 임상요약을 구조화한다.
2. Candidate Trial Retrieval Module이 ClinicalTrials.gov에서 관련 후보 trial 3~5개를 찾는다.
3. Criterion Matching Agent가 각 선정·제외 기준을 근거와 함께 `met / unmet / unknown / conflict`로 판정한다.
4. Missing Information Controller가 여러 trial의 공통 부족정보를 합치고 중요도를 계산한다.
5. Next-Best-Action Planner가 최대 3회 안에서 추천을 가장 크게 개선할 질문 또는 정보 조회를 선택한다.
6. Information Acquisition & Re-evaluation Agent가 답변을 상태에 반영하고 관련 criterion만 다시 평가한다.
7. Recommendation & Explanation Agent가 추천 순위, 기준별 근거, 남은 불확실성과 설명을 생성한다.
8. 전체 흐름은 `PatientSession` 기반 결정론적 controller가 관리하며 LLM은 문장 이해와 생성에 집중한다.

## 에이전트 구성

현재 10개 agent contract와 후보 검색·Next-Best-Action 모듈을 발표와
제안서에서는 다섯 그룹으로 묶어 설명한다.

| 그룹 | 포함 역할 | 결과 |
|---|---|---|
| 이해·검색 | Criteria Parser, Patient Understanding, Candidate Retrieval | 구조화 환자 변수와 후보 trial |
| 근거 매칭 | Evidence Context, Criterion Matching | criterion 상태와 근거 문장 |
| 부족정보 제어 | Missing Detection, Clarification Question, Next-Best-Action | 중복 제거된 질문·조회 순서 |
| 정보 반영 | Answer Update, Targeted Re-evaluation | 갱신된 환자 상태와 관련 criterion |
| 추천·설명 | Trial Recommendation, Result Explanation | 추천 순위, 근거, 불확실성, 면책 고지 |

Eligibility State Tracker는 모든 그룹이 읽고 쓰는 중앙 `PatientSession`을
관리한다. hard block, 질문 횟수, 사람 검토와 추천 우선순위는 Python 규칙으로
고정한다.

![ClarifyTrial 전체 워크플로](assets/clarifytrial-workflow.png)

## MVP 데모

| 단계 | 보여줄 내용 |
|---|---|
| 입력 | 합성 환자 임상요약과 ClinicalTrials.gov 선정·제외 기준 |
| 최초 판단 | 후보 trial 3~5개와 criterion별 상태·근거 |
| 정보 획득 | 최대 3개의 우선 질문 또는 선택적 EHR 조회 |
| 재평가 | 답변과 연결된 criterion만 갱신 |
| 최종 결과 | 추천 순위, 참여 가능성, 근거, 남은 질문, 설명, 면책 고지 |

평가 세션 100개를 기본 실행 단위로 하고, 발표에서는 결과 변화가 분명한
대표 환자 3명을 보여준다. Synthea FHIR 조회는 핵심 흐름이 완성된 뒤 한 개의
정보 획득 경로로 연결한다.

## 데이터와 정답지

| 데이터 | 사용 목적 |
|---|---|
| ClinicalTrials.gov | 실제 모집 trial과 inclusion/exclusion criteria 입력 |
| TrialGPT Criterion Annotations | criterion 판정과 근거 문장 정답 |
| Masked incomplete-information set | 부족정보 탐지, 질문, 답변 후 재평가 정답 |
| TREC Clinical Trials 2021·2022 | trial 단위 eligible/excluded와 추천 순위 평가 |
| Synthea | 합성 FHIR 기반 EHR 조회 데모 |

세부 라벨 변환, 출처, 버전과 이용조건은 [`DATA_SOURCES.md`](../DATA_SOURCES.md)에
모아 두고 메인 계획에서는 위 역할만 사용한다.

## 비교 실험

같은 환자, 후보 trial, matcher와 결정 규칙을 사용하고 정보 획득 정책만 바꾼다.

| 방식 | 동작 | 비교 목적 |
|---|---|---|
| Fixed-input | 추가 질문 없이 최초 정보만 사용 | 질문 0회의 기준선 |
| Ask-all | 발견한 부족정보를 모두 확인 | 정확도와 사용자 부담의 상한 |
| ClarifyTrial | 최대 3회, 가치가 높은 정보만 확인 | 적은 행동으로 추천 개선 |

주요 평가는 criterion macro-F1/accuracy, missing-variable recall, unknown 해소율,
trial nDCG@10/Recall@10, 평균 질문 수, 호출·토큰·비용·지연이다.

## 예상 API 사용량 및 USD 70 계획

### 요청 수

평가 세션 100개, 환자당 후보 trial 평균 4개, 평균 질문 2회를 기준으로 한다.

| API 단계 | 100세션 요청 수 |
|---|---:|
| 환자 정보 정규화 | 100 |
| trial별 criterion batch matching | 400 |
| 질문 답변 정규화·표적 재평가 | 200 |
| 최종 설명 | 100 |
| 전체 1회 실행 | 약 800 |

프롬프트 개발과 세 비교실험을 포함한 전체 개발 과정은 약 8,000요청을 상한으로
잡는다. criterion을 하나씩 호출하지 않고 trial 단위 batch로 처리한다.

### 모델과 작업 비중

- Solar Pro 3: 유료 LLM 호출의 100%
- criterion matching: 약 50%
- criteria parsing·환자 추출: 약 20%
- 질문·답변 정규화·표적 재평가: 약 20%
- 최종 설명: 약 10%
- 검색, 중복 제거, 우선순위, 상태 갱신, 추천 규칙과 평가는 로컬 Python으로 실행

### 비용 절감

- trial criteria와 공통 system prompt는 trial revision별로 캐싱한다.
- 세 baseline은 동일한 최초 매칭 결과와 hidden answer를 재사용한다.
- 합성 환자와 masked hidden-answer 데이터를 반복 실험에 사용한다.
- 답변 후 전체 trial을 다시 호출하지 않고 관련 criterion만 재호출한다.
- 누락되거나 형식이 깨진 batch만 선택적으로 재시도한다.

### USD 70 배분

| 용도 | 상한 |
|---|---:|
| API 연결·데이터 adapter | $7 |
| 프롬프트·validator 개발 | $14 |
| baseline·ablation 비교 | $28 |
| 고정 holdout 최종 평가 | $14 |
| 재시도·예비비 | $7 |


## 현재와 다음 단계

| 현재 확보 | 바로 다음 구현 |
|---|---|
| 공유 상태, 결정 규칙, 휴리스틱 agent, 합성 데이터, 102개 테스트 | 데이터 adapter와 세 baseline 실행기 |
| 질문 중복 제거, 질문 큐, 답변 정규화 계약 | Solar batch matching과 masked 질문 평가 |
| 워크플로·연구 계획·데이터 출처 문서 | LangGraph 실행과 최종 평가 리포트 |

모든 환자 예시는 합성 데이터이며, 결과에는 `MEDICAL_DISCLAIMER.md`의 의료적
면책 고지를 포함한다.
