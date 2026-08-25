# 언어모델 지시문

이 폴더에는 언어모델을 부를 때 사용하는 역할별 지시문이 있다. 정해진 형식의 JSON을
코드로 실행하는 기본 경로에서는 외부 모델을 부르지 않는다. 자유 문장 해석이나 코드로
계산할 수 없는 조건이 있을 때만 필요한 지시문을 사용한다.

지시문마다 다음 내용을 분명히 적는다.

- 모델이 맡은 일
- 모델이 받는 정보
- 사용할 수 있는 도구
- 바꾸면 안 되는 값과 근거
- 반환할 JSON 형식

표의 `출력 형식`은 코드에 정의된 JSON 구조의 이름이다.

## 1. 현재 전체 실행에서 선택적으로 쓰는 지시문

| 파일 | 모델이 맡는 일 | 출력 형식 |
|---|---|---|
| [patient_record_structurer.md](patient_record_structurer.md) | 자유 문장 환자 기록에서 검색 질환, 환자 사실과 근거 문구를 정리 | `PreparedPatientRecord` |
| [trial_protocol_structurer.md](trial_protocol_structurer.md) | 시험 원문에서 선정·제외 조건과 필요한 환자 사실을 정리 | `PreparedTrialProtocol` |
| [matcher_judge.md](matcher_judge.md) | 코드로 계산할 수 없는 조건의 상태와 현재 자료의 충분성을 판단 | `CriterionAssessmentBatch` |
| [next_evidence.md](next_evidence.md) | 코드가 고른 사실과 확인 방법을 사람이 읽을 질문이나 요청문으로 작성 | `AgentAction` |
| [selective_reviewer.md](selective_reviewer.md) | 실제 근거 충돌이나 구조화하지 못한 중요 조건을 원문과 대조 | `ReviewDecision` |

`next_evidence.md`는 질문 대상을 새로 고르는 지시문이 아니다. 코드가 이미 고른 사실과
방법을 자연스러운 문장으로 바꾸며, 다른 사실을 선택하면 출력이 거부된다.

## 2. 비교 실험에만 쓰는 지시문

| 파일 | 실험 목적 |
|---|---|
| [coordinator.md](coordinator.md) | 코드 진행 관리 대신 모델이 다음 단계를 고르는 비교 |
| [interactive_question_selector.md](interactive_question_selector.md) | 정보 선택 규칙 대신 모델이 다음 확인을 고르는 비교 |

현재 기본 실행에서는 진행 순서와 다음 정보를 코드가 고른다. 위 지시문은 모델 진행
관리의 비용과 결과를 비교할 때만 사용한다.

## 3. 평가자료 준비 지시문

| 파일 | 용도 |
|---|---|
| [natural_criterion_ai_review.md](natural_criterion_ai_review.md) | 공개 시험 원문에서 객관적으로 구조화할 수 있는 조건의 예비 초안 작성 |
| [natural_criterion_ai_audit.md](natural_criterion_ai_audit.md) | 초안을 원문과 대조해 방향, 수치와 복합 관계 재검사 |
| [natural_evaluation_record_extractor.md](natural_evaluation_record_extractor.md) | 합성 자유 문장 기록에서 값, 단위, 자료 출처와 확인 상태 읽기 |

이 지시문으로 만든 조건은 평가자료 제작 단계의 입력이다. 언어모델이 만든 답을 그대로
최종 성능 정답으로 사용하지 않는다.

## 4. TrialGPT 조건 판단 예비실험 지시문

다음 파일은 질문 전 한 번의 조건 판단과 반복 검토 방식을 비교한 과거 예비실험에
사용했다. 현재 전체 실행의 기본 지시문은 아니다.

| 파일 묶음 | 비교한 내용 |
|---|---|
| `trialgpt_criterion_judge*.md` | 기록 부재, 선정 조건과 제외 조건의 답 이름을 다르게 처리한 여러 판정 규칙 |
| `trialgpt_criterion_reviewer.md` | 첫 판단에서 정보 부족으로 남긴 조건의 제한된 재검토 |
| `trialgpt_architecture_*.md` | 단일 판단, 역할 분리와 검토 호출 구조 비교 |
| `trialgpt_strong_single_v1.md` | 개발 단계에서 가장 나았던 한 번 판단 규칙 |
| `trialgpt_strong_reviewer_no_web_*.md` | 외부 검색 없이 경계 사례 재검토 |
| `trialgpt_strong_reviewer_web_*.md` | 일반 의학 검색을 허용한 경계 사례 재검토 |

예비실험 결과는 [검증 결과](../docs/internal/CLARIFYTRIAL_VALIDATION_RESULTS.md)의
`질문 전 한 번의 조건 판단`에서 요약한다.

## 5. 역할 사이의 정보 전달

같은 모델을 여러 역할에 사용해도 하나의 대화를 이어 쓰지 않는다. 호출마다 그 역할에
필요한 지시문과 입력만 새로 보낸다. 다른 역할의 대화 내용이나 숨은 사고 과정은 다음
역할에 전달하지 않는다.

환자 사실, 후보 목록, 조건 판단, 부족 정보, 남은 확인 횟수와 검토 결과는 모델 기억이
아니라 코드가 관리하는 상태에 저장한다. 실행 기록에는 역할 이름, 입력 식별자, 구조화
출력, 호출 시간, 토큰과 오류를 따로 남긴다.

## 6. 모든 지시문이 지켜야 하는 규칙

- 입력에 없는 환자 사실이나 근거 식별자를 만들지 않는다.
- 합성 사례의 숨긴 답과 평가 정답을 모델 입력에 넣지 않는다.
- 날짜, 수치, 단위와 확인 횟수는 코드가 다시 검사한다.
- 후보 유지 여부와 현재 자료의 충분성은 조건별 결과에서 코드가 집계한다.
- 지정된 JSON 형식 밖의 설명은 실행 결과로 사용하지 않는다.
- 역할을 인격처럼 설정하거나 역할 사이의 자유 토론을 요구하지 않는다.
- 내부 사고 과정을 요구하거나 저장하지 않는다.
