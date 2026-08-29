# ClarifyTrial 문서 안내

문서는 역할에 따라 나뉜다. 같은 결과를 여러 파일에서 반복하지 않으며, 수치가
다르면 [검증 결과](CLARIFYTRIAL_VALIDATION_RESULTS.md)를 기준으로 삼는다.

## 처음 읽을 때

| 순서 | 문서 | 읽고 알 수 있는 것 |
|---:|---|---|
| 1 | [발표 구성과 연구 요약](CLARIFYTRIAL_REPORT_PRESENTATION_PACKET.md) | 핵심 주장, 10장 본편, 발표 대본과 질의응답 |
| 2 | [검증 결과](CLARIFYTRIAL_VALIDATION_RESULTS.md) | 각 수치가 어떤 자료와 조건에서 나왔는가 |
| 3 | [현재 상태](CURRENT_STATUS.md) | 지금 작동하는 범위와 아직 남은 일은 무엇인가 |

## 연구 설계와 프로그램 구조

| 문서 | 역할 |
|---|---|
| [현행 연구계획 v5](CLARIFYTRIAL_RESEARCH_PLAN_V5.md) | 연구 질문, 가설, 비교 방법과 결과 해석 기준 |
| [프로그램 설계](CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md) | 코드와 언어모델의 역할, 한 사례의 실행 순서와 실패 처리 |
| [시험 검색 구현](CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md) | 공개 임상시험을 찾고 순위를 매기는 방법과 검색 평가 |

## 자료와 근거문헌

| 문서 | 역할 |
|---|---|
| [실험자료 구성](CLARIFYTRIAL_DATASETS.md) | 자료마다 들어 있는 값과 정답, 가능한 평가와 불가능한 평가 |
| [자료 출처](../../DATA_SOURCES.md) | 공개 주소, 받은 날짜, 이용 조건과 저장 위치 |
| [선행연구 정리](RESEARCH_KNOWLEDGE_BASE.md) | 이미 알려진 방법, ClarifyTrial에 가져온 부분과 연구 경계 |
| [논문과 공개 코드 목록](CLARIFYTRIAL_AGENT_SOURCE_INDEX.md) | 논문별 핵심 근거, 공개 코드와 현재 설계의 연결 |

## 실행과 결과 재현

| 문서 | 역할 |
|---|---|
| [실행과 실험 안내](CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md) | 예제 실행, 일괄 평가, 결과 비교와 보고서 생성 명령 |
| [모델 호출 방식 비교 결과](results/independent-new-trial-agent-evaluation-v1/report.md) | 새 시험 최종 평가에서 세 실행 방식의 결과와 사용량 |

## 보관 자료

개발 과정의 긴 기록과 현재 계획에서 대체된 문서는 [trash](trash/)에 보관한다.
보관 자료는 출처나 과거 결정의 세부 내용을 확인할 때만 사용한다.

## 문서에서 쓰는 주요 표현

| 표현 | 뜻 |
|---|---|
| 구조화한 조건 | 수치, 날짜, 예·아니오와 논리 관계를 코드가 계산할 수 있도록 나눈 참가 조건 |
| 후보 유지 | 현재 정보가 부족해도 참가 가능성이 남아 있어 계속 확인할 시험 |
| 현재 확인 완료 | 지금 확보한 자료만으로 이번 사전 검토 범위의 조건을 확인한 상태 |
| 추가 확인 후보 | 후보로는 남지만 필요한 자료가 아직 있는 시험 |
| 참가 가능 후보 확인 | 합성 환자의 숨겨 둔 전체 상태에서 참가 가능한 시험을 질문 뒤 확인한 경우 |
| 제외 후보 정리 | 처음에는 정보 부족으로 남았지만 전체 상태에서는 부적합인 시험을 질문 뒤 제외한 경우 |
| 외부 언어모델 | GPT나 Claude처럼 문장을 해석하고 생성하는 별도 모델 |
