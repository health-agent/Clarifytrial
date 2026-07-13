# ClarifyTrial 지원서 제출 양식

## 프로젝트명

**ClarifyTrial: 부족정보 획득과 표적 재평가를 포함한 임상시험 추천 멀티에이전트 시스템**

## 타깃 사용자

임상시험 코디네이터와 임상의

## 한 문장 소개

환자 정보와 임상시험 선정·제외 기준을 근거와 함께 매칭하고, 판정에 필요한
정보가 부족하면 여러 후보 임상시험의 상태를 바꿀 수 있는 정보를 우선 확인한 뒤
관련 기준, 추천 순위와 설명을 갱신하는 연구용 대화형 임상시험 사전선별 시스템이다.

## 문제 정의

임상시험 매칭에서는 환자의 질환명만 비슷하다고 참여 가능하다고 판단할 수 없다.
연령, 병기, 바이오마커, ECOG, 이전 치료, 최근 검사값, 동반질환 등 여러 선정·제외
기준을 환자 기록과 하나씩 비교해야 한다. 그러나 초기 임상요약에는 이 정보가 모두
들어 있지 않은 경우가 많다. 기존의 single-shot 방식은 부족한 값을 unknown으로
남긴 채 추천을 끝내고, 모든 정보를 묻는 방식은 질문과 조회 업무가 과도해진다.

ClarifyTrial은 unknown을 단순한 실패가 아니라 다음 정보 획득 행동을 정하는 상태로
사용한다. 같은 정보가 여러 임상시험 기준에 필요하면 한 번만 확인하고, 새 답변과
연결된 criterion만 다시 평가한다.

## 연구 목표 및 접근 방법

1. 환자 임상요약, clinical note 또는 합성 FHIR 기록을 표준 patient variable로 정규화하고 모든 값에 출처 문장을 연결한다.
2. ClinicalTrials.gov API에서 임상시험을 수집하고 eligibility criteria의 원문 span, 수치·단위·시간 창과 Boolean 논리를 보존해 캐시한다.
3. BM25와 dense embedding을 결합해 관련 후보 trial을 찾고, relevance 검색과 eligibility 판정을 분리한다.
4. Criterion Matching Agent가 각 criterion을 근거와 함께 met, unmet, unknown, not_applicable, conflict로 판정한다.
5. Missing Information Controller가 unknown의 원인 변수를 추출하고 여러 trial에 반복되는 변수를 하나의 전역 항목으로 통합한다.
6. Next-Best-Action Planner가 가능한 답변 구간별 trial 상태 변화, 영향 trial·criterion 수와 관찰 가능한 획득 경로 비용을 이용해 다음 정보를 선택한다.
7. core 실험에서는 structured EHR, clinical note RAG, 직접 질문, 전문가 이관 순의 availability-first cascade를 고정하고 route 자체는 별도 합성 case에서 평가한다.
8. 답변을 값·단위·시점·출처로 정규화한 뒤 그 변수와 연결된 criterion만 표적 재평가한다.
9. 결정론적 controller가 inclusion/exclusion 효과, 최종 참여 가능성과 추천 순위를 계산하고 LLM은 구조화·근거·질문·설명 생성에 사용한다.
10. 정보 획득 순서 실험과 Full/Targeted 재평가 실험을 분리하고, TrialGPT interaction과 TREC historical ranking 결과도 별도 표로 평가한다.

## 멀티에이전트 구성

| 구성 | 역할 | 주요 출력 |
|---|---|---|
| Criteria Parser | 원문 선정·제외 기준을 source span이 있는 Boolean AST로 변환 | source item, atom ID, logic, required variables |
| Patient Understanding Agent | 환자 note/FHIR에서 표준 변수와 근거 추출 | PatientProfile, evidence sentence IDs |
| Candidate Trial Retrieval | 구조화 필터, BM25, dense retrieval과 reranking | candidate trial list |
| Criterion Matching Agent | 환자 근거와 각 criterion을 비교 | status, reason, evidence, missing keys |
| Missing Information Controller | unknown 원인 추출, cross-trial 중복 제거 | global missing-variable pool |
| Next-Best-Action Planner | 어떤 변수를 먼저 확인할지 결정론적으로 계산 | selected variable, priority key |
| Information Acquisition Controller | FHIR, note, 직접 질문, 전문가 이관 cascade 실행 | route, source result |
| Question & Answer Agent | 한 variable의 질문 생성과 자유답변 정규화 | question intent, normalized answer |
| Targeted Re-evaluation Agent | 답변과 연결된 criterion만 재판정 | before/after criterion states |
| Recommendation & Explanation Agent | 참여 가능성, 추천 순위와 설명 생성 | ranked trials, rationale, disclaimer |

전체 상태는 환자별 `PatientSession`에 저장한다. 에이전트끼리 자연어 메시지만
주고받지 않고 criterion ID, missing-variable key와 evidence ID가 있는 JSON으로
상태를 갱신한다. LLM이 낸 label은 schema validator를 통과한 뒤 Python rule로
eligibility effect를 다시 계산한다.

## 단계별 구현 방법

### 1. 환자 정보 구조화

임상요약을 sentence ID로 나누고 연령, 성별, 질환, 병기, 바이오마커, ECOG,
치료명·치료 시점, 동반질환, lab value와 지역을 추출한다. 수치에는 단위와 검사일,
모든 값에는 source sentence 또는 FHIR resource ID를 붙인다. 기록에 없는 값은
음성으로 간주하지 않고 unknown으로 둔다.

Synthea를 연결할 경우 `Patient`, `Condition`, `MedicationRequest`, `Procedure`,
`Observation`, `DiagnosticReport`를 초기 resource로 사용한다. 구조화 값이 있으면
직접 읽고, 없으면 clinical note RAG 또는 코디네이터 질문으로 이동한다.

### 2. 임상시험 수집과 기준 구조화

ClinicalTrials.gov API v2에서 모집 상태, 질환과 지역을 필터링해 trial metadata와
eligibility criteria를 가져온다. criteria의 목록 순서, inclusion/exclusion 구역,
수치 범위, 단위, 시간 창과 `A and (B or C)` 논리를 source span이 있는 Boolean
AST로 만든다. 원문 bullet 하나가 여러 atom으로 분리될 수 있으므로 개수 일치 대신
span coverage, 괄호 구조, atom ID와 수치 보존을 검증한다. raw response를 hash로
freeze하고 parsing 결과는 `NCT ID + update date + response hash + parser version`으로
캐시한다.

inclusion leaf는 충족해야 할 predicate, exclusion leaf는 해당하면 차단되는
predicate로 canonicalize하고 원문과 source span을 함께 보존한다. 따라서 원문의
긍정·부정 표현과 관계없이 `exclusion met = blocks` 의미가 일정하다.

### 3. 후보 검색

환자 질환, 하위 유형, 병기, 바이오마커, 치료 이력과 지역으로 query를 만든다.
BM25 lexical 검색과 dense vector 검색을 병렬로 수행하고 Reciprocal Rank Fusion으로
합친다. 후보 수는 고정하지 않고 TREC eligible trial Recall과 matching 비용 곡선으로
정한다. retrieval 점수는 관련성 순위에만 사용하며 명시적 exclusion 위반을 뒤집지
않는다.

### 4. 기준별 판정

각 AST leaf가 요구하는 변수와 환자 근거를 연결한 뒤 수치, 단위, 시점과 부정을
비교하고, Boolean logic은 로컬 three-valued rule로 집계한다. 출력에는 모든 atom과
criterion ID, truth, 근거 sentence ID, 이유와 missing variable을 기록한다. 근거가
없으면 unknown, 서로 충돌하면 conflict로 둔다.

criterion 문장이 참인지 나타내는 truth와 참여에 미치는 effect를 분리한다.
inclusion met와 exclusion unmet은 참여를 지원하고, inclusion unmet과 exclusion
met은 참여를 차단한다. 이 변환은 LLM이 아니라 고정 rule이 수행한다. 제출용
`satisfied`/`violated`에는 criterion type과 `eligibility_effect`를 항상 함께 붙인다.

### 5. 다음 정보 획득 행동 선택

unknown criterion에서 missing variable을 모은다. concept, 단위, 검체, 대상과 시간
창까지 같을 때만 여러 trial의 항목을 하나로 합친다. 연속 수치는 criterion
threshold에 따라 판정이 같은 decision-equivalence class로 나누고 대표값만 대입한다.
MVP 주 정책은 다음 사전식 tuple을 내림차순 비교한다.

```text
priority_key(v) = (
  flip_capable_trial_count,
  affected_trial_count,
  affected_decision_critical_criterion_count,
  -minimum_feasible_route_cost_tier,
  stable_variable_key
)
```

- 가능한 값에 따라 상태가 바뀌는 trial 수를 가장 먼저 본다.
- 그다음 영향을 받는 trial 수와 차단 여부가 달라지는 criterion 수를 본다.
- 같은 경우 관찰 가능한 metadata에서 실행 가능한 route의 비용 등급을 비교한다.
- 마지막 stable key는 같은 입력에서 순서를 재현하기 위한 tie-breaker다.

가중 `heuristic_priority_score`는 민감도 실험에만 사용하고 보정된 expected utility로
부르지 않는다. 질문 횟수에는 기본 상한을 두지 않는다. 남은 정보가 trial 상태를
바꿀 수 없거나, 획득 가능한 정보가 없거나, 사용자가 더 답할 수 없을 때 종료한다.

### 6. 질문·답변과 표적 재평가

선택한 변수에 구조화 EHR mapping과 관찰 가능한 source metadata가 있으면 먼저
조회하고, 해결되지 않으면 note RAG, 그 뒤 직접 질문을 사용한다. 충돌하거나 전문
해석이 필요하면 사람 검토로 보낸다. core benchmark의 한 행동은 변수 하나와 intent
하나만 다룬다. 질문 prompt에는 변수 정의, 허용 답변 형식, 단위, 시간 창, 영향을
받는 criteria와 이미 물은 질문을 준다.

답변은 `value, unit, observed_at, source`로 정규화한다. 불명확한 답은 unknown을
유지하고 기존 정보와 충돌하는 답은 덮어쓰지 않는다. 정상 답변이면 dependency
index에서 연결 criterion만 가져와 다시 평가하고 추천을 갱신한다.

### 7. 최종 추천과 설명

한 개라도 blocking criterion이 있으면 `likely_ineligible`, conflict 또는 검토
대상이 있으면 `needs_human_review`, 차단은 없지만 decision-critical unknown이
하나라도 있으면 `uncertain`이다. 적용 가능한 모든 inclusion이 met이고 모든
exclusion이 unmet일 때만 `likely_eligible`이다.
추천 순위는 참여 가능성, trial relevance와 남은 uncertainty를 함께 사용하되
명시적 차단 trial은 뒤에 둔다. 설명 agent는 이미 결정된 상태에서 지원 기준,
차단 기준, 남은 부족정보, 질문·답변과 순위 이유를 읽기 쉽게 정리한다.

## 데이터와 정답셋

| 트랙 | 데이터와 사용 방법 |
|---|---|
| Trial 입력 | [ClinicalTrials.gov](https://clinicaltrials.gov/data-api/about-api)의 timestamp가 고정된 trial metadata와 criteria |
| Parser component | [Leaf Clinical Trials Corpus](https://www.nature.com/articles/s41597-022-01521-0)의 concept·source span·attribute annotation |
| A. Static criterion | [TrialGPT Criterion Annotations](https://huggingface.co/datasets/ncbi/TrialGPT-Criterion-Annotations)의 expert criterion label과 evidence sentence |
| B. Interactive policy | TrialGPT에서 파생한 multi-variable masked session의 질문 순서, 답변 반영과 종료 평가 |
| C. Static retrieval | [TREC Clinical Trials 2021](https://trec.nist.gov/data/trials2021.html)·[2022](https://trec.nist.gov/data/trials2022.html)의 historical topic·qrel ranking 평가 |
| D. Route | [Synthea](https://github.com/synthetichealth/synthea) 등을 이용해 동기화한 합성 FHIR·note·direct-answer case |

네 트랙은 환자와 trial 정답 체계가 다르므로 점수를 합산하지 않는다. 특히 TREC
qrel은 interactive hidden answer sheet에 넣지 않고 historical retrieval 결과로만
보고한다.

### Masked set 생성

TrialGPT annotation에서 expert evidence가 있는 patient-criterion pair를 선택하고,
같은 환자의 여러 trial을 session으로 묶는다. 질문 순서를 평가하는 session에는
서로 다른 missing variable을 복수로 가린다. 같은 사실을 표현하는 모든 span을
자연스럽게 삭제·재작성하고 `[MASK]` 같은 위치 표시는 사용하지 않는다. 완전한
기록, 답을 얻을 수 없는 unknown과 추가 행동이 필요 없는 negative session도 둔다.

마스킹 상태의 gold는 unknown, 원래 typed value를 답한 뒤 gold는 원 expert label로
둔다. criterion을 반대로 만드는 합성 답변은 threshold의 판정 구간에서 만들고
다른 환자 사실과의 일관성을 확인한다. “모름” 또는 문맥으로 해석할 수 없는 답은
unknown 유지 branch로 둔다. 질문은 문장 exact match가 아니라 variable, unit,
time-window intent의 joint match로 평가하고, intent가 맞을 때만 hidden answer를
반환한다.

각 case는 `full note → expert label`, `masked note → unknown`, `restored answer →
expert label`, `non-target criterion → unchanged` 계약을 통과해야 한다.

### Hidden answer sheet 구성

- `CriterionBenchmarkCase`: TrialGPT criterion과 evidence 정답
- `InteractiveAcquisitionSession`: original/masked note, 복수 missing variable,
  질문 intent, typed 답변 branch, 전후 criterion과 rule-derived trial state
- `TrecRetrievalTopic`: historical topic, corpus revision과 qrel
- `SyntheticRouteCase`: 같은 사실의 source availability와 기대 route

실행 agent에는 visible input만 주고 evaluator-only oracle은 별도 파일과 프로세스가
읽는다. interactive trial state는 full note와 deterministic rule에서 파생한 값으로
표시하며 TREC qrel 또는 임상 gold라고 부르지 않는다.

## 비교 실험

### 실험 A: 정보 획득 순서

| 정책 | 동작 |
|---|---|
| No-acquisition | 추가 정보를 얻지 않고 현재 상태로 종료 |
| Fixed-order | source order 또는 stable variable key 순서로 확인 |
| Coverage-only | 연결 criterion 수가 많은 변수부터 확인 |
| Clarify-priority | trial 상태 변화 가능성을 우선한 `priority_key` 순서 |
| Ask-all | 모든 answerable variable을 확인하는 information ceiling |

모든 정책에 같은 initial state, missing pool, hidden answer, matcher와 재평가 engine을
사용한다. 순서만 평가할 때는 gold missing pool을 주는 oracle-pool 모드, 전체
파이프라인에서는 detector 출력부터 쓰는 end-to-end 모드를 나눈다.

### 실험 B: 재평가 범위

| 방식 | 동작 |
|---|---|
| Full re-evaluation | 동일 답변 뒤 모든 candidate criterion을 다시 평가 |
| Targeted re-evaluation | 동일 답변 뒤 dependency index로 연결된 criterion만 평가 |

두 방식에는 같은 variable 순서와 typed answer trajectory를 replay한다. 결과 state
일치도와 non-target mutation, 호출·token·비용·latency를 측정한다. 따라서 정책의
효과와 표적 재평가의 계산 효과가 한 비교에 섞이지 않는다.

## 평가 방법

| 평가 대상 | 지표 |
|---|---|
| criterion status | inclusion/exclusion별 per-class F1, macro-F1, confusion matrix |
| evidence grounding | sentence precision/recall/F1, unsupported assertion rate |
| missing-variable 탐지 | target recall, false-positive variable rate, MRR |
| 질문 내용 | variable exact, unit match, time-window match, joint intent exact |
| 답변 정규화 | typed value 정확도, conflict detection, wrong commitment |
| interactive quality | criterion recovery, trial-state exact, non-target mutation |
| 정책 효율 | quality-action AUC, quality-cost AUC, cost-to-target-gain |
| 종료 | premature-stop rate, residual flip-capable variables |
| TREC retrieval | nDCG@10, P@10, RPrec, MRR; eligible Recall@k 보조 |
| 재평가 효율 | Full/Targeted state agreement, 호출·token·비용·latency |

TREC 평가는 공식 qrel과 같이 eligible=2, excluded=1, not relevant=0의 nDCG gain을
사용한다. unknown 결과는 `correctly_resolved`, `wrongly_committed`,
`remaining_unknown`으로 나눈다. 행동이 증가할 때 full-information criterion
recovery와 누적 비용을 매 단계 기록해 적절한 종료 구간을 찾는다. 복원·반대·불명확
답변 branch는 먼저 각각 보고한다.

## 세부 검증 실험

- cross-trial dedup을 끄고 trial마다 질문했을 때 반복 질문 수 비교
- priority를 fixed-order 또는 coverage-only로 바꿨을 때 품질·비용 곡선 비교
- decision-sensitive 항을 빼고 coverage만 사용했을 때 비교
- 동일 trajectory에서 targeted와 full 재평가의 state·호출 수 비교
- evidence sentence를 요구한 matcher와 label만 요구한 matcher의 근거 F1 비교
- stop policy 없이 모든 answerable variable을 확인했을 때 비교

route cascade는 별도 `SyntheticRouteCase`에서 평가한다. 각 비교는 환자별 paired
결과를 만들고 bootstrap으로 95% 구간을 계산한다.

TrialGPT 파생 자료는 patient ID 단위로 development와 최종 평가를 나누며, 같은
환자의 trial, mask variant와 answer branch는 같은 partition에 둔다. priority의
가중치·정규화·종료 조건은 development에서 고정하고 최종 평가에서는 바꾸지 않는다.
TREC 결과는 official metric으로 별도 산출하며 interactive 점수와 합산하지 않는다.

## 선행연구와 차별성

[TrialGPT](https://www.nature.com/articles/s41467-024-53081-z)는 retrieval,
criterion-level matching, evidence sentence와 ranking을 결합했고, 공개 expert
annotation은 ClarifyTrial matcher의 직접 평가 자료가 된다.
[TrialMatchAI](https://www.nature.com/articles/s41467-026-70509-w)는 환자·trial
정규화, BM25와 vector search의 hybrid retrieval, criterion reranking과 구조화된
eligibility 설명을 보여주므로 ClarifyTrial의 검색 설계에 반영한다.

[DeepEnroll](https://arxiv.org/abs/2001.08179)은 수치 정보 entailment의 중요성을,
[COMPOSE](https://arxiv.org/abs/2006.08765)는 inclusion과 exclusion을 분리해
처리해야 함을 보여준다. ClarifyTrial은 이를 값·단위·시점 구조화와 명시적
criterion-effect rule로 구현한다.

[Leaf Clinical Trials Corpus](https://www.nature.com/articles/s41597-022-01521-0)는
eligibility criteria의 세밀한 concept·span·attribute annotation을 제공하므로 parser
component 평가에 사용한다. 완전한 Boolean AST 정답은 아니므로 괄호 구조와
source-item 분해는 별도 독립 검토 set으로 확인한다.

질문 선택은 임상시험 매칭 밖의 선행연구도 이용한다.
[Learning to Ask Medical Questions](https://proceedings.mlr.press/v126/shaham20a.html)은
가려진 feature를 환자마다 다른 순서로 획득하는 adaptive policy를,
[FollowupQ](https://aclanthology.org/2025.acl-long.1226/)는 patient message와 EHR를
함께 사용하는 multi-agent 질문 생성과 question-intent 평가를,
[Diaformer](https://ojs.aaai.org/index.php/AAAI/article/view/20365)와
[DxFormer](https://arxiv.org/abs/2205.03755)는 질문 단계와 최종 판단 단계의 분리를
제시한다.

[Chen et al., Scientific Reports 2025](https://www.nature.com/articles/s41598-025-11876-0)는
eligibility criteria에서 모집 questionnaire를 만들고 환자 답변으로 eligibility를
평가하며 knowledge graph QA까지 연결했다. 따라서 criterion-question-answer 연결도
이미 존재하는 직접 선행연구다.

가장 가까운 직접 선행연구는 2026년 UTHealth Houston의
[patient-centric clinical trial chatbot](https://sbmi.uth.edu/research/phd-dissertations/a-patient-centric-chatbot-for-improving-clinical-trial-accessibility.htm)이다.
이 연구는 criteria clustering, 환자용 질문, 답변 판정과 dynamic trial elimination을
이미 다룬다. 따라서 ClarifyTrial은 “임상시험 후속 질문 자체”를 최초라고 주장하지
않는다.

검토한 연구를 기준으로 ClarifyTrial이 새롭게 검증하려는 결합은 다음과 같다.

1. concept, 단위와 시간 창이 같은 missing variable이 여러 candidate trial에 미치는 영향을 semantic dependency graph로 계산한다.
2. 가능한 답변에 따라 trial state가 바뀔 수 있는 정보를 fixed-order보다 먼저 확인하는 정책을 검증한다.
3. 동일 answer trajectory에서 연결 criterion만 갱신해 full re-evaluation state를 보존하면서 계산량을 줄이는지 별도로 검증한다.
4. 복수 missing variable, typed 답변, criterion recovery와 non-target 불변을 포함한 interactive benchmark를 만든다.
5. FHIR·note·질문 route는 동기화한 합성 case에서 core interaction 결과와 분리해 탐색적으로 검증한다.

따라서 hybrid retrieval, criterion matching, 후속 질문 자체를 신규 기여로 주장하지
않는다. scoped literature review에서 직접 확인하지 못한 cross-trial dependency,
decision-sensitive ordering과 targeted update의 결합을 실험 가설로 제시하며
“최초” 또는 “유일”이라고 표현하지 않는다.

## 예상 API 사용량 및 비용 계획

LLM API는 criteria parsing, 환자 정보 추출, criterion matching, 질문 문장,
자유답변 정규화와 최종 설명에 사용한다. 후보 필터링, retrieval fusion,
missing-variable 통합, priority 계산, 상태 갱신, effect 변환, 재평가 대상 선택과
metric 계산은 로컬 코드로 처리한다.

환자 session당 호출 수는 다음 식으로 측정한다.

```text
patient extraction
+ uncached trial parsing
+ initial matching batches
+ 정보 획득 횟수 * (question + answer normalization + linked rematching)
+ final explanation
```

trial parsing과 embedding은 `NCT ID + update date + response hash + parser version`으로 캐시하고,
한 trial의 criteria는 batch로 matching한다. 답변 뒤에는 연결 criterion만 다시
호출하며 파싱 실패 항목만 retry한다. 각 요청의 input/cached/output token, latency,
retry와 실행 시점 단가를 저장해 session당 비용을 계산한다.

```text
uncached_input_tokens = total_input_tokens - cached_input_tokens

cost = uncached_input_tokens * uncached_input_rate
     + cached_input_tokens   * cached_input_rate
     + output_tokens         * output_rate
```

cold-cache와 warm-cache 비용을 분리해 cached token을 중복 계산하지 않는다.

예산을 임의 금액으로 고정하지 않는다. 소규모 pilot에서 단계별 token 비중과
latency를 확인한 뒤 candidate 수, matching batch 크기와 평가 규모를 정한다.
모델별 비교는 accuracy뿐 아니라 criterion 하나와 patient session 하나를 끝내는
비용을 함께 보고한다.

## 현재 구현과 제안 구현

현재 저장소에는 Pydantic 기반 `PatientSession`, inclusion/exclusion effect rule,
합성 환자·trial 입력용 offline heuristic parser/matcher, global missing-variable dedup,
질문 template, 단일 변수 답변 정규화와 추천 골격이 구현되어 있다.

본 제안에서 구현·검증할 범위는 ClinicalTrials.gov·TrialGPT·TREC adapter, Boolean
criteria AST, 실제 LLM matcher와 evidence RAG, semantic dependency와 priority policy,
queue가 연결된 targeted re-evaluation, 네 평가 schema와 공개 benchmark runner다.
Synthea FHIR와 note route는 core 결과와 분리한 선택 시연으로 둔다.

## 기대 산출물과 MVP 범위

### 필수 산출물

- ClinicalTrials.gov trial adapter와 criteria cache
- TrialGPT criterion benchmark와 TREC historical ranking benchmark runner
- multi-mask 생성기, visible bundle과 evaluator-only hidden answer sheet
- 환자 입력부터 질문 전·후 판정, 추천과 설명까지 이어지는 멀티에이전트 실행기
- 다섯 acquisition policy 비교와 Full/Targeted replay 결과
- criterion 근거, missing variable, 질문·답변과 전후 상태가 담긴 결과 JSON
- 행동 수별 criterion recovery·비용 곡선과 session별 API 사용량
- 재현 명령, 데이터 출처와 의료적 면책 고지

### 선택 기능

Synthea FHIR 조회와 clinical note RAG를 연결해 availability-first cascade가
구조화 EHR, note, 코디네이터 질문과 전문가 이관을 거치는 과정을 시연한다.

## 최종 데모

1. 합성 환자 임상요약 또는 합성 FHIR 기록을 입력한다.
2. 관련 ClinicalTrials.gov trial과 각 criterion을 보여준다.
3. 최초 criterion 판정, 근거와 unknown 원인을 보여준다.
4. 다음으로 확인할 정보와 availability-first cascade가 선택한 행동을 보여준다.
5. 숨겨둔 환자 답변을 넣고 연결 criterion만 바뀌는 과정을 보여준다.
6. 질문 전·후 참여 가능성, 추천 순위, 근거, 남은 불확실성과 API 비용을 비교한다.
7. Fixed-order·Coverage-only·Clarify-priority의 품질·비용 곡선과 Ask-all ceiling을
   제시하고, Full/Targeted 재평가 결과는 별도 표로 보여준다.

## 최종 출력

최종 결과에는 제출용 `eligible`, `ineligible`, `uncertain`과 내부
`screening_status`를 함께 둔다. criterion별 `satisfied`, `violated`, `unknown`,
`not_applicable`에는 `criterion_type`, `criterion_truth`, `eligibility_effect`를
붙여 exclusion criterion의 `satisfied`가 참여 차단일 수 있음을 명확히 한다.
근거 문장, 부족정보 질문과 답변, 질문 전·후 변화, 환자별 추천 순위와 설명,
`review_required` 사유와 의료적 면책 고지를 포함한다.

## 의료적 면책

ClarifyTrial은 합성 환자와 공개 임상시험 데이터에서 평가되는 연구용 사전검토
프로토타입이다. 출력은 의료적 자문, 임상시험 적격성 확정 또는 등록 결정을
대체하지 않으며, 최종 판단은 자격을 갖춘 임상 전문가가 수행해야 한다.
