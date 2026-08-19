# ClarifyTrial 공통 RAG·평가 틀 구현계획

문서 역할: 에이전트 설계를 실제 코드와 예비 성능 실험으로 옮기는 상세 구현 기준  
작성일: 2026-08-20  
현재 상태: 구현 전, v5 성능 미측정

관련 문서:

- 연구 질문과 실험 원칙: [CLARIFYTRIAL_RESEARCH_PLAN_V5.md](CLARIFYTRIAL_RESEARCH_PLAN_V5.md)
- 전체 에이전트 구조: [CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md](CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md)
- 데이터셋 검토: [CLARIFYTRIAL_DATASETS.md](CLARIFYTRIAL_DATASETS.md)
- 원 논문과 코드: [CLARIFYTRIAL_AGENT_SOURCE_INDEX.md](CLARIFYTRIAL_AGENT_SOURCE_INDEX.md)

이 문서는 새 연구 계획이 아니다. 이미 정한 구조를 어떤 자료, 공통 검색기와
평가 프로그램으로 구현할지 정한다.

## 1. 먼저 정한 결론

### 모든 임상시험 문서를 처음부터 수집하지 않는다

자료를 두 층으로 나눈다.

```text
넓은 후보 검색
→ 많은 시험의 제목·질환·요약·참가 조건

정확한 조건 판단
→ 예비실험에 쓰는 6~10개 시험의 상세 조건과 공개 프로토콜
```

첫 구현은 다음 자료를 사용한다.

1. 저장소에 이미 있는 SIGIR 임상시험 자료로 검색 프로그램을 먼저 작동시킨다.
2. TrialGPT가 공개한 TREC 2021·2022 임상시험 JSONL을 내려받는다.
3. TREC 2021에서 검색 설정을 정하고 TREC 2022에서 성능을 확인한다.
4. v5 짝 사례에는 ClinicalTrials.gov에서 고른 6~10개 시험만 상세하게 준비한다.
5. 중앙검사, 검사 유효기간과 공식 확인 절차가 필요한 시험은 공개 프로토콜까지
   추가한다.

웹 화면을 하나씩 긁지 않는다. 공개 JSONL, 공식 API와 공개 프로토콜 문서를
사용한다.

### 논문 성능표를 직접 비교하지 않는다

논문마다 모델, 자료, 검색 범위와 비용이 다르다. 비교를 세 종류로 분리한다.

| 구분 | 하는 일 | 결과 용도 |
|---|---|---|
| 공식 코드 원형 실행 | 저자 코드를 가능한 범위에서 그대로 실행 | 구현 이해와 작동 확인 |
| 논문 기반 재구현 | 공개된 구조만 같은 입력 형식으로 다시 구현 | 구조 참고 |
| 공통 환경 비교 | 같은 모델·자료·검색 결과·행동 한도로 실행 | 주 성능 비교 |

ClarifyTrial이 더 낫다는 판단은 공통 환경 비교에서만 한다.

## 2. 자료 확보 계획

### 2.1 지금 저장소에 있는 자료

`.research-cache/TrialGPT/`에 다음 자료가 있다.

- TrialGPT 공식 코드
- SIGIR 임상시험 말뭉치
- TREC 2021·2022 환자 주제
- TREC 2021·2022 관련성 정답
- TrialGPT가 미리 만든 검색어와 후보 시험 목록

현재 없는 큰 파일:

- TrialGPT `trial_info.json`
- TREC 2021 전체 임상시험 `corpus.jsonl`
- TREC 2022 전체 임상시험 `corpus.jsonl`

### 2.2 넓은 검색 자료

공식 위치:

- [TrialGPT 공식 저장소](https://github.com/ncbi-nlp/TrialGPT)
- [TREC Clinical Trials 2021](https://trec.nist.gov/data/trials2021.html)
- [TREC Clinical Trials 2022](https://trec.nist.gov/data/trials2022.html)

역할:

- TREC 2021: 검색 방식과 상위 개수 조정
- TREC 2022: 조정이 끝난 검색기의 평가
- TrialGPT 공개 임상시험 JSONL: 공통 검색 색인 입력

TREC 정답의 의미는 다음과 같다.

- `0`: 관련 없음
- `1`: 질환에는 관련되지만 해당 환자는 제외
- `2`: 해당 환자에게 참가 가능한 시험

### 2.3 v5 상세 자료

공식 위치:

- [ClinicalTrials.gov API](https://clinicaltrials.gov/data-api/api)

대상:

- 실제 프로토콜 기반 기준 묶음 12~20개
- 임상시험 6~10개
- 기준 묶음마다 날짜·출처·확인 절차 또는 선별 단계만 바꾼 2~4개 변형

각 시험에서 저장할 내용:

- NCT ID와 시험 제목
- 모집 상태와 수집일
- 선정·제외 조건 원문
- 조건 원문 위치
- 공개 프로토콜 문서와 해당 쪽 또는 절
- 자료 최신성, 검사 기관 또는 별도 확인 요구

실제 파일을 추가할 때는 저장소의 데이터 출처 문서에 이용 조건, 수집일과 공식
주소를 함께 기록한다.

## 3. 공통 RAG의 범위

### 3.1 첫 버전에 포함하는 검색

1. 환자에게 관련 있는 시험 찾기
2. 해당 시험에서 판단에 필요한 조건 원문 찾기

### 3.2 첫 버전에 넣지 않는 검색

- 인터넷 실시간 검색
- 일반 의료지식 웹 검색
- 환자에게 없는 사실을 외부 자료로 추정하는 검색
- 합성 환자의 숨은 정답 검색

환자 기록 확인은 RAG가 아니라 별도의 `LOOKUP_RECORD` 도구로 처리한다.

### 3.3 문서 단위

임의의 글자 수로 조건을 자르지 않는다. 한 조각은 원칙적으로 조건 하나다.

```text
trial_id
protocol_version
criterion_id
criterion_type: inclusion 또는 exclusion
raw_text
section_name
source_location
condition
intervention
recruitment_status
```

긴 조건이 여러 절로 이루어졌을 때만 하위 절을 나누고, 원래 조건 ID와 연결한다.

### 3.4 검색 순서

첫 공통 검색기는 TrialGPT 공개 구조를 기준으로 시작한다.

```text
모집 상태·명확한 나이·성별·지역 확인
        ↓
BM25 글자 검색
        +
MedCPT 의미 검색
        ↓
두 순위 결합
        ↓
후보 시험
        ↓
후보 안에서 관련 조건 재정렬
```

첫 순위 결합 설정은 TrialGPT 공개 기본값인 양쪽 가중치 `1`, 결합 상수 `20`을
사용한다. 이 값은 TREC 2021에서만 조정할 수 있고 TREC 2022 결과를 본 뒤 바꾸지
않는다.

### 3.5 공통 검색 결과

에이전트 구조 비교에서는 검색 결과를 매번 새로 만들지 않는다. 사례별로 다음
결과를 저장해 모든 시스템에 똑같이 제공한다.

```text
case_id
query
rank
trial_id
retrieval_score
criterion_id
criterion_rank
raw_text
source_location
```

따라서 다음 두 오류를 따로 볼 수 있다.

- 검색기가 필요한 시험을 찾지 못함
- 시험은 찾았지만 에이전트가 조건을 잘못 판단함

## 4. 공통 평가 틀

### 4.1 세 가지 시험을 분리한다

| 시험 | 자료 | 평가 대상 |
|---|---|---|
| 조건 판정 | TrialGPT 1,015건 | 조건 상태와 환자 근거 선택 |
| 후보 검색 | TREC 2021·2022 | 관련 시험 회수와 순위 |
| 대화·확인 행동 | v5 합성 짝 사례 | 후보 유지, 현재 확정, 다음 확인과 재판정 |

한 점수로 합치지 않는다.

### 4.2 공통 입력

각 사례는 다음 정보를 가진다.

- 사례 ID와 판단 기준일
- 현재 선별 단계
- 현재 공개된 환자 사실
- 사실별 출처, 사건 날짜, 기록 날짜와 원문 위치
- 후보 시험과 구조화 조건
- 지금까지 한 행동과 새로 공개된 정보
- 남은 행동 수

숨은 전체 환자 상태와 정답은 시스템 입력에서 분리한다.

### 4.3 공통 출력

모든 시스템의 결과는 다음 형식으로 바꾼다.

```text
조건별 상태와 환자·시험 근거 ID
후보 유지: retain / remove / uncertain
현재 확정: confirmed / not_confirmed / ineligible / uncertain
다음 행동: LOOKUP_RECORD / ASK_PATIENT / REQUEST_VERIFICATION / DEFER / NONE
확인하려는 사실 ID와 조건 ID
종료 이유
```

논문 시스템에 없는 출력은 억지로 그 시스템의 원래 프롬프트에 추가하지 않는다.
원래 결과를 고정 규칙으로 공통 형식에 옮기고, 옮길 수 없는 항목은 지원하지 않는
항목으로 기록한다.

### 4.4 합성 답변 환경

시스템이 자연어 답을 만들어 스스로 사용하는 것을 금지한다.

- `LOOKUP_RECORD`: 미리 만든 합성 기록에서 해당 사실 반환
- `ASK_PATIENT`: 환자가 직접 알 수 있는 숨은 사실 카드에서 반환
- `REQUEST_VERIFICATION`: 미리 정한 공식 결과와 이용 가능 시점 반환
- `DEFER`: 현재 상태로 종료
- `NONE`: 추가 확인 없이 종료

주 평가에서는 시스템이 자연어 질문만 출력하지 않고 `확인할 사실 ID`를 함께
선택하게 한다. 질문 표현 품질은 별도 보조 평가로 둔다.

### 4.5 첫 실행 한도

- 같은 기반 모델과 모델 버전
- 온도 0
- 시스템 전체 모델 호출 최대 8회
- 외부 확인 행동 최대 3회
- 형식 오류 수정 최대 1회

행동 한도에 도달해도 적합 또는 부적합을 강제로 고르지 않는다. 정보가 부족하면
`not_confirmed`, `uncertain` 또는 `DEFER`로 끝낸다.

### 4.6 모델 실패 처리

JSON 형식 오류, 시간 초과와 빈 결과는 별도 오류로 기록한다. 평가 정답이나 내부
규칙 기준선으로 모델 답을 채우지 않는다. 형식 검사는 의미를 바꾸지 않고 JSON만
한 번 수정할 수 있다.

## 5. 비교 시스템의 구현 수준

### 첫 러프 비교에 넣을 시스템

1. 강한 단일 모델 한 번 판정
2. 모든 부족 정보를 순서대로 확인하는 단순 정책
3. TrialGPT 구조의 조건별 판정
4. CLEAR-MATCH식 세 단계 대화와 후보 갱신
5. DQueST식 영향 후보 수 우선 정책
6. Fink식 영향 범위와 부담 우선 정책
7. MediQ식 정보 충분성 판단
8. ClarifyTrial v5 전체 구조

### 나중에 연결할 시스템

- TrialMatchAI 공식 실행: GPU와 큰 저장공간이 필요하므로 공통 틀 뒤에 연결
- EXACT 공식 실행: 외부 시험 데이터베이스와 규칙 엔진 연결 뒤 비교
- PRomop: 전체 시스템 비교가 아니라 환자 상태표 사용 전후 비교
- TRIAGE: 전체 시스템이 아니라 근거 검사 사용 전후 비교
- Yang: 공개 구현 세부가 부족하여 실행 기준선이 아니라 구조 비교 자료

공식 코드가 없는 연구에는 반드시 `재구현` 또는 `식 정책`이라고 표시한다.

## 6. 코드 경계

과거 Solar 경로와 기존 데모 코드를 직접 늘리지 않는다. 논문용 구현은 별도
`Clarifytrial/` 영역에 둔다.

```text
Clarifytrial/
  src/clarifytrial/
    contracts.py
    data/
      trial_corpus.py
      eval_cases.py
    retrieval/
      criterion_store.py
      bm25_retriever.py
      dense_retriever.py
      rank_fusion.py
      criterion_reranker.py
    environment/
      hidden_patient.py
      tools.py
      episode.py
    systems/
      base.py
      single_llm.py
      trialgpt_controlled.py
      clear_match_reimplementation.py
      dquest_policy.py
      fink_policy.py
      clarifytrial_v5.py
    evaluation/
      runner.py
      retrieval_metrics.py
      criterion_metrics.py
      episode_metrics.py
      trace.py
  baselines/
    official/
    reimplemented/
  tests/
```

공식 코드는 프로젝트 본문에 복사해 섞지 않는다. 별도 환경에서 실행하고 JSONL
입출력 변환기만 연결한다.

## 7. 구현 순서

### 1단계: 작은 공통 RAG

- 시험·조건·원문 위치의 최소 자료형
- 현재 있는 SIGIR 임상시험 자료 읽기
- 조건 단위 문서 저장소
- BM25 검색
- 검색 결과에 시험·조건·원문 위치 표시

### 2단계: TREC 검색 평가

- TREC 2021·2022 전체 임상시험 JSONL 확보
- MedCPT 의미 검색
- BM25와 의미 검색 순위 결합
- TREC 2021에서 설정 결정
- TREC 2022에서 Recall과 nDCG 측정

### 3단계: 공통 평가 계약과 작은 사례

- 공통 입력·출력 자료형
- 숨은 환자 상태와 행동 결과
- 결정 규칙만 있는 3~5개 작은 사례
- 가짜 시스템으로 실행과 채점 확인

### 4단계: 고정 후보의 조건 판정

- TrialGPT 1,015건 변환기
- 단일 모델과 TrialGPT 구조 비교
- 근거 ID와 조건 상태 평가

### 5단계: 대화와 행동 정책

- 공통 합성 답변 환경
- 모든 정보 확인, DQueST식, Fink식과 MediQ식 정책
- 같은 행동 결과로 후보 갱신

### 6단계: ClarifyTrial v5

- 날짜·출처가 있는 환자 상태표
- 후보 유지와 현재 확정 분리
- 확인 경로 선택
- 관련 조건만 재판정
- 한 번의 근거 검사

### 7단계: 무거운 공식 시스템 연결

- TrialMatchAI
- EXACT
- 필요한 공식 코드 원형 실행

## 8. 첫 RAG 구현 완료 기준

다음이 되면 공통 RAG 첫 단위를 완료한 것으로 본다.

- SIGIR 임상시험 말뭉치를 읽을 수 있음
- 시험과 조건에 변하지 않는 ID가 붙음
- 환자 질의로 상위 시험과 관련 조건을 찾을 수 있음
- 결과에 원문과 출처 위치가 포함됨
- 같은 입력에서 같은 결과를 반환함
- 합성 환자의 숨은 사실이 검색 색인에 들어가지 않음
- 검색 실패와 조건 판정 실패를 따로 기록할 수 있음
- 작은 프로그램 검사와 단위 테스트가 통과함

이 단계에서는 최종 성능을 주장하지 않는다. TREC 전체 평가와 v5 조건 판정은
다음 단계다.

## 9. 아직 정하지 않아도 되는 것

- 최종 유료 모델 제공자
- TrialMatchAI용 GPU 환경
- 전체 ClinicalTrials.gov 수집
- 일반 의료지식 RAG
- 여러 모델의 토론 구조
- 실제 임상 사용 방식

공통 계약과 RAG가 모델 제공자와 분리되어 있으므로 위 항목은 첫 구현을 막지 않는다.

## 10. 안전과 주장 범위

- 실제 환자 자료를 사용하지 않는다.
- 합성 환자의 숨은 상태를 시스템 입력이나 RAG에 넣지 않는다.
- 환자 질문으로 확인할 수 없는 검사 결과를 질문으로 해결하지 않는다.
- 검색된 시험 조건은 추천 근거이지 실제 등록 결정이 아니다.
- v5 구조는 아직 실행하지 않았으며 현재 성능은 미측정이다.
- 과거 Solar 합성 데모 수치, 특히 84%를 현재 기준선으로 사용하지 않는다.
