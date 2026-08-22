# ClarifyTrial 문서 안내

처음 보는 사람은 [저장소 README](../../README.md)부터 읽는다. 현재 구조, 사용한
자료, 예비결과와 실행 방법을 한 흐름으로 설명한다.

## 현행 문서

| 순서 | 문서 | 역할 |
|---:|---|---|
| 1 | [현재 상태](CURRENT_STATUS.md) | 활성 요구사항, 구현 상태, 현재 결과와 다음 작업 |
| 2 | [현행 연구계획 v5](CLARIFYTRIAL_RESEARCH_PLAN_V5.md) | 연구 질문, 가설, 비교 범위와 주장 기준 |
| 3 | [에이전트 워크플로우](CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md) | 세 고정 역할, 선택적 검토, 공통 상태와 호출 규칙 |
| 4 | [검증 결과](CLARIFYTRIAL_VALIDATION_RESULTS.md) | 실행 조건, 결과, 비용, 한계와 채택·기각 결정 |
| 5 | [데이터셋 정리](CLARIFYTRIAL_DATASETS.md) | TrialGPT·TREC·ClinicalTrials.gov와 합성자료의 실제 용도 |

필요할 때 다음 문서를 이어서 읽는다.

| 문서 | 내용 |
|---|---|
| [근거문헌과 공개 코드](CLARIFYTRIAL_AGENT_SOURCE_INDEX.md) | 각 단계의 원 논문, 공개 코드와 재현 범위 |
| [RAG·평가 구현계획](CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md) | 자료 준비, 검색기, 모델 연결, 평가 명령과 구현 기준 |
| [쉬운 실험 안내](CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md) | 사례 제작부터 결과 해석까지의 실행 순서 |
| [보고서·발표 정리본](CLARIFYTRIAL_REPORT_PRESENTATION_PACKET.md) | 전체 설명, 핵심 결과 그림, 대표 사례, 발표 문안과 주장 범위 |
| [연구 지식 정리](RESEARCH_KNOWLEDGE_BASE.md) | 선행연구, 데이터와 구현 방법을 꺼내 쓰는 참고자료 |

## 현재 상태 한눈에 보기

- 고정 역할은 진행 관리, 검색·판정, 다음 확인 세 개다.
- 중요한 제거·확정의 근거가 약하거나 충돌할 때만 선택적 검토를 한 번 부른다.
- 후보 검색은 TrialGPT의 BM25·MedCPT 결합을 TREC 2021·2022에서 재현했다.
- 여러 정보가 동시에 부족한 사례는 숨은 사실 5개와 확인 횟수 3회로 구현했다.
- 새 합성 평가 환자 30명에서 시험 상태 회복은 질문 없음 42%, 고정 순서 75%,
  현재 v3 89%였다.
- 기본 입력은 환자 상태와 시험 조건을 담은 규격화된 JSON이다. 자유 형식 기록을
  JSON으로 바꾸는 기능은 선택 연결 기능으로 둔다.
- 대표 합성 환자 한 명을 외부 모델 없이 다시 실행하는 명령과 보고서용 SVG 그림을
  저장소에 포함했다.
- 92개 시험 조건과 합성 환자를 사용한 예비결과이며 의사 일치도는 측정하지 않았다.

연구계획은 v5 문서 하나만 현행 기준으로 사용한다. `trash/`는 과거 기록이며 출처의
세부 확인이 필요할 때만 본다. 과거 Solar 합성 데모 수치와 84%는 현재 결과나
기준선으로 사용하지 않는다.
