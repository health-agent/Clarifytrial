# ClarifyTrial Demo Script

## 60초 설명

> ClarifyTrial은 임상시험 코디네이터와 임상의를 위한 대화형 임상시험 추천
> 멀티에이전트 시스템입니다. 환자 임상요약과 실제 임상시험 기준을 구조화하고,
> 후보 trial을 criterion 단위로 판단한 뒤, 여러 trial에 공통으로 부족한 정보를
> 하나로 합칩니다. 모든 정보를 묻지 않고 최대 3회 안에서 추천을 가장 크게
> 개선할 질문이나 조회를 선택하며, 답변이 들어오면 관련 criterion만 다시
> 평가합니다. 최종적으로 추천 순위, 기준별 근거, 남은 불확실성과 설명을
> 제공합니다. Fixed-input, Ask-all과 비교해 정확도뿐 아니라 질문 수, unknown
> 해소율과 API 비용까지 함께 평가합니다.

## 3분 데모 순서

1. **연구 목표 30초:** `docs/assets/clarifytrial-research-plan.png`에서 한 문장 목표와 5단계 agent 흐름을 설명한다.
2. **실행 흐름 45초:** `docs/assets/clarifytrial-workflow.png`에서 `PatientSession`, 공통 부족정보, 질문, 답변, 표적 재평가와 최종 결과를 설명한다.
3. **결정 규칙 30초:** `rules.py`에서 hard block, unknown, human review와 추천 우선순위를 보여준다.
4. **현재 데모 45초:** `python scripts/run_end_to_end_demo.py`를 실행해 합성 환자, 두 trial, 공통 질문과 추천 결과를 보여준다.
5. **평가 계획 20초:** Fixed-input, Ask-all, ClarifyTrial과 criterion·ranking·질문 수·비용 지표를 설명한다.
6. **구현 계획 10초:** 데이터, 기준선, Solar, 질문 실험, 오케스트레이션, 최종 평가의 6주 순서를 보여준다.

## 저장소에서 먼저 보여줄 파일

1. `docs/project-overview-ko.md` — 목표, agent 구성, 실험, API 예산과 일정
2. `docs/assets/clarifytrial-research-plan.png` — 연구 계획 한 장 요약
3. `docs/assets/clarifytrial-workflow.png` — 실제 agent 상태 흐름
4. `models.py`와 `rules.py` — 공유 상태와 결정 규칙
5. `tests/` — 102개 자동 검증

## 데모에서 강조할 네 가지

- 여러 trial의 판단을 하나의 `PatientSession`에서 관리한다.
- 같은 부족정보는 한 번만 확인한다.
- 최대 3회 안에서 가치가 높은 정보부터 얻는다.
- 답변과 관련된 criterion만 다시 평가한다.

## 현재 실행 명령

```bash
python -m pytest -q
python scripts/run_end_to_end_demo.py
python scripts/validate_synthetic_data.py
```

모든 환자 예시는 합성 데이터이며 최종 결과에는 의료적 면책 고지를 포함한다.
