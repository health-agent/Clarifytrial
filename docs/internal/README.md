# ClarifyTrial 문서

## 연구와 결과

| 문서 | 내용 |
|---|---|
| [연구 요약](CLARIFYTRIAL_REPORT_PRESENTATION_PACKET.md) | 핵심 아이디어, 사용한 자료와 발표용 결과 |
| [현재 상태](CURRENT_STATUS.md) | 완성된 범위, 활성 요구사항과 남은 작업 |
| [검증 결과](CLARIFYTRIAL_VALIDATION_RESULTS.md) | 각 수치의 실행 조건, 결과와 해석 범위 |
| [현행 연구계획 v5](CLARIFYTRIAL_RESEARCH_PLAN_V5.md) | 연구 질문, 비교 방법과 주장 기준 |
| [실험자료 구성](CLARIFYTRIAL_DATASETS.md) | 공개자료와 합성자료의 입력·정답·용도 |

현행 개발·실험 계획은 v5 연구계획 하나다. 현재 상태에는 요구사항과 구현 상태를,
검증 결과에는 실제 실행한 결과를 기록한다.

## 설계와 근거

| 문서 | 내용 |
|---|---|
| [에이전트 실행 구조](CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md) | 모델 역할, 코드 규칙, 복합 조건과 전체 실행 순서 |
| [선행연구 정리](RESEARCH_KNOWLEDGE_BASE.md) | 이미 알려진 방법, 가져온 근거와 남은 연구 경계 |
| [근거문헌과 공개 코드](CLARIFYTRIAL_AGENT_SOURCE_INDEX.md) | 논문, 공개 코드와 현재 구조에 사용한 범위 |
| [관련 시험 검색·평가 구현](CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md) | 자료 준비, 검색기와 평가 명령 |

## 현재 범위

- 모집 중·모집 예정 공개 시험 589건에서 후보를 검색한다.
- 10개 질환의 공개 시험 50건에서 구조화한 조건 202개를 정밀 평가에 사용한다.
- 원문에서 명확한 `모두`, `하나 이상`, `일정 개수 이상` 관계를 계산한다.
- 합성 환자 50명에게 정보 1개·2개·3개·5개를 가린 시작 상태와 답을 연결했다.
- 별도 평가 환자 30명에서는 추가 확인 세 번으로 실제 참가 가능 후보 47/54개를
  확정하고, 결국 제외될 후보 60/60개를 정리했다.
- 강한 단순 질문 방식과 결과가 같으므로 새 질문 알고리즘의 우월성을 주장하지 않는다.
- 외부 모델 30명 실행과 호출·토큰 측정은 보류 상태다.

과거 합성 데모 수치와 Solar 84%는 현재 결과나 기준선으로 사용하지 않는다.

## 구현과 재현

| 문서 | 내용 |
|---|---|
| [쉬운 실험 안내](CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md) | 사례 실행, 일괄 평가와 보고서 생성 명령 |
| [개발 상세 기록](CLARIFYTRIAL_IMPLEMENTATION_RECORD.md) | 완료·보류·기각된 과거 요구사항과 결정 |

코드 실행의 시작점은 저장소 [README](../../README.md)다. 외부 자료의 원본, 받은 날짜와
이용 조건은 [DATA_SOURCES.md](../../DATA_SOURCES.md)에 있다.
