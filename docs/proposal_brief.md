# ClarifyTrial 제안 요약

![ClarifyTrial 연구 계획](assets/clarifytrial-research-plan.png)

## 프로젝트명

**ClarifyTrial Agent: 공유 상태 기반 대화형 임상시험 추천 멀티에이전트 시스템**

## 해결하려는 문제

환자 임상요약에는 임상시험 적격성 판단에 필요한 정보가 자주 빠져 있다.
ClarifyTrial은 여러 trial의 부족정보를 통합하고, 추천 결과를 가장 크게 개선할
정보부터 최대 3회 확인한 뒤 관련 criterion만 다시 평가한다.

## 핵심 설계

- `PatientSession`에 환자, trial, criterion, 질문과 답변 상태를 통합한다.
- criterion을 근거와 함께 `met / unmet / unknown / conflict`로 판단한다.
- 여러 trial이 요구하는 같은 정보를 하나의 missing variable로 합친다.
- Next-Best-Action이 질문 또는 EHR 조회의 순서를 결정한다.
- 답변과 관련된 criterion만 표적 재평가한다.
- LLM은 문장 이해와 생성을 맡고 Python 규칙은 상태와 추천 결정을 관리한다.

## 에이전트 흐름

| 단계 | 역할 |
|---|---|
| 이해·검색 | 환자와 criteria 구조화, 후보 trial 3~5개 검색 |
| 근거 매칭 | criterion별 상태와 근거 생성 |
| 부족정보 제어 | 공통 missing variable과 다음 행동 선택 |
| 정보 반영 | 질문·EHR 조회, 답변 정규화, 표적 재평가 |
| 추천·설명 | 추천 순위, 근거, 불확실성, 설명 출력 |

## 실험과 평가

Fixed-input, Ask-all, ClarifyTrial을 같은 환자·후보·matcher에서 비교한다.
criterion macro-F1, missing-variable recall, unknown 해소율, nDCG@10,
평균 질문 수와 API 비용을 함께 측정한다.

## MVP 규모

- 합성 평가 세션 100개
- 환자당 후보 trial 3~5개
- 정보 획득 최대 3회
- 환자당 API 5~10회, 전체 1회 실행 약 800요청
- 대표 환자 3명의 최종 데모

## 현재 준비 상태와 산출물

공유 상태, 결정 규칙, agent contract, 합성 데이터, 오프라인 데모와 102개
테스트가 준비되어 있다. 최종 산출물은 실행 가능한 CLI, 100세션 결과 JSON,
criterion 근거와 질문·재평가 이력, 환자별 추천, 세 baseline 비교표, 재현 문서와
의료적 면책 고지다.
