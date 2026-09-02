# ClarifyTrial 문서

현행 실험 수치와 해석 범위는 [검증 결과](CLARIFYTRIAL_VALIDATION_RESULTS.md)에 모여
있다. 개발 과정에서 대체된 내용은 `trash`에 보관돼 있으며 현재 설계의 근거로 쓰지
않는다.

## 연구와 현재 결과

| 문서 | 내용 |
|---|---|
| [현재 상태](CURRENT_STATUS.md) | 구현된 범위, 최신 결과와 남은 일 |
| [연구계획 v5](CLARIFYTRIAL_RESEARCH_PLAN_V5.md) | 연구 질문, 실험 방법과 결과 해석 기준 |
| [검증 결과](CLARIFYTRIAL_VALIDATION_RESULTS.md) | 자료 규모, 실행 조건, 수치와 해석 범위 |
| [발표 구성과 연구 요약](CLARIFYTRIAL_REPORT_PRESENTATION_PACKET.md) | 발표 흐름, 화면 구성, 발표 대본과 질의응답 |
| [KOSMI 포스터 논문 원고](CLARIFYTRIAL_KOSMI_POSTER_MANUSCRIPT.md) | 2쪽 제출용 본문, 250단어 이하 초록과 표·그림 문구 |

## 프로그램 구조

| 문서 | 내용 |
|---|---|
| [프로그램 설계](CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md) | 코드와 언어모델의 역할, 한 사례의 실행 순서와 실패 처리 |
| [시험 검색 구현](CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md) | 공개 임상시험 검색, 순위 계산과 검색 평가 |
| [실행과 실험 안내](CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md) | 예제 실행, 일괄 평가, 결과표와 그림 생성 명령 |

## 자료와 선행연구

| 문서 | 내용 |
|---|---|
| [실험자료](CLARIFYTRIAL_DATASETS.md) | 공개 시험, 합성 환자와 통제 실험의 구성 |
| [자료 출처](../../DATA_SOURCES.md) | 공개 주소, 받은 날짜, 이용 조건과 저장 위치 |
| [선행연구](RESEARCH_KNOWLEDGE_BASE.md) | 이미 알려진 방법, 가져온 부분과 남은 연구 경계 |
| [논문과 공개 코드](CLARIFYTRIAL_AGENT_SOURCE_INDEX.md) | 논문별 근거, 공개 코드와 현재 구조의 연결 |

## 결과 파일

| 위치 | 내용 |
|---|---|
| [발표용 결과표](results/presentation-evidence-v2/) | 질문 전후 변화, 정보 연결 구조, 환자 제한과 확인 방법 표 |
| [KOSMI 보완 측정](results/kosmi-poster-evidence-v1/) | 적격·부적격 방향별 확인 예산 결과와 환자 단위 비교 |
| [모델 역할 비교](results/independent-new-trial-agent-evaluation-v1/report.md) | 새 시험에서 코드와 언어모델 실행 결과, 호출 수와 토큰 |
| [발표용 대화형 데모](demo/clarifytrial-presentation-demo.html) | 상태 읽기, 다음 행동 결정, 확인 도구 실행, 상태 갱신과 반복·종료 판단을 잇는 합성 실행 |

## 용어

| 표현 | 뜻 |
|---|---|
| 구조화한 조건 | 수치, 날짜, 예·아니오와 논리 관계를 코드가 계산할 수 있게 나눈 참가 조건 |
| 후보 유지 | 현재 정보가 부족해도 참가 가능성이 남아 있어 계속 확인할 시험 |
| 확인 완료 | 현재 확보한 자료로 이번 사전 검토 범위의 조건을 확인한 상태 |
| 추가 확인 필요 | 후보로 남아 있지만 날짜, 출처, 검사나 병력 확인이 더 필요한 상태 |
| 확인 대기 | 허용된 질문 횟수를 썼거나 현재 이용할 수 있는 방법이 없어 답을 기다리는 상태 |
| 외부 언어모델 | GPT나 Claude처럼 문장을 해석하고 생성하는 별도 모델 |

개발 과정의 긴 기록과 대체된 문서는 [보관 자료](trash/)에서 확인할 수 있다.
