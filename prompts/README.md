# 에이전트 프롬프트

이 폴더에는 ClarifyTrial v5 실행 중 모델에 전달하는 역할별 지시문을 둔다. 각
지시문은 역할, 입력, 허용 도구, 반드시 지킬 규칙과 출력의 같은 순서로 작성한다.
연구자는 파일과 출력 자료형을 함께 읽어 모델이 받은 정보와 맡은 판단 범위를 확인할
수 있다.

| 파일 | 맡은 일 | 출력 |
|---|---|---|
| [coordinator.md](coordinator.md) | 현재 상태와 남은 횟수를 보고 다음 단계 하나를 선택 | `CoordinatorDecision` |
| [matcher_judge.md](matcher_judge.md) | 환자 사실과 한 시험의 관련 조건 묶음을 연결해 조건 상태와 자료 충분성을 판단 | `CriterionAssessmentBatch` |
| [next_evidence.md](next_evidence.md) | 부족한 사실 하나와 확인 경로 하나를 선택 | `AgentAction` |
| [selective_reviewer.md](selective_reviewer.md) | 표시된 중요 결론을 원문으로 독립 검사 | `ReviewDecision` |
| [trialgpt_criterion_judge.md](trialgpt_criterion_judge.md) | TrialGPT 예비실행에서 한 환자-시험 조합의 선정 또는 제외 조건을 묶어 판정 | `TrialGPTPredictionBatch` |
| [trialgpt_criterion_judge_faithful.md](trialgpt_criterion_judge_faithful.md) | 공식 TrialGPT의 조건 판단 순서와 기록 부재 처리 원칙을 재현 | 요청에 지정된 구조화 자료형 |
| [trialgpt_criterion_judge_calibrated.md](trialgpt_criterion_judge_calibrated.md) | 실제 정보 부족과 기록 부재 추론을 네 단계 기준으로 구분 | 요청에 지정된 구조화 자료형 |
| [trialgpt_criterion_judge_balanced.md](trialgpt_criterion_judge_balanced.md) | 제외 조건의 기록 부재와 선정 조건의 미확인을 비대칭으로 처리하고 수치·기간 예외를 보존 | 요청에 지정된 구조화 자료형 |
| [trialgpt_criterion_reviewer.md](trialgpt_criterion_reviewer.md) | 최초 정보 부족 판정만 기존 근거로 제한 재검토 | 요청에 지정된 구조화 자료형 |
| [trialgpt_architecture_single.md](trialgpt_architecture_single.md) | Sol 구조 비교의 강한 단일 판단 | `ArchitectureSingleResponse` |
| [trialgpt_architecture_matcher_judge_v2.md](trialgpt_architecture_matcher_judge_v2.md) | Sol 구조 비교에서 근거와 조건 상태 판단 | `ArchitectureMatcherResponse` |
| [trialgpt_architecture_reviewer_v2.md](trialgpt_architecture_reviewer_v2.md) | 최초 정보 부족 조건만 다시 판단 | `ArchitectureReviewerResponse` |
| [trialgpt_strong_single_v1.md](trialgpt_strong_single_v1.md) | 선정·제외를 나누어 가장 강한 규칙으로 한 번 판단 | `ArchitectureMatcherResponse` |
| [trialgpt_strong_reviewer_no_web_v1.md](trialgpt_strong_reviewer_no_web_v1.md) | 강한 단일 판단의 정보 부족 결과만 같은 자료로 재검토 | `ArchitectureReviewerResponse` |
| [trialgpt_strong_reviewer_web_v1.md](trialgpt_strong_reviewer_web_v1.md) | 일반 의학 개념 검색을 허용해 같은 결과를 재검토 | `ArchitectureReviewerResponse` |
| [trialgpt_strong_reviewer_no_web_v2.md](trialgpt_strong_reviewer_no_web_v2.md) | 최초 판단이 경계 사례로 표시한 정보 부족만 집중 재검토 | `ArchitectureReviewerResponse` |
| [trialgpt_strong_reviewer_web_v2.md](trialgpt_strong_reviewer_web_v2.md) | 같은 경계 사례에서 일반 의학 검색을 실제 사용해 재검토 | `ArchitectureReviewerResponse` |

## 같은 모델을 사용할 때의 기록 분리

네 역할이 같은 모델을 사용하더라도 하나의 대화방을 이어 쓰지 않는다. 호출마다 해당
역할의 지시문과 필요한 구조화 입력만 새로 전달한다. 다른 역할의 대화 기록, 자유
형식 설명과 숨은 사고 과정은 다음 역할의 입력이 아니다.

환자 사실, 후보 목록, 조건 판단, 부족한 정보, 남은 행동 횟수와 검토 결과는 모델의
대화 기억이 아니라 코드가 관리하는 공통 상태에 저장한다. 진행 관리는 그 상태의
요약만 보고 다음 단계를 고른다. 따라서 같은 모델을 여러 번 호출해도 각 판단의 입력,
출력과 책임 범위를 실행 기록에서 따로 확인할 수 있다.

## 공통 원칙

- 모델은 입력에 없는 환자 사실이나 근거 식별자를 만들지 않는다.
- 최종 후보 유지와 현재 확인은 조건별 출력에서 코드가 집계한다.
- 날짜·수치·단위와 행동 횟수는 코드가 검사한다.
- 합성 사례의 숨은 답과 평가 정답은 어떤 프롬프트에도 전달하지 않는다.
- 출력은 지정된 자료형만 사용하며 객체 밖의 자유 형식 설명은 저장하지 않는다.
- 역할을 인격처럼 설정하거나 에이전트 사이의 자유 토론을 요구하지 않는다.
- 내부 사고 과정을 요구하거나 실행 기록에 저장하지 않는다.
