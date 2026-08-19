# ClarifyTrial 에이전트 구조 원문·코드 색인

문서 역할: 에이전트 재설계에 사용한 논문, 공식 발표 자료와 공개 코드를 한곳에서 찾기 위한 색인  
확인일: 2026-08-20  
현재 실험 상태: ClarifyTrial v5 실행 전, 성능 미측정

이 문서는 설계 설명을 반복하지 않는다. 실제 구조와 채택 판단은
[에이전트 구조 재설계 문서](CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md)에서 다룬다.

- `공식 코드`는 논문이나 저자·기관의 공식 페이지에서 연결된 저장소를 뜻한다.
- `미확인`은 코드가 없다는 단정이 아니라, 원문과 공식 발표 페이지에서 공개
  저장소를 확인하지 못했다는 뜻이다.
- 외부 코드를 이 저장소에 복사하지 않는다. 논문 재현에는 공식 저장소와 논문이
  지정한 버전이 있으면 그 버전을 사용한다.
- 발표 사이트가 만드는 짧은 유효기간의 다운로드 주소 대신, 다시 열 수 있는
  공식 논문·발표 페이지를 연결한다.

## 1. 최종 구조에 직접 쓰는 원문과 코드

아래 연구는 전체 시스템 또는 특정 핵심 단계에 직접 사용한다.

현재 선택한 중심 흐름은 다음과 같다.

> **시험 조건 준비 → PRomop식 환자 상태표 → TrialMatchAI 검색·조건 판정
> → CLEAR-MATCH 대화·후보 갱신 → TRIAGE식 단일 근거 검사 → MediQ식
> 정보 충분성 판단 → DQueST·Fink식 다음 확인 우선순위 → v5의 후보 유지·현재
> 확정 두 판단과 올바른 확인 경로**

Yang은 이 전체 흐름과 가장 가까운 독립 연구이고, EXACT는 구조화 규칙 엔진을
교체해 비교할 때의 대안이다. TrialGPT는 강한 고정 입력 기준선으로 사용한다.

| 연구 | 공식 원문·발표 자료 | 공식 코드·자료 | 공개 범위 | 재현할 때 주의할 점 |
|---|---|---|---|---|
| PRomop | [arXiv v2 원문](https://arxiv.org/abs/2607.13947) | [공식 코드](https://github.com/healthkey-ai/PRomop) | OMOP CDM 5.4와 FHIR 입력, 종양 확장, 환자의 장기 기록을 300개가 넘는 열로 정리한 `PatientRecord`, 검사·치료·질병 상태의 사전 계산, API·테스트·합성자료 재현 안내가 공개됨 | 임상시험을 검색하거나 적합성을 판단하는 에이전트가 아니라 여러 판정기가 함께 읽을 환자 상태표다. 종양 중심 파생 규칙을 그대로 모든 질환에 적용할 수 없고, 논문의 속도 향상이 조건 판정 정확도 향상을 뜻하지 않는다. |
| TrialMatchAI | [Nature Communications 논문·보충자료](https://www.nature.com/articles/s41467-026-70509-w) | [공식 코드](https://github.com/cbib/TrialMatchAI), [논문 당시 `v0.01`](https://github.com/cbib/TrialMatchAI/tree/v0.01) | 자료 준비, 개념 표준화, 두 단계 검색, 재정렬, 기준 판정과 평가 코드가 공개됨 | 기본 브랜치는 논문 뒤에 바뀌었다. 논문 구조 재현은 `v0.01`과 보충자료를 기준으로 하고, 현재 코드는 별도 최신 구현으로 취급한다. 모델과 색인 구축에 큰 계산 자원이 필요하다. |
| CLEAR-MATCH | [AMIA 공식 발표 페이지](https://amia.secure-platform.com/symposium/gallery/rounds/82021/details/20567) | 공식 코드·평가자료 미확인 | 포스터에 대화 단계, 주요 모듈, 질문 지침, 사용 기술과 소수 예비 사례가 공개됨 | 정식 논문, 대규모 정답 평가와 질문 선택 계산식이 공개되지 않았다. 따라서 전체 대화 흐름의 원형으로는 쓸 수 있지만 보고 성능을 재현하는 기준선으로는 아직 부족하다. |
| TRIAGE | [JCO Oncology Practice 원문](https://doi.org/10.1200/OP-26-00076) | 공식 코드·프롬프트·평가자료 미확인 | 기준일 이전의 장기 EHR, 기준별 판정과 원문 근거, 별도 자동 검토, 시험 단위 점수와 사람 재검토가 설명됨 | 기관 EHR와 버전이 있는 프로토콜이 필요하고 기반 모델·검색 방식이 공개되지 않았다. ClarifyTrial에서는 전체 예측 구조가 아니라 첫 판정의 근거·날짜·원문을 한 번 검사하는 부분만 재구현한다. |
| Yang, 2026 | [UTHealth Houston 공식 학위 소개와 초록](https://sbmi.uth.edu/research/phd-dissertations/a-patient-centric-chatbot-for-improving-clinical-trial-accessibility.htm) | 전체 학위논문, 코드와 세 주석자료의 공개 위치 미확인 | 기준 묶기, 환자용 질문, 답변 판정과 동적 시험 제거로 이어지는 전체 구성과 주요 결과가 공개됨 | 질문 우선순위 계산, 종료 조건, 비용 처리와 세부 프롬프트를 초록만으로 복제할 수 없다. 확인되지 않은 세부를 임의로 채우지 않는다. |
| EXACT | [AMIA 2026 공식 시스템 시연](https://amia.secure-platform.com/amplify/gallery/rounds/82026/details/26163) | [공식 공개 엔진](https://github.com/healthkey-ai/exact) | 속성별 기준 구조화, 환자 정보 보완, 조건 판정, 환자 가치에 따른 순위화와 실행 가능한 API·평가 도구가 공개됨 | 공개 발표는 시스템 시연이며, 독립된 대규모 정답 평가 논문은 확인되지 않았다. 저장소는 계속 개발되는 엔진이고 실제 시험 목록·참조자료는 외부 데이터베이스 연결이 필요하다. |
| TrialGPT | [Nature Communications 논문·보충자료](https://www.nature.com/articles/s41467-024-53081-z) | [공식 코드](https://github.com/ncbi-nlp/TrialGPT), [기준별 전문가 주석](https://huggingface.co/datasets/ncbi/TrialGPT-Criterion-Annotations) | 검색어 생성, BM25·MedCPT 검색, 순위 결합, 기준별 프롬프트, 판정·근거·순위화와 주석자료가 공개됨 | 당시 GPT 모델과 ClinicalTrials.gov 자료 시점을 그대로 맞추기 어렵다. 공개 주석 1,015건은 기준별 고정 입력 평가용이며 대화와 다음 행동의 정답은 아니다. |
| MediQ | [NeurIPS 2024 원문](https://proceedings.neurips.cc/paper_files/paper/2024/file/32b80425554e081204e5988ab1c97e9a-Paper-Conference.pdf) | [공식 코드](https://github.com/stellalisy/mediQ) | 현재 정보로 답할지 보류할지를 질문 생성과 분리하고, 숨긴 전체 기록에서 질문과 관련된 사실만 답하는 환경이 공개됨 | 임상시험 매칭 연구가 아니다. 정보 충분성 판단과 합성 답변 환경만 사용하며, 최대 질문 수에 도달했다고 답을 강제로 확정하지 않는다. |
| DQueST | [JAMIA 원문](https://academic.oup.com/jamia/article/26/11/1333/5544734) | [저자 공개 코드](https://github.com/stormliucong/dquest-flask) | 여러 남은 시험에 공통인 기준을 먼저 질문하고 답변 뒤 후보를 갱신하는 구조가 공개됨 | 실제 질문 점수는 완전한 정보이득 계산보다 후보 시험 범위에 가깝다. 한 답으로 시험을 곧바로 제거하는 규칙과 단순 조건 논리는 사용하지 않는다. |
| Fink 계열 | [Artificial Intelligence in Medicine 원문](https://www.cs.cmu.edu/~eugene/research/full/trial-selection.pdf) | 공식 코드 미확인 | 비용이 낮고 여러 시험과 조건에 영향을 주는 검사·질문을 우선하고 새 결과마다 재판정하는 구조가 공개됨 | 점수 가중치가 공개되지 않았고 오래된 수동 규칙 환경이다. 임의 가중치를 복제하지 않고 부담·위험·시간을 포함한 우선순위 원칙만 사용한다. |

## 2. 실제 임상 업무형 추가 참고

| 연구 | 공식 원문·보충자료 | 공식 코드·자료 | 공개 범위 | 재현할 때 주의할 점 |
|---|---|---|---|---|
| OncoAgents | [공개 원문](https://pmc.ncbi.nlm.nih.gov/articles/PMC13091143/) | 공식 코드·공개 평가자료 미확인 | 환자 정보 추출·표준화·판정 역할, 종양 지식 그래프, 시간 규칙, 조건 개정과 사람 검토 구조가 논문에 설명됨 | 정확한 모델, 전체 프롬프트, 호출 횟수와 사내 자료가 공개되지 않았다. 비공개 자료의 보고 성능을 공개 기준선처럼 재현할 수 없다. |

## 3. 기타 구조와 평가 참고

아래 연구는 특정 모듈이나 비교 실험의 근거다. 전체 ClarifyTrial 흐름의 주축으로
삼지 않는다.

| 연구 | 공식 원문 | 공식 코드·자료 | 공개 범위와 재현상 주의점 |
|---|---|---|---|
| Chen et al., 2025 | [Scientific Reports 원문·보충자료](https://www.nature.com/articles/s41598-025-11876-0) | 공식 코드·자료 미확인 | 기준에서 환자용 설문을 만들고 답변으로 판정하는 고정 흐름이 공개됐다. 여러 후보에 따라 다음 질문을 바꾸는 전체 정책의 재현 자료는 확인되지 않았다. |
| MAKAR | [arXiv 최신 원문](https://arxiv.org/abs/2411.14637) | 공식 코드 미확인 | 논문과 원본 TeX에 의사코드와 역할별 프롬프트가 있다. 논문 버전이 여러 번 바뀌었으므로 사용할 때 버전을 명시한다. 실행 코드와 정확한 반복 종료 조건은 공개되지 않았다. |
| MDAgents | [NeurIPS 2024 논문](https://proceedings.neurips.cc/paper_files/paper/2024/file/90d1fc07f46e31387978b88e7e057a31-Paper-Conference.pdf) | [공식 코드](https://github.com/mitmedialab/MDAgents) | 복잡도에 따른 실행 경로, 역할 모집과 단계별 프롬프트가 공개됐다. 공개 코드의 일부 모델 연결과 고정 역할 수가 논문 설정과 달라 둘을 같은 실행으로 보지 않는다. |
| MedAgents | [ACL Anthology 원문](https://aclanthology.org/2024.findings-acl.33/) | [공식 코드](https://github.com/gersteinlab/MedAgents) | 분야 선정, 독립 분석, 보고서 통합, 검토와 수정 흐름이 공개됐다. 논문과 코드의 생성 온도 등 실행 설정에 차이가 있고 호출량이 크다. |
| MCC | [Cell Reports Medicine 공개 원문](https://pmc.ncbi.nlm.nih.gov/articles/PMC12866169/) | [공식 코드](https://github.com/sunxinti/MCC) | 서로 다른 세 모델의 첫 판단, 불일치 때 추가 검토, 조기 종료와 최종 다수결이 공개됐다. 의료 객관식용 구조이며 임상시험 검색·기준 판정 하네스는 아니다. |
| AIDS2 | [공개 원문](https://pmc.ncbi.nlm.nih.gov/articles/PMC2248545/) | 공식 코드 미확인 | 직접 확인한 사실과 의학적으로 추정한 상태를 나누고, 부족한 병력·진찰·검사를 제안하는 고전 구조다. 사람이 만든 지식베이스와 오래된 자료 환경을 현대 LLM 성능 기준선으로 직접 비교하지 않는다. |
| CliniCARE-Bench | [arXiv v1 원문](https://arxiv.org/abs/2608.07796) | arXiv 공식 페이지에서 저자 코드·자료 링크 미확인 | 환자 기록과 정책 도구, 보류, 과정 위반과 비용을 함께 평가하는 하네스가 논문에 설명됐다. MIMIC-IV 접근 조건과 비공개 평가 구성요소 때문에 원문만으로 전체 환경을 다시 만들 수 없다. |
| Should we be going MAD? | [ICML 2024 공식 원문](https://proceedings.mlr.press/v235/smit24a.html) | [공식 코드](https://github.com/instadeepai/DebateLLM) | 여러 검토 방식과 같은 문제 반복을 비교하는 일반 평가 코드가 공개됐다. 임상시험 매칭 연구가 아니며, 검토 방식 자체의 성능을 ClarifyTrial 성능으로 옮겨 말할 수 없다. |

## 4. 이 색인에서 성능 수치를 다루는 원칙

- 각 논문의 수치는 그 논문의 자료, 모델, 호출량과 정답 정의에서만 유효하다.
- 공개 코드가 있어도 현재 기본 브랜치가 논문 당시 실행과 같다는 뜻은 아니다.
- 코드를 공개하지 않은 연구의 보고 수치는 구조 선택의 참고 근거로만 사용한다.
- ClarifyTrial v5 구조는 아직 실행하지 않았다. 이 문서의 어떤 수치나 구조도
  ClarifyTrial의 현재 성능을 뜻하지 않는다.
- 과거 Solar 합성 데모 수치, 특히 84%를 v5 기준선이나 현재 성능으로 사용하지
  않는다.
