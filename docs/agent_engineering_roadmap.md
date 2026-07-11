# ClarifyTrial 6-Week Engineering Roadmap

_2026-07-11 기준. 쉬운 전체 설명은 `docs/project-overview-ko.md`, 데이터 출처와
라벨 규칙은 `DATA_SOURCES.md`를 따른다._

## 목표

합성 환자 100개를 대상으로 후보 임상시험 검색, criterion 판단, 우선 질문,
답변 반영, 표적 재평가와 근거 기반 추천을 한 명령으로 실행하고 세 baseline의
정확도·질문 수·API 비용을 비교한다.

## 전체 흐름

```text
ClinicalTrials.gov + 합성 환자
→ 환자·criteria 구조화
→ 후보 trial 3~5개 검색
→ criterion별 근거 매칭
→ missing variable 통합·우선순위
→ 최대 3회 질문 또는 정보 조회
→ 관련 criterion만 재평가
→ 추천 순위·설명·평가 리포트
```

## 6주 계획

| 주차 | 구현 | 완료 기준 |
|---|---|---|
| 1주차: 데이터 | ClinicalTrials.gov run cache, TrialGPT/TREC loader, 50~100개 masked set | 출처·버전·hash가 있는 동일 입력 재생 |
| 2주차: 기준선 | Eligibility State Tracker, Fixed-input·Ask-all·ClarifyTrial CLI | 같은 후보·matcher에서 세 정책 실행 |
| 3주차: Solar | 공통 client, trial batch matching, schema validator, cache, token·cost log | 환자당 5~10회 호출과 offline fallback |
| 4주차: 질문 | Missing Controller, Next-Best-Action, hidden answer, targeted re-evaluation | 최대 3회 안에 질문 전후 변화 자동 채점 |
| 5주차: 오케스트레이션 | LangGraph interrupt/resume, checkpoint, 선택적 Synthea FHIR route | 같은 `PatientSession`에서 중단·재개 |
| 6주차: 평가 | 지표 집계, baseline·ablation, 대표 환자 3명 데모 | 100세션 결과 JSON·Markdown·비교표 |

## 에이전트 오케스트레이션

| 순서 | Agent group | 책임 |
|---|---|---|
| 1 | Understanding & Retrieval | 환자·criteria 구조화, 후보 trial 검색 |
| 2 | Criterion Matching | criterion 상태와 근거 생성 |
| 3 | Missing Information Controller | 공통 부족정보 통합과 우선순위 계산 |
| 4 | Information Acquisition & Re-evaluation | 질문·EHR 조회, 답변 반영, 관련 criterion 갱신 |
| 5 | Recommendation & Explanation | 추천 순위, 근거, 불확실성, 설명 출력 |

`PatientSession`이 전체 상태를 보관하고 deterministic controller가 질문 횟수,
hard block, 사람 검토와 최종 추천 우선순위를 관리한다.

## 실험 설계

| 실험 | 정보 획득 정책 |
|---|---|
| Fixed-input | 추가 질문 없음 |
| Ask-all | 모든 missing variable 확인 |
| ClarifyTrial | 최대 3회, 예상 효과가 큰 정보부터 확인 |

공통 조건:

- 같은 환자, 후보 trial, criterion matcher와 hidden answer 사용
- TrialGPT expert label로 criterion 판단 평가
- TREC qrels로 trial ranking 평가
- patient_id 단위 development/holdout 분리

핵심 지표:

- criterion macro-F1·accuracy
- missing-variable recall·unknown 해소율
- nDCG@10·Recall@10
- 평균 질문 수·호출 수·토큰·비용·지연

## API 계획

100세션 전체 1회 실행 목표:

| 단계 | 요청 수 |
|---|---:|
| 환자 정규화 | 100 |
| trial batch matching | 400 |
| 질문 답변·표적 재평가 | 200 |
| 최종 설명 | 100 |
| 합계 | 약 800 |

개발·비교실험 전체 상한은 약 8,000요청이다. 유료 LLM 호출은 Solar Pro 3로
통일하고, 호출 비중은 matching 50%, parsing/extraction 20%, 질문·재평가 20%,
설명 10%로 계획한다.

비용 절감:

- trial criteria와 공통 prompt를 revision별 캐싱
- trial 단위 batch matching
- 세 baseline의 최초 매칭 결과 재사용
- 합성 환자·masked hidden answer 반복 사용
- 답변과 관련된 criterion만 재호출
- invalid batch만 부분 재시도

USD 70 배분:

| 연결·adapter | 프롬프트 | 비교실험 | 최종평가 | 예비비 |
|---:|---:|---:|---:|---:|
| $7 | $14 | $28 | $14 | $7 |

## MVP 최종 산출물

- 실행 가능한 멀티에이전트 CLI
- 합성 환자 100세션 결과 JSON
- criterion 근거와 질문·답변·재평가 이력
- 환자별 추천 순위와 설명
- 세 baseline 성능·질문 수·비용 비교표
- 대표 환자 3명 데모
- 데이터 출처, 재현 명령과 의료적 면책 고지

## 바로 다음 작업

1. TrialGPT label mapper와 dataset manifest를 구현한다.
2. ClinicalTrials.gov run-cache adapter와 TREC qrels loader를 구현한다.
3. 10개 masked 표본을 만들고 세 baseline CLI 입력 계약을 고정한다.

매 단계에서 기존 102개 테스트와 `rules.py`의 결정 규칙을 유지한다.
