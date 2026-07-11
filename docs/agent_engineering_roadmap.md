# Agent Engineering Roadmap

_2026-07-11 기준. 현재 저장소의 실제 구현과 검증된 공개 데이터 범위를 바탕으로
작성한 6주 MVP 계획이다. 상세 데이터 근거는 `DATA_SOURCES.md`, 쉬운 전체 설명은
`docs/project-overview-ko.md`를 따른다._

## 1. 현재 기준선

이미 구현된 것:

- `PatientSession` 중심의 Pydantic 공유 상태와 3라운드 제한
- 선정·제외 효과, 추천 우선순위와 ranking의 결정론적 규칙
- criteria parsing, 기본 환자 추출, criterion matching, missing-info 탐지,
  질문 생성과 답변 정규화의 오프라인 휴리스틱
- 합성 데이터 4종, 오프라인 데모와 102개 테스트
- Python 3.10과 3.13 GitHub CI

아직 구현되지 않은 것:

- ClinicalTrials.gov, TrialGPT, TREC 데이터 adapter
- Git 밖의 평가용 run cache와 provenance manifest
- Eligibility State Tracker 상태 변경 함수 전체
- Solar API client, 실제 토큰·비용 로그와 batch matcher
- masked incomplete-information benchmark와 세 baseline
- LangGraph interrupt/resume와 선택적 Synthea FHIR 조회

현재 테스트는 소프트웨어 계약과 규칙을 검증할 뿐 임상 정확도나 실제 데이터
성능을 증명하지 않는다.

## 2. MVP 완료 조건

다음 조건이 모두 충족되어야 MVP 완료로 본다.

1. 실제 ClinicalTrials.gov 기준을 실행별 cache로 읽고 query, data timestamp,
   NCT ID와 hash를 기록한다.
2. TrialGPT expert labels로 criterion 판정을 재현 가능하게 채점한다.
3. TREC qrels로 trial 검색·추천 순위를 별도로 채점한다.
4. 50~100개 masked criterion 사례에서 질문 전후 변화를 평가한다.
5. Fixed-input, Ask-all, ClarifyTrial이 같은 matcher와 후보 trial을 사용한다.
6. ClarifyTrial은 최대 3회 정보 획득 후 영향 criterion만 재평가한다.
7. 결과에 criterion 근거, 남은 unknown, 질문 이력, 추천과 면책 고지가 포함된다.
8. 모델·프롬프트 버전, 토큰, 비용, 지연, 재시도와 fallback이 기록된다.

Synthea/FHIR, 웹 UI와 실시간 ClinicalTrials.gov 새로고침은 이 조건에 포함하지
않는다.

## 3. 6주 구현 계획

### Week 1: 데이터 계약과 gold adapter

산출물:

- `DATA_SOURCES.md`와 dataset manifest schema
- ClinicalTrials.gov API v2 adapter와 Git 밖의 run cache 생성 명령
- TrialGPT 1,015개 행 loader, label mapper, evidence loader
- TREC 2021·2022 topic/qrels loader
- explicit-evidence 기반 masked-set 생성기 초안

종료 조건:

- 다운로드 파일의 출처·revision·SHA-256·변환 버전이 모두 기록된다.
- TrialGPT `conflict` 부재와 TREC의 trial-level 범위가 테스트로 고정된다.
- 외부 원본 대용량 파일 없이 fixture subset으로 CI가 통과한다.

### Week 2: 결정론적 실행과 공정한 baseline

산출물:

- Eligibility State Tracker의 trial 등록, criterion 갱신, round 증가 함수
- frozen 입력을 끝까지 처리하는 deterministic pipeline
- Fixed-input, Ask-all, ClarifyTrial 세 실행 모드
- 동일 후보, 동일 matcher, 동일 hidden answer를 강제하는 실험 설정

종료 조건:

- 여섯 기존 합성 시나리오를 end to end로 재현한다.
- 여러 trial에 걸친 같은 missing variable은 한 번만 묻는다.
- Ask-all과 ClarifyTrial의 차이는 질문 정책뿐이다.

### Week 3: Solar 연결과 비용 통제

산출물:

- 공통 Solar client, timeout, retry, schema validator와 deterministic fallback
- `RequestLog`의 input/cached/output tokens, estimated cost, attempt 필드
- trial 기준 parsing cache와 trial 단위 batch criterion matching
- prompt/version registry

목표 호출 수:

- 환자 정규화 1회
- 후보 trial 3~5개의 batch matching 3~5회
- 답변·표적 재평가 0~3회
- 최종 설명 1회
- 평균 환자당 약 5~10회

종료 조건:

- API가 없어도 모든 테스트가 통과한다.
- 잘못된 출력은 추측하지 않고 `unknown` 또는 사람 검토로 내려간다.
- 실제 토큰 합계와 표준 가격 기준 비용을 리포트로 재계산할 수 있다.

### Week 4: 불완전정보와 Next-Best-Action 실험

산출물:

- 50~100개 masked criterion 사례와 비공개 hidden-answer split
- missing-variable detector와 canonical key dictionary
- 영향 trial 수, 예상 판정 변화, 획득 비용, 안전 민감도를 사용하는
  해석 가능한 우선순위 함수
- question generator와 독립 hidden-answer simulator
- 답변 후 dependency-based targeted re-evaluation

종료 조건:

- 자동 마스킹 표본을 수동 검토해 문맥 누출 사례를 제거하고, split은
  criterion 행이 아니라 `patient_id` 단위로 만든다.
- 질문 전후 unknown 감소와 추천 변화가 자동 채점된다.
- 같은 LLM이 질문과 정답을 동시에 만들지 않는다.

### Week 5: 상태 그래프와 선택적 정보 획득 경로

산출물:

- 기존 tracker 함수를 감싸는 LangGraph node와 checkpoint
- 질문 직전 interrupt, 답변 뒤 동일 session resume
- `structured_ehr`, `clinical_note`, `direct_question`, `human_review`
  acquisition action interface
- 핵심 실험 통과 시에만 Synthea FHIR adapter 한 경로

종료 조건:

- 프로세스를 재시작해도 같은 세션을 재개한다.
- 답변과 무관한 criterion은 다시 호출하지 않는다.
- FHIR에 없는 정보를 없는 것으로 단정하지 않고 질문 또는 검토로 전환한다.

### Week 6: 평가·ablation·최종 데모

산출물:

- criterion macro-F1/accuracy와 evidence sentence F1
- missing-variable recall/precision within budget
- TREC nDCG@10/Recall@10
- 평균 정보 획득 행동 수, unknown 해소율, 올바른 추천 변화율
- 환자당 호출·토큰·비용·지연·실패율
- 대표 환자 3명의 고정 데모와 전체 결과 JSON/Markdown

종료 조건:

- 공개 development split에서 설정을 고정한 뒤 holdout은 한 번만 실행한다.
- 세 baseline 결과를 동일 표로 비교한다.
- 모든 결과에 근거, 불확실성, 질문 이력과 의료적 면책 고지가 남는다.

## 4. 실험 설계 규칙

- **Criterion gold:** TrialGPT `expert_eligibility`; GPT-4 예측은 gold가 아니다.
- **Evidence gold:** TrialGPT `expert_sentences`.
- **Trial ranking gold:** TREC qrels 0/1/2.
- **TREC corpus:** qrels와 같은 연도의 corpus를 사용하고 live API record와
  섞지 않는다.
- **Missing-info gold:** transformation manifest에 기록한 masked variable과
  hidden expert sentence.
- **Conflict gold:** 별도의 합성·수동 검토 fixture.
- **Unjudged TREC trial:** 자동 negative로 간주하지 않는다.
- **Recruitment/location:** TREC benchmark 점수와 실제 운영 필터를 분리한다.
- **Question budget:** ClarifyTrial 최대 3회; Ask-all은 부담 상한으로 별도 보고한다.
- **Data split:** 같은 `patient_id`는 development와 holdout 중 한쪽에만 둔다.

## 5. USD 70 예산 상한

2026-07-11 Solar Pro 3 공개 표준 가격을 기준으로 하며 계정별 무료 크레딧은
가정하지 않는다. 가격은 실행 전에 다시 확인한다.

| 용도 | 상한 |
|---|---:|
| 연결·adapter smoke test | $7 |
| 프롬프트·validator 개발 | $14 |
| baseline·ablation 반복 | $28 |
| 고정 holdout 최종 실행 | $14 |
| 재시도·가격 변동 예비비 | $7 |

비용 절감 원칙:

- criteria parsing은 trial별 한 번만 실행하고 revision key로 캐시한다.
- retrieval, dedup, priority, state update와 final rules는 로컬에서 실행한다.
- criterion을 하나씩 호출하지 않고 trial 단위 batch로 평가한다.
- 답변 뒤 전체를 다시 실행하지 않고 영향 criterion만 호출한다.
- invalid batch와 누락된 criterion만 재시도한다.

## 6. 연구 차별점과 검증 과제

정적 trial matching은 TrialGPT, TrialMatchAI와 여러 연구가 이미 강하다.
의료 후속질문 자체도 MediQ와 FollowupQ 같은 선행연구가 있다. 따라서
“LLM이 질문한다”만으로는 차별점이 아니다.

ClarifyTrial이 입증해야 할 차별점:

1. 여러 trial에서 같은 missing variable을 한 번으로 합친다.
2. 제한된 행동 예산 안에서 질문·EHR 조회·검토 중 다음 행동을 고른다.
3. 답을 얻은 뒤 관련 criterion만 재평가해 비용과 상태 변화를 추적한다.
4. 정확도뿐 아니라 질문 부담과 unknown 해소를 함께 평가한다.

정식 제안서에서 “최초”라고 쓰기 전에는 임상시험 매칭, active feature
acquisition, 의료 후속질문의 체계적 문헌 검토가 추가로 필요하다.

## 7. 구현 위험과 대응

| 위험 | 대응 |
|---|---|
| TrialGPT와 ClarifyTrial 라벨 의미 차이 | criterion type별 명시적 mapper와 mapping test |
| 마스킹 후에도 남은 문맥으로 답을 추론 가능 | explicit evidence만 사용하고 수동 audit |
| TREC 미판정 trial을 오답으로 처리 | official qrels 평가 규칙과 judged-only 분석 병행 |
| Synthea에 필요한 바이오마커가 없음 | optional demo로 제한하고 질문·검토 fallback |
| LLM이 criterion을 누락하거나 JSON을 깨뜨림 | ID 완전성 validator, 부분 retry, deterministic fallback |
| API 비용·가격 변화 | token ledger, stage cap, 실행 전 가격 snapshot |
| 질문기와 답변기의 정보 누출 | 별도 프로세스·입력 계약·hidden split |

## 8. 바로 다음 3일

- **Day 1:** 데이터 manifest와 TrialGPT label mapper/fixture tests.
- **Day 2:** ClinicalTrials.gov run-cache adapter와 TREC qrels loader.
- **Day 3:** masked-set 생성 규칙, 10개 표본 수동 audit, 세 baseline CLI 계약.

매 단계에서 기존 `rules.py`의 안전 우선순위와 102개 테스트를 유지한다.
