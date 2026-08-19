# ClarifyTrial 내부 문서

현재 사용하는 문서는 여덟 개다. 연구 계획은 두 번째 문서 하나뿐이며, 나머지는
현재 상태·자료·구현·설명을 위한 보조 문서다.

1. [CURRENT_STATUS.md](CURRENT_STATUS.md)
   - 현재 요구사항, 완료 상태, 다음 구현, 검증 결과와 아직 결정하지 않은 항목
2. [CLARIFYTRIAL_RESEARCH_PLAN_V5.md](CLARIFYTRIAL_RESEARCH_PLAN_V5.md)
   - 현재 연구 기준: 문제 정의, 연구 기여 후보, 자료 구성, 비교 실험, 평가 지표와 실행 순서
3. [CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md](CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md)
   - 시험 조건 준비부터 환자 상태표, 검색·판정, 대화, 근거 검사, 다음 확인과 두 판단까지 이어지는 성능 중심 구현 구조
4. [CLARIFYTRIAL_DATASETS.md](CLARIFYTRIAL_DATASETS.md)
   - 공개 자료의 실제 환자·시험·라벨 구조, v5에서 쓸 부분, 합성 자료 제작과 정답 검토 계획
5. [RESEARCH_KNOWLEDGE_BASE.md](RESEARCH_KNOWLEDGE_BASE.md)
   - 선행연구, 데이터의 용도, 가져올 구현 방법, 비교군과 평가 지식
6. [CLARIFYTRIAL_AGENT_SOURCE_INDEX.md](CLARIFYTRIAL_AGENT_SOURCE_INDEX.md)
   - 에이전트 구조 조사에 사용한 원 논문, 공식 발표 자료, 공개 코드와 재현 범위
7. [CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md](CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md)
   - 기본 방식, 추가한 두 판단, 사례 제작부터 비교·해석까지의 순서를 쉬운 한국어로 풀어 쓴 설명
8. [CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md](CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md)
   - 공통 RAG 자료, 검색 구조, 평가 입출력, 합성 답변 환경, 비교 시스템 연결과 실제 구현 순서

연구 결정은 CLARIFYTRIAL_RESEARCH_PLAN_V5.md를 따른다. 현재 상태 문서는 실제
구현과 다음 작업을 관리한다. 데이터셋·에이전트 설계·RAG와 평가 구현계획·연구
지식 문서는 연구계획을 바꾸지 않고 세부 선택을 구체화한다. 현재는 v5 실험 준비
단계이며 성능표는 비어 있다.
