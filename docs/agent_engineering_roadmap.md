# ClarifyTrial 6-Week Engineering Roadmap

_2026-07-11 기준. 쉬운 전체 설명은 `docs/project-overview-ko.md`, 데이터 출처와
라벨 규칙은 `DATA_SOURCES.md`를 따른다._

## 목표

patient 단위로 분리한 100개의 masked session specification을 대상으로 criterion
판단, 우선 질문, 답변 반영, 표적 재평가와 근거 기반 추천을 한 명령으로
실행한다. 네 정보 획득 정책과 targeted/full 재평가를 분리해 비교한다.

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
| 1주차: 데이터 계약 | label mapping, source manifest, patient split, 10개 audited multi-mask pilot | counterfactual test와 trials-per-patient 분포 확인 |
| 2주차: 정책 기준선 | immutable state fork, Fixed·FIFO@3·Clarify@3·Ask-all runner | 같은 initial state에서 네 정책 paired replay |
| 3주차: Solar | 공통 client, batch matching, schema validator, cache, token·cost log | 100-session manifest와 20-session paid pilot |
| 4주차: 질문·재평가 | priority key, question validator, hidden oracle, targeted/full harness | H1~H3 지표와 non-target mutation 자동 채점 |
| 5주차: ablation | FIFO, no-dedup, full rerun 비교와 leakage audit | 세 ablation 결과와 실패 사례 trace |
| 6주차: 최종 평가 | locked holdout, patient-clustered CI, 대표 환자 3명 데모 | 결과 JSON·Markdown·비교표·면책 고지 |

## 팀별 조사와 완료 산출물

| 담당 | 핵심 질문 | 완료 산출물 | 거시 계획과의 연결 |
|---|---|---|---|
| 팀원 1: 데모·제안 | 최종 데모 5~6단계, 필수·선택 기능, 제안 문장 | 데모 흐름 1장, Must/Should/Could 표, 제안 요약 2문장 | 사용자 가치와 MVP 경계 고정 |
| 팀원 2: 데이터·평가 | TrialGPT adapter, multi-mask 가능성, 비교군·지표 | 10-session audited pilot, label mapping, 4-policy 평가표 | H1·H2의 정답지와 공정성 확보 |
| 팀원 3: API·비용 | API/로컬 단계, 캐시, targeted 재평가, USD 70 | 호출 그래프, 20-session usage report, 갱신된 비용표 | H3와 100-session 실행 가능성 확인 |

각 팀은 새로운 독립 계획을 만드는 대신 같은 `research-design-ko.md`의 용어와
acceptance gate를 사용한다. 조사 결과는 각각 `proposal_brief.md`,
`DATA_SOURCES.md`, 이 문서의 API 계획에 합친다.

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

## 평가 트랙

| 트랙 | 입력 | 검증 대상 |
|---|---|---|
| A0 | 원본 TrialGPT annotations | 정적 criterion matching·evidence |
| A1 | TrialGPT 기반 multi-mask session | 제한예산 질문과 재평가 |
| B | TREC 2021·2022 historical corpus | 정적 retrieval·ranking |
| C | 별도 Synthea patient | 선택적 FHIR route 데모 |

A1과 B의 점수를 합치지 않는다. live ClinicalTrials.gov 후보 3~5개는 운영 데모,
TREC는 historical ranking 평가로 분리한다.

## 정책 비교

| 실험 | 행동 예산 | 정보 획득 정책 |
|---|---:|---|
| Fixed-input | 0 | 추가 질문 없음 |
| FIFO@3 | 3 | 발견 순서대로 확인 |
| Clarify@3 | 3 | potential impact가 큰 정보부터 확인 |
| Oracle Ask-all | 전체 | benchmark의 모든 hidden answer 제공 |

공통 조건:

- 같은 immutable initial state, 후보 trial, criterion matcher와 hidden answer 사용
- TrialGPT expert label로 criterion 판단 평가
- TREC qrels로 trial ranking 평가
- patient_id 단위 development/holdout 분리
- 각 정책은 같은 targeted re-evaluation 사용
- targeted/full 비교는 같은 Clarify answer sequence로 별도 실행

핵심 지표:

- correct resolution, wrong resolution, `U = CR - WR`
- criterion macro-F1·per-class F1·non-target mutation
- FIFO@3 대비 행동당 회복량과 impact coverage@3
- candidate panel nDCG@5, TREC nDCG@10·P@10·RPrec·MRR
- 평균 행동 수·호출 수·토큰·비용·median·p95 지연

## 우선순위 계약

hidden answer를 보지 않고 다음 key를 내림차순 정렬한다.

```python
priority_key = (
    potential_flip_trial_count,
    affected_trial_count,
    affected_criterion_count,
    -route_burden,
    stable_variable_key,
)
```

`manual_review_required`는 가중치가 아니라 `needs_human_review` hard override다.
core 실험은 모든 정책이 같은 canonical question과 typed hidden-answer oracle을
사용한다. Synthea FHIR, note retrieval과 LangGraph는 core 결과 이후 선택적으로
연결한다.

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
- 네 정책의 최초 매칭 결과 재사용
- 합성 환자·masked hidden answer 반복 사용
- 답변과 관련된 criterion만 재호출
- invalid batch만 부분 재시도

USD 70 배분:

| 연결·adapter | 프롬프트 | 비교실험 | 최종평가 | 예비비 |
|---:|---:|---:|---:|---:|
| $7 | $14 | $28 | $14 | $7 |

## 범위 우선순위

| 우선순위 | 범위 |
|---|---|
| Must | TrialGPT adapter, multi-mask benchmark, 네 정책 runner, priority, targeted/full 비교, usage log |
| Should | TREC historical ranking |
| Could | LangGraph interrupt/resume, Synthea FHIR, open-ended note RAG |

## MVP 최종 산출물

- 실행 가능한 멀티에이전트 CLI
- 100개 masked session specification 결과 JSON
- criterion 근거와 질문·답변·재평가 이력
- 환자별 추천 순위와 설명
- 네 정책과 targeted/full 성능·행동 수·비용 비교표
- 대표 환자 3명 데모
- 데이터 출처, 재현 명령과 의료적 면책 고지

## 바로 다음 작업

1. TrialGPT label mapper와 source manifest를 구현한다.
2. patient 단위 split 후 10개 multi-mask pilot을 만들고 leakage audit를 실행한다.
3. immutable state fork와 Fixed·FIFO@3·Clarify@3·Ask-all 입력 계약을 고정한다.
4. ClinicalTrials.gov run-cache adapter와 20-session Solar pilot을 실행한다.

매 단계에서 기존 102개 테스트와 `rules.py`의 결정 규칙을 유지한다.
정답 접근 통제, metric 정의와 acceptance gate는
[`research-design-ko.md`](research-design-ko.md)를 canonical 계약으로 사용한다.
