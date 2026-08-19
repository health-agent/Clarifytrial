# ClarifyTrial 문서 안내

처음 읽을 때는 아래 네 문서면 충분하다.

1. [현재 상태](CURRENT_STATUS.md)
   - 완료한 설계, 구현 전 항목과 바로 시작할 작업
2. [에이전트 워크플로우](CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md)
   - 환자 입력부터 후보 검색, 두 판단, 다음 확인과 재판정까지의 전체 흐름
3. [근거문헌](CLARIFYTRIAL_AGENT_SOURCE_INDEX.md)
   - 각 단계의 원 논문, 공개 코드, 가져온 부분과 재현 범위
4. [RAG·평가 구현계획](CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md)
   - 자료 다운로드와 후처리, 검색기, 모델 교체, 공통 평가와 구현 순서

연구 질문과 데이터 정답을 확인할 때는 다음 문서를 읽는다.

| 문서 | 내용 |
|---|---|
| [현행 연구계획 v5](CLARIFYTRIAL_RESEARCH_PLAN_V5.md) | 연구 질문, 가설, 정답 구조, 비교 실험과 주장 범위 |
| [데이터셋 정리](CLARIFYTRIAL_DATASETS.md) | TrialGPT, TREC 등 공개자료의 실제 라벨과 새 합성 자료 계획 |
| [연구 지식 정리](RESEARCH_KNOWLEDGE_BASE.md) | 선행연구, 평가 지식과 구현 참고사항 |
| [쉬운 실험 설명](CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md) | 사례 제작부터 결과 해석까지의 전체 순서 |

연구계획은 v5 문서 하나를 기준으로 삼는다. 나머지 문서는 현재 상태, 구현, 자료와
근거를 설명한다. 에이전트 구조와 실행계획은 설계가 끝났으며, v5 코드와 성능
실험은 아직 시작하지 않았다.
