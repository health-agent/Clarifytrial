# ClarifyTrial

ClarifyTrial은 환자 정보가 아직 완전하지 않은 단계에서 임상시험 후보를 검토하는
연구 프로젝트다. 시스템은 다음 두 질문에 따로 답한다.

1. **이 환자를 후보로 계속 검토할 가치가 있는가?**
2. **현재 확보한 자료만으로 참가 조건을 확인할 수 있는가?**

가능성은 보이지만 최근 검사나 공식 확인이 남아 있다면 후보는 유지하고, 현재
상태는 `확인 대기`로 표시한다. 이어서 기존 기록 조회, 환자 질문, 공식 검사·의료진
확인, 보류 가운데 알맞은 경로를 고른다.

## 현재 상태

| 항목 | 상태 |
|---|---|
| 연구 질문과 실험 원칙 | 확정 |
| 에이전트 워크플로우와 근거문헌 | 설계 완료 |
| 공통 RAG와 평가 구현계획 | 설계 완료 |
| v5 실행 코드 | 구현 전 |
| v5 성능 | 미측정 |

저장소에는 합성 환자 사례만 둔다. 과거 Solar 합성 데모 수치와 84% 결과는 v5의
성능이나 기준선에 포함하지 않는다.

## 전체 워크플로우

![ClarifyTrial 임상시험 사전 선별 흐름](docs/internal/diagrams/clarifytrial-workflow.svg)

[수정 가능한 Mermaid 원본](docs/internal/diagrams/clarifytrial-performance-agent-architecture.mmd)

### 단계별 역할

| 단계 | 하는 일 | 주로 참고한 연구 |
|---|---|---|
| 시험 조건 준비 | 조건, 수치, 기간, 논리와 원문 위치를 미리 정리 | TrialMatchAI, EXACT |
| 환자 상태표 | 여러 기록을 날짜와 출처가 붙은 한 상태표로 정리 | PRomop |
| 후보 검색 | 명확한 규칙 필터와 글자·의미 검색을 함께 사용 | TrialMatchAI |
| 조건별 판단 | 선정 조건과 제외 조건을 나누고 양쪽 원문 근거를 연결 | TrialMatchAI, TrialGPT |
| 근거 확인 | 판정이 환자 원문, 시험 원문과 기준일에 맞는지 점검 | TRIAGE |
| 대화와 후보 갱신 | 새 답변이 들어올 때마다 후보와 미해결 조건을 갱신 | CLEAR-MATCH, Yang |
| 정보 충분성 판단 | 지금 멈출지, 정보를 더 확인할지 먼저 결정 | MediQ |
| 다음 확인 선택 | 중요한 조건을 많이 해결하면서 부담이 낮은 확인을 우선 | DQueST, Fink |
| 두 판단과 확인 경로 | 후보 유지와 현재 확인을 분리하고 정보 획득 경로를 선택 | ClarifyTrial v5 |

상세한 역할, 입력과 출력은
[에이전트 워크플로우](docs/internal/CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md)에
정리했다. 논문과 공개 코드 링크는
[근거문헌 색인](docs/internal/CLARIFYTRIAL_AGENT_SOURCE_INDEX.md)에서 확인할 수 있다.

## 주요 근거문헌

| 연구 | ClarifyTrial에서 참고하는 부분 | 공개 자료 |
|---|---|---|
| PRomop | 여러 기록을 하나의 환자 상태표로 정리 | [논문](https://arxiv.org/abs/2607.13947), [코드](https://github.com/healthkey-ai/PRomop) |
| TrialMatchAI | 규칙 필터, 글자·의미 검색, 조건 재정렬과 조건별 판정 | [논문](https://www.nature.com/articles/s41467-026-70509-w), [코드](https://github.com/cbib/TrialMatchAI) |
| CLEAR-MATCH | 대화 단계와 답변 뒤 후보 갱신 | [공식 발표](https://amia.secure-platform.com/symposium/gallery/rounds/82021/details/20567) |
| TRIAGE | 환자 원문·시험 원문·기준일을 이용한 판정 확인 | [논문](https://doi.org/10.1200/OP-26-00076) |
| Yang, 2026 | 질문, 답변 판정과 동적 후보 제거를 잇는 전체 대화 흐름 | [학위 연구 소개](https://sbmi.uth.edu/research/phd-dissertations/a-patient-centric-chatbot-for-improving-clinical-trial-accessibility.htm) |
| MediQ | 현재 정보로 답할지 먼저 판단한 뒤 부족할 때 질문 | [논문](https://proceedings.neurips.cc/paper_files/paper/2024/file/32b80425554e081204e5988ab1c97e9a-Paper-Conference.pdf), [코드](https://github.com/stellalisy/mediQ) |
| DQueST | 여러 남은 시험에 공통으로 필요한 질문을 우선 | [논문](https://academic.oup.com/jamia/article/26/11/1333/5544734), [코드](https://github.com/stormliucong/dquest-flask) |
| Fink 계열 | 검사·질문의 비용과 여러 시험에 미치는 범위를 함께 고려 | [논문](https://www.cs.cmu.edu/~eugene/research/full/trial-selection.pdf) |
| TrialGPT | 공통 검색 구조와 전문가가 검토한 조건별 평가자료 | [논문](https://www.nature.com/articles/s41467-024-53081-z), [코드](https://github.com/ncbi-nlp/TrialGPT), [주석 자료](https://huggingface.co/datasets/ncbi/TrialGPT-Criterion-Annotations) |

위 기능은 이미 선행연구에 존재한다. ClarifyTrial v5는 같은 환자 사실에서도
자료의 날짜·출처·확인 절차에 따라 **후보 유지**와 **현재 확인**이 달라지는지를
별도 정답으로 평가한다.

## RAG와 모델

첫 RAG는 두 가지 검색을 담당한다.

1. 환자에게 관련 있는 임상시험 찾기
2. 후보 시험에서 판단에 필요한 조건 원문 찾기

환자 기록 확인은 통제된 기록 조회 기능으로 처리한다. 합성 환자의 숨은 정보와
평가 정답은 검색 색인에서 분리한다.

주 비교의 기본 모델은 Claude Sonnet 5 `medium` 설정이다. 모든 비교 구조에 같은
모델을 사용한다. Claude Opus 4.8은 대표 사례의 성능 상한 확인, Solar Pro 4는
저비용 작동 확인에 사용한다. 모델 교체는 공통 어댑터 설정으로 처리한다.

## 평가

목적이 다른 세 평가를 따로 보고한다.

| 평가 | 자료 | 확인할 내용 |
|---|---|---|
| 조건별 판단 | TrialGPT Criterion Annotations 1,015건 | 조건 상태와 환자 근거 선택 |
| 후보 검색 | TREC Clinical Trials 2021·2022 | 관련 시험 회수와 순위 |
| 대화와 다음 행동 | 새 v5 합성 흐름 자료 | 후보 유지, 현재 확인, 확인 경로와 재판정 |

논문 수치는 각 논문의 모델, 자료와 평가 방식 안에서 참고한다. 구조 비교는 같은
모델, 같은 검색 결과, 같은 행동 한도와 같은 합성 답변 환경에서 다시 실행한다.

## 구현 순서

1. 시험·조건·환자 사실·두 판단·다음 행동의 공통 자료형
2. 작은 조건 저장소와 BM25 검색
3. 숨은 합성 환자 상태와 행동 결과를 가진 통제 환경
4. 조건 판단, 후보 유지, 현재 확인과 다음 행동을 채점하는 평가기
5. 단일 모델과 공개 연구 구조를 옮긴 비교 시스템
6. ClarifyTrial v5 전체 흐름
7. TREC 전체 검색과 무거운 공식 코드 연결

구현 세부는
[RAG·평가 구현계획](docs/internal/CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md)을
따른다.

## 문서 안내

| 문서 | 용도 |
|---|---|
| [현재 상태](docs/internal/CURRENT_STATUS.md) | 완료한 일과 다음 구현 순서 |
| [에이전트 워크플로우](docs/internal/CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md) | 확정한 전체 구조와 단계별 역할 |
| [근거문헌 색인](docs/internal/CLARIFYTRIAL_AGENT_SOURCE_INDEX.md) | 원 논문, 공개 코드와 재현 범위 |
| [RAG·평가 구현계획](docs/internal/CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md) | 자료 확보, 검색, 모델 교체와 공통 평가 |
| [현행 연구계획 v5](docs/internal/CLARIFYTRIAL_RESEARCH_PLAN_V5.md) | 연구 질문, 가설과 실험 원칙 |
| [데이터셋 정리](docs/internal/CLARIFYTRIAL_DATASETS.md) | 공개 자료의 라벨과 새 합성 자료 계획 |
| [연구 지식 정리](docs/internal/RESEARCH_KNOWLEDGE_BASE.md) | 선행연구와 평가 지식 |

임상시험 추천 결과는 연구용 사전 검토 자료다. 실제 참가 가능 여부와 등록 결정은
해당 임상시험 연구진과 의료진이 확인한다. 자세한 내용은
[의료 면책 안내](MEDICAL_DISCLAIMER.md)를 따른다.
