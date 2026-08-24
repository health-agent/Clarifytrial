# ClarifyTrial 자료 구성

[저장소 README](../../README.md)에 핵심 아이디어, 전체 흐름, 사용한 자료, 현재 결과와
다음 평가 범위가 정리돼 있다.

## 연구 요약과 현재 결과

| 자료 | 내용 |
|---|---|
| [연구 요약](CLARIFYTRIAL_REPORT_PRESENTATION_PACKET.md) | 핵심 아이디어, 대표 사례와 결과 그림 |
| [현재 상태](CURRENT_STATUS.md) | 완성된 범위, 현재 자료와 다음 작업 |
| [검증 결과](CLARIFYTRIAL_VALIDATION_RESULTS.md) | 실험 조건, 결과, 비용과 한계 |

## 현행 연구 기준

| 문서 | 역할 |
|---|---|
| [현행 연구계획 v5](CLARIFYTRIAL_RESEARCH_PLAN_V5.md) | 연구 질문, 비교 범위와 주장 기준을 정하는 유일한 현행 계획 |
| [실험자료 구성](CLARIFYTRIAL_DATASETS.md) | TrialGPT·TREC·ClinicalTrials.gov와 합성자료의 실제 용도와 정답 구조 |
| [연구 지식 정리](RESEARCH_KNOWLEDGE_BASE.md) | 선행연구와 구현 방법 참고자료 |
| [근거문헌과 공개 코드](CLARIFYTRIAL_AGENT_SOURCE_INDEX.md) | 현재 구조에 영향을 준 논문, 공개 코드와 가져온 범위 |

현행 계획은 v5 연구계획 하나다. 검증 결과에는 실행한 사실을, 연구 지식 정리에는
선행연구를 보관한다.

## 현재 상태 한눈에 보기

- 후보로 계속 볼지와 현재 자료로 참가 조건을 확인할 수 있는지를 따로 판단한다.
- 여러 정보가 부족하면 남은 확인 횟수 안에서 가장 많은 시험 판단을 끝낼 정보를
  매번 다시 계산한다.
- 같은 정보라도 기존 기록, 환자 답변, 공식 결과, 새 검사 중 환자 부담이 적고 실제로
  이용할 수 있는 방법을 우선한다.
- 현재 정밀 평가는 3개 질환, 공개 시험 15건의 조건 92개와 별도 합성 환자 30명을
  사용한다.
- 세 번까지 확인한 전체 연결 평가에서는 시험 판단 150개 중 추가 질문 없이 63개,
  입력 순서대로 99개, 현재 영향 우선으로 112개, 현재 질문 선택 방법으로 115개 판단을
  끝냈다.
- 팀의 공개 시험 1,931건 변환과 모집 중·모집 예정 589건 필터, 10개 질환군·50개 정밀
  평가 후보 선택까지 끝났다. 다음 작업은 50건의 조건 정리와 새 합성 환자 제작이다.

과거 합성 데모 수치는 현재 결과나 비교 기준으로 사용하지 않는다.

## 구현과 재현

| 문서 | 내용 |
|---|---|
| [에이전트 실행 구조](CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md) | 역할별 모델 호출, 코드 실행 순서와 입출력 형식 |
| [관련 시험 검색·평가 구현계획](CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md) | 자료 다운로드, 검색기 준비, 평가 명령과 구현 기준 |
| [쉬운 실험 안내](CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md) | 사례 실행, 일괄 평가와 보고서 생성 명령 |
| [개발 상세 기록](CLARIFYTRIAL_IMPLEMENTATION_RECORD.md) | 완료·보류·기각된 요구사항 106개와 과거 결정 |

개발 기준은 [현재 상태](CURRENT_STATUS.md),
[현행 연구계획](CLARIFYTRIAL_RESEARCH_PLAN_V5.md)과 변경 대상에 해당하는 구현 문서다.
