# ClarifyTrial 설계의 근거문헌과 공개 코드

확인일: 2026-08-25

논문마다 실제로 사용한 흐름, 공개 범위와 ClarifyTrial에 가져온 부분을 구분한다. 공개
코드를 찾지 못한 연구는 설명된 구조까지만 참고하며 동일한 구현을 재현했다고 표현하지
않는다.

## 1. 현재 구조에 직접 연결된 연구

### PRomop

- **논문이 한 일:** 장기간 환자 기록을 OMOP와 FHIR 형식에서 읽어 날짜, 출처와 상태가
  붙은 하나의 환자 기록으로 정리했다. 여러 판단 단계가 이 공통 기록을 사용했다.
- **구조:** 원자료 변환 → 환자 상태 계산 → 여러 판정 단계가 같은 상태 사용
- **가져온 부분:** 모델 대화 기억 대신 날짜와 출처가 붙은 환자 상태표를 코드가 관리
- **원문과 코드:** [논문](https://arxiv.org/abs/2607.13947),
  [공식 코드](https://github.com/healthkey-ai/PRomop)

### TrialMatchAI

- **논문이 한 일:** 임상 개념 정리, 규칙 필터, 같은 단어와 의학적 의미 검색, 관련
  조건 재정렬, 선정·제외 조건 판단과 시험 순위화를 연결했다.
- **구조:** 환자 개념 정리 → 후보 검색 → 관련 조건을 위로 정렬 → 조건별 판단 → 순위
- **가져온 부분:** 후보 검색과 조건별 판단을 분리하면서 원문 조건을 최종 근거로 사용
- **원문과 코드:** [논문과 보충자료](https://www.nature.com/articles/s41467-026-70509-w),
  [공식 코드](https://github.com/cbib/TrialMatchAI),
  [논문 당시 v0.01](https://github.com/cbib/TrialMatchAI/tree/v0.01)

### TrialGPT

- **논문이 한 일:** 검색어 생성, 같은 단어 검색, 의학적 의미 검색, 두 검색 순위 결합,
  환자–조건 판단과 근거 생성, 시험 순위화를 구현했다.
- **구조:** 환자 기록 → 검색어 → 후보 시험 → 조건별 판단 → 시험 점수
- **가져온 부분:** 검색 최소 기준, 질문 전 한 번 판단하는 비교 방식, 조건별 근거 형식
- **원문과 코드:** [논문](https://www.nature.com/articles/s41467-024-53081-z),
  [공식 코드](https://github.com/ncbi-nlp/TrialGPT),
  [전문가 조건 판단 1,015건](https://huggingface.co/datasets/ncbi/TrialGPT-Criterion-Annotations)
- **경계:** 공개 조건 자료에는 질문, 답변 뒤 변화와 종료 시점 정답이 없다.

### CLEAR-MATCH

- **연구가 한 일:** 환자 답변을 표준화하고 규칙 검색과 의미 검색을 결합해 후보 시험을
  갱신했다. 대화를 기본 정보, 상세 조건, 마지막 확인 단계로 나눴다.
- **구조:** 단계별 질문 → 답변 정리 → 후보 갱신 → 다음 대화 단계
- **가져온 부분:** 답변마다 후보와 현재 대화 목적을 갱신하는 큰 흐름
- **공개 자료:** [AMIA 공식 발표](https://amia.secure-platform.com/symposium/gallery/rounds/82021/details/20567)
- **경계:** 전체 코드, 대규모 평가자료와 질문 점수 계산식은 공개 위치를 확인하지 못했다.

### DQueST

- **논문이 한 일:** 여러 후보 시험에 공통으로 필요한 조건을 먼저 질문하고, 답변과
  맞지 않는 시험을 제거한 뒤 다음 질문을 다시 골랐다.
- **구조:** 후보 시험 묶음 → 영향이 큰 조건 선택 → 질문 → 후보 제거 → 반복
- **가져온 부분:** 한 정보가 영향을 주는 시험과 미해결 조건 수를 질문 순서에 반영
- **원문과 코드:** [논문](https://academic.oup.com/jamia/article/26/11/1333/5544734),
  [저자 공개 코드](https://github.com/stormliucong/dquest-flask)

### Yang, 2026

- **연구가 한 일:** 비슷한 시험 조건을 묶어 환자가 이해할 질문을 만들고, 답변에 따라
  맞지 않는 시험을 대화 중 줄였다.
- **구조:** 조건 묶기 → 환자용 질문 → 답변 판단 → 후보 갱신
- **가져온 부분:** 질문, 답변과 동적 후보 갱신이 이어지는 가장 가까운 비교 흐름
- **공개 자료:** [UTHealth Houston 학위 연구 소개](https://sbmi.uth.edu/research/phd-dissertations/a-patient-centric-chatbot-for-improving-clinical-trial-accessibility.htm)
- **경계:** 전체 학위논문, 코드, 세부 질문 순서와 종료 규칙의 공개 위치를 확인하지 못했다.

### TrialTriage, 2026

- **논문이 한 일:** 빠지거나 불확실한 시험 정보를 연구진에게 확인하고 답을 반영해
  선별을 다시 실행했다.
- **구조:** 불확실한 정보 발견 → 외부 확인 요청 → 답 반영 → 다시 선별
- **가져온 부분:** 답을 얻지 못했을 때 다른 확인으로 이동하고 새 답과 관련된 판단을
  다시 실행하는 원칙
- **원문:** [ScienceDirect 논문](https://www.sciencedirect.com/org/science/article/pii/S2561326X26005871)

### MediQ

- **논문이 한 일:** 현재 정보로 답할 수 있는지 먼저 판단하고, 부족할 때 질문을 만들었다.
  질문 시스템과 숨겨 둔 전체 기록에서 답을 반환하는 환경을 분리했다.
- **구조:** 정보 충분성 판단 → 질문 생성 → 독립 답변 환경 → 다시 판단
- **가져온 부분:** 정보가 충분할 때 불필요한 질문을 만들지 않는 규칙과 독립 합성 답변기
- **원문과 코드:** [NeurIPS 2024 논문](https://proceedings.neurips.cc/paper_files/paper/2024/file/32b80425554e081204e5988ab1c97e9a-Paper-Conference.pdf),
  [공식 코드](https://github.com/stellalisy/mediQ)

### Fink 계열

- **논문이 한 일:** 검사와 질문의 비용, 영향을 받는 시험 수와 조건 수를 함께 고려해
  다음 확인을 골랐다. 새 결과가 들어오면 시험 판단을 갱신했다.
- **구조:** 미확인 조건 → 가능한 검사와 비용 → 영향 비교 → 검사 → 다시 계산
- **가져온 부분:** 영향이 비슷하면 환자에게 새로 생기는 부담과 대기가 낮은 경로 선택
- **원문:** [비용 효율적 배정](https://www.cs.cmu.edu/~eugene/research/full/trial-assignment.pdf),
  [상세 지식 입력](https://www.cs.cmu.edu/~eugene/research/full/trial-selection.pdf)
- **경계:** 공개된 수치 가중치를 그대로 사용하지 않는다. ClarifyTrial의 부담값은 합성
  구조 평가용이다.

### TRIAGE

- **논문이 한 일:** 기준일 이전의 장기 환자 기록과 전체 시험 원문을 사용해 조건별
  판단과 원문 근거를 만들고, 임상시험 코디네이터의 재검토와 비교했다.
- **구조:** 환자 기록과 시험 원문 → 조건별 판단·근거 → 코디네이터 재검토
- **가져온 부분:** 중요한 결론에서 환자 근거, 시험 원문, 기준일과 설명을 함께 대조
- **원문:** [JCO Oncology Practice](https://doi.org/10.1200/OP-26-00076)
- **경계:** 정확한 모델, 검색 설정, 프롬프트와 평가 코드는 공개 위치를 확인하지 못했다.
  ClarifyTrial의 선택 검토는 별도 설계다.

### 환자 부담과 참여 선호 연구

- **연구가 한 일:** 연구 기간, 시간, 이동 장소, 보상, 부작용과 기존 치료 경험이 참여
  선택에 미치는 영향을 조사했다. 장기 시험 참여자에게는 직장을 비우는 시간, MRI와
  장시간 검체 수집도 부담이었다.
- **가져온 부분:** 같은 검사라도 이동, 경제 상황, 시간 긴급성과 이미 예정된 진료에
  따라 추가 부담을 다르게 기록
- **원문:** [Thomas et al., 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC9756590/),
  [장기 시험 참여자 경험, 2023](https://pmc.ncbi.nlm.nih.gov/articles/PMC10432794/)

## 2. 바꿔 끼울 수 있는 조건 판단 방법

### EXACT

- **한 일:** 구조화한 환자 속성과 시험 조건을 비교해 통과, 실패와 자료 부족을 계산하고
  시험을 참가 가능, 가능성 있음, 참가 불가로 정리한다.
- **사용 가능 위치:** 구조화 규칙이 많은 질환에서 언어모델 조건 판단을 대신하는 방법
- **공개 자료:** [AMIA 2026 시연](https://amia.secure-platform.com/amplify/gallery/rounds/82026/details/26163),
  [공식 코드](https://github.com/healthkey-ai/exact)

### Chen et al., 2025

- **한 일:** 임상시험 조건을 환자용 설문으로 바꾸고 환자 답변으로 적합성을 판단했다.
- **사용 가능 위치:** 코드가 고른 확인 내용을 환자가 이해할 질문으로 바꾸는 역할
- **공개 자료:** [Scientific Reports 논문과 보충자료](https://www.nature.com/articles/s41598-025-11876-0)

## 3. 구현과 후속 평가에 참고한 연구

### OncoAgents

환자 정보 추출, 표준화, 판정 역할, 종양 지식 그래프, 시간 규칙과 사람 검토를 연결했다.
시간, 부정, 단위 처리와 새 기록 뒤 재선별을 참고했다.

- [논문](https://pmc.ncbi.nlm.nih.gov/articles/PMC13091143/)

### MAKAR

여러 역할이 임상시험 조건을 설명하고 원문과 맞는지 다시 확인했다. 역할별 지시문과
원문 보존 검사를 참고했다. 자유 토론 구조는 현재 기본 설계에 넣지 않았다.

- [arXiv 원문](https://arxiv.org/abs/2411.14637)

### MDAgents, MedAgents와 MCC

문제 난이도나 첫 판단의 불일치에 따라 여러 의료 역할을 모아 검토, 수정 또는 다수결을
수행한다. 반복 토론이 실제 오류를 줄이는지 비교할 때 사용할 수 있다. 현재 구조화 조건
평가에서는 모델 호출 증가가 이득을 주지 않아 중심 흐름에서 제외했다.

- [MDAgents 논문](https://proceedings.neurips.cc/paper_files/paper/2024/file/90d1fc07f46e31387978b88e7e057a31-Paper-Conference.pdf) ·
  [코드](https://github.com/mitmedialab/MDAgents)
- [MedAgents 논문](https://aclanthology.org/2024.findings-acl.33/) ·
  [코드](https://github.com/gersteinlab/MedAgents)
- [MCC 논문](https://pmc.ncbi.nlm.nih.gov/articles/PMC12866169/) ·
  [코드](https://github.com/sunxinti/MCC)

### CliniCARE-Bench

환자 기록 도구, 정책 근거, 판단 보류, 필수·금지 절차와 자원 사용을 함께 평가한다.
최종 답뿐 아니라 근거 사용, 허용 행동, 보류와 비용도 평가해야 한다는 근거로 사용했다.

- [arXiv v1](https://arxiv.org/abs/2608.07796)

### TrialSim-10k

26개 질환 분야에서 임상시험 사전 선별을 위한 9,864개 다회 대화를 제공한다. 대규모
합성 대화와 논리 평가가 이미 존재하므로, 합성 대화 자체를 새 기여로 주장하지 않는다.

- [논문](https://www.sciencedirect.com/science/article/pii/S2352648326000504)

### AIDS2와 반복 토론 비교

AIDS2는 직접 확인한 사실과 의학적으로 추정한 상태를 나누고 부족한 병력, 진찰과 검사를
제안했다. *Should we be going MAD?*는 여러 토론 방식과 독립 반복 답변을 비교해 토론이
항상 이득을 주는 것은 아님을 살폈다.

- [AIDS2](https://pmc.ncbi.nlm.nih.gov/articles/PMC2248545/)
- [토론 비교 논문](https://proceedings.mlr.press/v235/smit24a.html)
- [DebateLLM 코드](https://github.com/instadeepai/DebateLLM)

## 4. 근거 사용 원칙

- 논문 수치는 해당 논문의 자료, 모델, 정답 정의와 호출량 안에서만 해석한다.
- 공식 코드 실행, 공개 설명을 바탕으로 한 독립 구현과 ClarifyTrial 자체 설계를 구분한다.
- 공개 코드가 계속 바뀌면 논문이 지정한 태그나 버전을 우선한다.
- 코드나 지시문이 공개되지 않은 연구는 확인된 구조까지만 참고한다.
- 질문 생성, 외부 검색, 여러 모델 역할과 재판정은 ClarifyTrial만의 새 기능으로 표현하지
  않는다.
- 과거 Solar 합성 데모와 84% 수치는 v5 결과에 사용하지 않는다.
