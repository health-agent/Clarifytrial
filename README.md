# ClarifyTrial

**임상시험 코디네이터와 임상의를 위한 대화형 임상시험 매칭·추천 멀티에이전트 시스템**

![ClarifyTrial 전체 연구 계획](docs/assets/clarifytrial-research-plan.png)

지원서에 바로 사용할 문안은 [APPLICATION_KO.md](APPLICATION_KO.md)에 정리되어
있다. 이 README는 프로젝트의 거시 계획과 상세 구현 계획을 한 문서에 합친
통합 설명서다.

## 한눈에 보기

환자 정보에는 임상시험 참여 가능성을 판단하는 데 필요한 바이오마커, 검사값,
치료 이력 등이 빠져 있을 수 있다. ClarifyTrial은 현재 정보만으로 성급하게
추천을 끝내지 않고 다음 과정을 수행한다.

| 순서 | 처리 | 결과 |
|---|---|---|
| 1 | 환자 정보와 임상시험 기준 구조화 | patient variable과 criterion |
| 2 | 관련 임상시험 검색 | 후보 임상시험 목록 |
| 3 | 기준별 근거 매칭 | met, unmet, unknown, conflict |
| 4 | 부족정보 선택·획득 | EHR 조회, note 검색, 질문 또는 전문가 확인 |
| 5 | 관련 기준 재평가 | 답변이 영향을 주는 criterion만 갱신 |
| 6 | 추천·설명 | 참여 가능성, 추천 순위, 근거와 남은 불확실성 |

핵심은 **모든 부족정보를 무조건 묻는 것이 아니라 추천에 영향이 큰 정보부터
확인하고, 새 정보와 관련된 기준만 다시 판단하는 것**이다.

![ClarifyTrial 에이전트 워크플로](docs/assets/clarifytrial-workflow.png)

## 최종 데모 흐름

1. 합성 환자 임상요약 또는 합성 FHIR 기록을 입력한다.
2. ClinicalTrials.gov에서 관련 임상시험을 찾는다.
3. 각 임상시험의 선정·제외 기준을 근거와 함께 판정한다.
4. 부족한 정보와 다음 질문 또는 조회 행동을 보여준다.
5. 환자 답변이나 EHR 조회 결과를 반영해 관련 기준만 다시 판정한다.
6. 최종 추천 순위, 참여 가능성, 근거, 설명과 의료적 면책 고지를 출력한다.

## 에이전트 구성

### 1. Criteria & Patient Understanding

EHR 요약, clinical note 또는 의료진이 제공한 환자 정보를 표준 patient
variable로 정규화한다. 질환, 바이오마커, 치료 이력, 연령, 지역 등을 추출하고
ClinicalTrials.gov의 inclusion/exclusion criteria를 criterion 단위로 나눈다.

### 2. Candidate Trial Retrieval

환자 변수로 모집 중인 임상시험을 검색하고 관련 후보군을 선별한다.

### 3. Criterion Matching

후보 임상시험의 각 criterion을 환자 근거와 함께 met, unmet, unknown,
conflict로 판정한다.

### 4. Missing Information & Targeted Re-evaluation

unknown의 원인이 되는 missing variable을 추출하고 여러 trial에서 요구하는
같은 정보를 하나로 합친다. 판정에 미치는 영향, 정보 획득 경로, 사용자 부담,
확인 난이도와 판정 변화 가능성을 고려해 다음 행동을 정한다.

정보 획득 경로는 structured EHR 조회, clinical note 검색, 직접 질문 또는
전문가 확인이다. 답변이 들어오면 해당 variable과 연결된 criterion만 다시
평가한다.

### 5. Recommendation & Explanation

criterion별 근거, 남은 불확실성과 질문·답변 이력을 이용해 각 임상시험을
likely eligible, likely ineligible, uncertain, needs human review로 분류한다.
이후 환자별 추천 순위와 설명을 생성한다.

전체 흐름은 PatientSession 공유 상태와 결정론적 controller가 관리한다. LLM은
criteria parsing, 자유답변 정규화, criterion matching, 질문 문장과 설명 생성에
사용하고, 상태 갱신·중복 제거·재평가 대상 선택·최종 규칙은 Python으로 처리한다.

## 데이터와 사용 목적

| 데이터 | 사용 목적 | 검토 결과 |
|---|---|---|
| ClinicalTrials.gov | 실제 모집 임상시험과 inclusion/exclusion criteria 입력 | 시스템 입력으로 적합 |
| TrialGPT Criterion Annotations | criterion별 판정과 근거 평가 | 현재 ClarifyTrial 구조와 가장 직접적으로 맞음 |
| Masked incomplete-information set | missing variable 탐지, 질문, 답변 반영과 재평가 평가 | TrialGPT 환자 사례에서 직접 생성 가능 |
| TREC Clinical Trials 2021·2022 | 후보 검색과 최종 추천 순위 평가 | eligible/excluded qrels로 Recall@k·nDCG@k 평가 가능 |
| Synthea | 합성 FHIR 입력과 EHR 조회 시연 | 선택적 정보 획득 데모에 적합 |

TrialGPT는 criterion 단위 판단 정답으로 사용한다. TREC는 trial 단위 검색과
추천 순위 정답으로 사용한다. Synthea는 EHR 조회 기능을 보여주기 위한 합성
데이터이며 추천 정답지로 사용하지 않는다.

TrialGPT에는 conflict 정답 라벨이 없으므로 conflict 동작은 별도 합성 사례로
확인하고, TrialGPT 성능표에는 포함하지 않는다.

자세한 출처와 라벨 대응은
[docs/internal/DATA_SOURCES.md](docs/internal/DATA_SOURCES.md)에 정리한다.

## Masked incomplete-information set

후속 질문과 재평가를 평가하기 위해 TrialGPT 환자 사례에서 특정 정보를 가린
평가 자료를 만든다.

1. 원본 patient note와 expert criterion 판정을 준비한다.
2. 특정 patient variable에 해당하는 정보를 가린다.
3. 가린 정보를 missing variable과 환자 답변으로 저장한다.
4. 가린 상태에서 unknown 탐지와 질문 생성을 실행한다.
5. 답변을 반영한 뒤 criterion 판정이 어떻게 달라지는지 평가한다.

이 자료로 missing variable 탐지, 질문 선택, 답변 반영과 targeted
re-evaluation을 하나의 흐름으로 검사할 수 있다.

## 비교 실험

| 방식 | 동작 |
|---|---|
| Single-shot LLM matching | 추가 질문 없이 현재 환자 정보만으로 바로 추천 |
| Ask-all | 탐지한 모든 missing variable을 질문하거나 조회한 뒤 재평가 |
| ClarifyTrial | 영향이 큰 missing variable부터 확인하고 관련 criterion만 재평가 |

세 방식은 같은 환자 정보, 후보 임상시험과 criterion을 사용한다. 이를 통해
질문이 없는 일반 매칭, 모든 정보를 확인하는 방식, 우선순위 기반 방식의 차이를
직접 비교한다.

## 질문 횟수 결정

질문 횟수에는 고정 상한을 두지 않는다. 확인할 정보가 남아 있고 추가 정보가
판단을 개선할 가능성이 있는 동안 다음 행동을 선택한다.

실험에서는 정보 획득을 한 번 진행할 때마다 criterion 정확도, unknown 해소율,
추천 순위와 누적 비용을 저장한다. 질문 수가 증가할수록 성능이 얼마나
개선되는지 그래프로 비교하고, 추가 질문의 이득이 작아지는 지점을 찾아 이후
종료 조건으로 사용한다.

## 평가 범위 결정

환자 수와 후보 trial 수는 미리 고정하지 않는다. 환자 사례는 바이오마커,
검사값, 치료 이력, 질환 단계와 지역 등 서로 다른 missing-information 유형을
포함하도록 구성한다. 후보 trial 수는 검색 결과를 늘려가며 Recall@k와 criterion
matching 비용을 함께 측정하고, 관련 trial을 충분히 포함하면서 비용 증가가
과도하지 않은 지점에서 정한다. 전체 평가 규모도 첫 실행 결과가 크게 흔들리지
않는 수준까지 단계적으로 늘린다.

## 평가 지표

| 평가 대상 | 지표 |
|---|---|
| criterion 판정 | macro-F1, accuracy |
| missing variable 탐지·우선순위 | Recall@k |
| 후보 임상시험 검색 | Recall@k |
| 최종 추천 순위 | nDCG@k |
| 질문 효과 | unknown 해소율, 질문 전후 판정 변화 |
| 사용자 부담 | 평균 질문·정보 획득 행동 수 |
| 운영 효율 | 세션당 API 호출 수, 토큰, 비용, 응답 시간 |

unknown 해소율은 criterion 정확도와 함께 확인한다. unknown을 단순히 다른
라벨로 바꾸는 것이 아니라 올바른 판정으로 바뀌었는지를 평가한다.

## 기대 산출물

- 실행 가능한 멀티에이전트 CLI 또는 데모 UI
- criterion별 판정과 근거
- missing variable, 질문·조회 행동과 답변 이력
- 답변 전후 targeted re-evaluation 결과
- 환자별 임상시험 추천 순위와 설명
- Single-shot, Ask-all, ClarifyTrial 비교 리포트
- 질문 횟수에 따른 성능 변화 그래프
- 세션별 API 호출 수·토큰·비용
- 데이터 출처, 재현 방법과 의료적 면책 고지

## 필수 기능과 선택 기능

### 필수

- ClinicalTrials.gov criteria 입력
- TrialGPT criterion matching 평가
- masked incomplete-information 질문·재평가 평가
- Single-shot, Ask-all, ClarifyTrial 비교
- criterion 근거와 최종 추천 설명
- TREC 기반 검색·순위 평가

### 선택

- Synthea FHIR 입력과 구조화 EHR 조회
- clinical note 검색 경로
- LangGraph 기반 중단·재개 실행

## API 사용 계획

### LLM API 사용 단계

- 자유형 criteria parsing
- patient note의 변수 추출
- criterion matching과 근거 생성
- 질문 문장과 자유답변 정규화
- 최종 추천 설명

### 로컬 처리 단계

- 후보 필터링과 캐시
- missing variable 중복 제거와 우선순위 계산
- PatientSession 상태 갱신
- targeted re-evaluation 대상 선택
- 추천 규칙과 평가 지표 계산

호출량이나 예산은 미리 고정하지 않는다. 먼저 소규모 실행에서 환자당 후보
trial 수, 정보 획득 행동 수, 단계별 호출·토큰·비용과 응답 시간을 측정한 뒤
전체 평가 규모를 정한다. trial criteria와 공통 prompt는 캐싱하고, matching은
trial 단위로 묶으며 답변 후에는 관련 criterion만 다시 호출한다.

## 구현 순서

1. ClinicalTrials.gov, TrialGPT와 TREC 데이터 adapter
2. 환자·criteria 구조화와 candidate retrieval
3. criterion matching과 근거 생성
4. missing variable 통합, 질문·조회와 답변 반영
5. targeted re-evaluation과 추천·설명
6. 세 비교 방식과 평가지표 리포트
7. 선택적 Synthea FHIR 조회

## 현재 구현 상태

현재 저장소에는 다음 기반 구현이 있다.

- Pydantic 기반 PatientSession과 criterion 상태 모델
- inclusion/exclusion 판정과 추천 규칙
- missing variable 전역 통합과 질문 생성
- 답변 반영·targeted re-evaluation용 데이터 계약
- 합성 데이터 기반 오프라인 데모
- 자동 테스트와 GitHub CI

공개 데이터 adapter, 실제 LLM 기반 전체 단계와 정식 비교 실험은 다음 구현
대상이다.

## 실행 방법

Python 3.10 이상에서 다음 명령을 실행한다.

    pip install -r requirements.txt
    pytest -q
    python scripts/run_end_to_end_demo.py

추가 오프라인 데모:

    python scripts/run_patient_profile_extraction_demo.py
    python scripts/run_criteria_parser_demo.py
    python scripts/run_criterion_matching_demo.py
    python scripts/run_missing_info_clarification_demo.py
    python scripts/validate_synthetic_data.py

## 문서 구조

- README.md: 프로젝트 통합 설명서
- APPLICATION_KO.md: 지원서 제출 문안
- docs/assets: GitHub에 표시되는 순서도와 편집 가능한 SVG
- docs/internal: 개발·검증·데이터 출처용 내부 문서

## 의료적 면책

모든 환자 예시는 합성 데이터만 사용한다. ClarifyTrial의 결과는 연구 및
사전검토를 위한 보조 정보이며 의료적 자문, 임상시험 적격성 확정 또는 등록
결정을 대체하지 않는다. 최종 판단은 자격을 갖춘 임상 전문가가 수행해야 한다.

전문은
[docs/internal/MEDICAL_DISCLAIMER.md](docs/internal/MEDICAL_DISCLAIMER.md)에
정리한다.
