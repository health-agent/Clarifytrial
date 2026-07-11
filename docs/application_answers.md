# ClarifyTrial 지원서 제출용 문안

## 1. 연구 목표 및 접근 방법

1. ClarifyTrial은 임상시험 코디네이터와 임상의를 위한 대화형 임상시험 추천 멀티에이전트 시스템이다.
2. Criteria & Patient Understanding Agent가 ClinicalTrials.gov 기준과 환자 임상요약을 표준 변수로 구조화한다.
3. Candidate Trial Retrieval Module이 질환, 바이오마커, 치료 이력, 연령과 지역을 이용해 후보 trial 3~5개를 찾는다.
4. Criterion Matching Agent가 각 선정·제외 기준을 근거와 함께 `met / unmet / unknown / conflict`로 판정한다.
5. Missing Information Controller가 여러 trial의 공통 부족정보를 합치고 판정 영향도와 확인 비용을 계산한다.
6. Next-Best-Action Planner가 최대 3회 안에서 추천을 가장 크게 개선할 질문 또는 정보 조회를 선택한다.
7. Information Acquisition & Re-evaluation Agent가 답변을 반영하고 관련 criterion만 다시 평가한다.
8. Recommendation & Explanation Agent가 추천 순위, 기준별 근거, 남은 불확실성과 설명을 만들며 전체 흐름은 `PatientSession` 기반 deterministic controller가 관리한다.

## 2. 예상 API 사용량 및 USD 70 계획

- **실험 규모:** 합성 평가 세션 100개, 환자당 후보 trial 3~5개, 정보 획득 최대 3회.
- **예상 요청 수:** 환자 정규화 100회 + trial batch matching 400회 + 질문 답변·표적 재평가 200회 + 최종 설명 100회로 전체 1회 실행 약 800회.
- **개발 전체 상한:** 프롬프트 개발, 세 baseline과 최종 평가를 포함해 약 8,000요청.
- **모델 사용 비중:** 유료 LLM 호출은 Solar Pro 3로 통일하고 matching 50%, parsing·patient extraction 20%, 질문·재평가 20%, 설명 10%로 배분.
- **로컬 처리:** 후보 검색, missing-variable 중복 제거, 우선순위, 상태 갱신, 추천 규칙과 지표 계산은 Python으로 처리해 API 호출을 줄임.
- **캐싱:** trial criteria와 공통 system prompt를 revision별로 저장하고, 세 baseline은 동일한 최초 매칭 결과를 재사용.
- **가상데이터 활용:** 합성 환자, TrialGPT 기반 masked note와 독립 hidden answer를 반복 실험에 사용해 실제 환자정보 없이 질문 전후 변화를 평가.
- **USD 70 배분:** 연결·adapter $7, 프롬프트·validator $14, baseline·ablation $28, 고정 holdout $14, 재시도·예비비 $7.

## 3. 기대 산출물

- 합성 환자 100세션을 처리하는 멀티에이전트 CLI
- criterion별 판정과 근거, 부족정보 질문, 답변 반영과 표적 재평가 이력
- 환자별 임상시험 추천 순위와 설명
- Fixed-input, Ask-all, ClarifyTrial의 정확도·질문 수·비용 비교표
- 대표 환자 3명의 최종 데모와 재현 가능한 결과 JSON/Markdown
- 데이터 출처와 의료적 면책 고지가 포함된 프로젝트 문서

## 4. 현재 준비 상태

공유 상태, 결정 규칙, agent contract, 질문 중복 제거, 합성 데이터, 오프라인
데모와 102개 테스트가 준비되어 있다. 다음 구현은 공개 데이터 adapter, 세
baseline 실행기, Solar batch matching과 masked 질문 평가 순서로 진행한다.
