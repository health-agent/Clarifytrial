# 자연어 평가 조건 검토 안내

이 폴더는 개발에 사용하지 않은 공개 임상시험 조건을 두 사람이 독립적으로 확인하기
위한 작업 묶음이다. 환자 자료는 없으며 ClinicalTrials.gov 공개 원문만 사용한다.

## 파일

| 파일 | 용도 |
|---|---|
| `criterion_review.json` | 코드가 읽는 원문 위치, 시험 정보와 검토 대기 상태 |
| `reviewer_1.csv` | 첫 번째 검토자가 작성할 표 |
| `reviewer_2.csv` | 두 번째 검토자가 작성할 표 |
| `ai_preliminary_review_polarity_audited.json` | 채택된 줄의 조건 방향까지 `max`로 다시 확인한 전체 결과 |
| `ai_preliminary_gold_conservative.json` | 복합 관계를 빼고 바로 계산할 수 있는 62조건 |
| `reserve_criterion_review.json` | 유방암·우울장애 예비 시험 10건의 원문 231줄 |
| `reserve_reviewer_1.csv`, `reserve_reviewer_2.csv` | 예비 시험 원문을 두 사람이 따로 확인할 표 |
| `ai_preliminary_reserve_review_polarity_audited.json` | 예비 시험 조건 방향까지 다시 확인한 AI 전체 결과 |
| `ai_preliminary_reserve_gold_conservative.json` | 예비 시험에서 바로 계산할 수 있는 50조건 |
| `preliminary_trial_set.json` | 조건이 부족한 본 시험 6건을 교체한 15건·92조건 |
| `preliminary_patient_pairs.json` | 합성 환자 30명의 근거 충분·불충분 짝 60회 |

두 CSV는 처음에는 내용이 같다. 검토자는 상대방 표를 보지 않고 자신의 파일만 작성한다.
자동 규칙이 객관적 조건일 가능성이 있다고 표시한 문구는 164개다. 이 규칙이 빠뜨린
조건까지 확인할 수 있도록 검토표에는 본 시험의 조건 원문 272줄을 모두 넣었다. 아직
두 CSV의 어느 행도 사람 두 명이 합의한 정답은 아니다.

## AI 예비 검토

사람 검토 전에 GPT-5.6 Sol로 예비 검토를 진행했다. 마지막 전체 결과는 채택 123줄,
제외 61줄, 보류 88줄이다. 높은 확신 조건 중에서도 한 원문에 여러 조건이 복잡하게
묶인 줄은 단순 규칙 파일에서 제외했다. 한 조건만 있거나 같은 수치의 하한·상한이
명확한 59줄에서 62조건을 남겼다.

이 결과는 합성 환자 제작을 위한 AI 단독 초안이다. 의사 정답, 사람 두 명의 합의
정답이나 ClinicalTrials.gov의 공식 해석이 아니다. 전체 실행 조건과 토큰은
`docs/internal/CLARIFYTRIAL_VALIDATION_RESULTS.md` 23절에 기록했다.
중간 두 단계의 상세 호출 기록과 초안은 Git에서 제외한 `runs`에 두고, 저장소에는
마지막 전체 검토와 그 검토에서 뽑은 단순 규칙만 남긴다.

### 예비 시험 교체와 합성 환자

본 시험 15건 중 단순 조건이 4줄보다 적은 6건을 같은 질환의 예비 시험과 비교했다.
유방암·우울장애 예비 시험 10건의 원문 231줄을 같은 방식으로 검토해 50조건을
남겼다. 그중 시험당 4줄 이상인 7건을 교체 후보로 두고, 원래 고정된 예비 순서의
앞쪽 6건으로 부족한 본 시험을 교체했다. 최종 예비 구성은 질환별 5건, 총 15건과
92조건이다.

새 합성 환자는 질환별 10명, 총 30명이다. 환자마다 임상값은 그대로 두고 확인할 사실
5개의 자료 상태만 바꿨다. 한쪽은 환자 답변이나 의료기록으로 확인됐고, 다른 쪽은
같은 값이지만 답변 대기 또는 기록 미확인 상태다. 총 60회에서 후보 결과는 모두
짝끼리 같았다. 150개 시험 판단 중 105건은 `확인 완료`와 `확인 대기`로 갈렸고,
45건은 두 상태 모두 부적합이었다. 확인된 답을 넣은 뒤 충분한 자료 쪽 결과와 다른
경우는 0건이었다.

이 숫자는 자료 생성 코드의 검사 결과다. 에이전트 정확도나 의료 성능을 측정한 값이
아니다. 시험 조건은 AI 단독 검토 상태이며 두 사람의 독립 확인이 끝나지 않았다.
파일 연결 해시는 Windows와 Linux의 줄바꿈 차이를 제외한 본문으로 계산한다.

보수적 파일은 다음 명령으로 원문 연결과 숫자를 다시 검사해 만들 수 있다. 기존 파일은
자동으로 덮어쓰지 않는다.

```powershell
.\.venv\Scripts\clarifytrial.exe build-natural-evaluation-conservative-gold `
  --source data\natural_evaluation_v1\criterion_review.json `
  --tiered-review data\natural_evaluation_v1\ai_preliminary_review_polarity_audited.json `
  --selection-config configs\natural_evaluation_source_selection_v1.json `
  --output data\natural_evaluation_v1\ai_preliminary_gold_conservative.json
```

최종 예비 시험 구성과 환자 짝 자료는 다음 명령으로 다시 만들고 검사한다. 모든 명령은
기존 결과를 자동으로 덮어쓰지 않는다.

```powershell
.\.venv\Scripts\clarifytrial.exe build-natural-evaluation-trial-set
.\.venv\Scripts\clarifytrial.exe build-natural-evaluation-patient-pairs
.\.venv\Scripts\clarifytrial.exe audit-natural-evaluation-patient-pairs
```

## 작성 방법

다음 열은 수정하지 않는다.

- `reviewer_id`
- `group_id`, `nct_id`, `title`
- `candidate_id`, `section_hint`, `line_number`, `start_char`, `end_char`
- `source_text`, `detection_reasons`

검토자는 아래 열만 작성한다.

| 열 | 작성 내용 |
|---|---|
| `include_in_objective_gold` | 객관적인 조건으로 구조화할 수 있으면 `true`, 아니면 `false` |
| `kind` | 선정 조건은 `inclusion`, 제외 조건은 `exclusion` |
| `fact_code` | 확인할 환자 사실의 짧고 일관된 이름. 예: `hba1c`, `age` |
| `operator` | 숫자 기준이 있을 때만 초과 `gt`, 이상 `gte`, 미만 `lt`, 이하 `lte`, 같음 `eq` |
| `threshold` | 숫자 기준이 있을 때만 기준 숫자 |
| `unit` | 숫자 기준이 있을 때만 원문 단위. 횟수와 점수는 `count`, `score`처럼 명시 |
| `max_age_days` | 자료 유효기간이 원문에 명시된 경우에만 일수로 작성 |
| `allowed_source_types` | `medical_record`, `patient_report`, `official_verification` 중 원문이 요구하는 값 |
| `allowed_verification_statuses` | `verified`, `reported`, `pending`, `conflicting` 중 해당 값 |
| `reviewer_notes` | 제외 이유나 해석이 필요한 부분 |

자료 출처나 확인 상태가 원문에 명시되지 않았다면 관련 열을 비운다. 추측해서 공식
검사를 요구하지 않는다. 임신 여부나 현재 약물 사용처럼 숫자가 없는 명시적 상태는
`fact_code`까지만 쓰고 `operator`, `threshold`, `unit`은 모두 비운다.

### 원문 한 줄에 조건이 여러 개인 경우

같은 행을 복제하고 `annotation_index`를 2, 3처럼 늘린다. `candidate_id`와 원문 열은
그대로 둔다. 예를 들어 `7.0% ≤ HbA1c ≤ 10.0%`는 하한과 상한 두 행으로 작성한다.
한 검토자가 두 조건으로 나누고 다른 검토자가 하나로 두면 비교 결과에서 미작성 또는
불일치로 표시된다.

## 두 표 비교

두 사람이 작성을 마치면 다음 명령을 실행한다.

```powershell
.\.venv\Scripts\clarifytrial.exe compare-natural-evaluation-reviews `
  --source data\natural_evaluation_v1\criterion_review.json `
  --reviewer-1 data\natural_evaluation_v1\reviewer_1.csv `
  --reviewer-2 data\natural_evaluation_v1\reviewer_2.csv `
  --output runs\natural-evaluation-review-comparison.json
```

결과는 일치, 불일치와 미작성 항목을 나눈다. 불일치를 코드가 자동으로 정답 처리하지
않는다. 두 사람이 공식 원문을 다시 확인한 뒤 별도로 합의해야 한다.

최종 예비 구성에는 예비 시험이 들어 있으므로 `reserve_reviewer_1.csv`와
`reserve_reviewer_2.csv`도 같은 방식으로 작성해야 한다. 교체에 쓰지 않은 예비 시험
행은 검토 범위에서 제외할 수 있지만 두 검토자는 같은 시험 목록을 사용해야 한다.

## 다시 만들기

선정 규칙은 `configs/natural_evaluation_source_selection_v1.json`에 고정돼 있다.

```powershell
.\.venv\Scripts\clarifytrial.exe prepare-natural-evaluation-sources `
  --config configs\natural_evaluation_source_selection_v1.json `
  --cache .research-cache\clinicaltrials-natural-evaluation-v1 `
  --review-output data\natural_evaluation_v1\criterion_review.json
```

기존 검토표가 있으면 명령은 내용을 덮어쓰지 않고 원본 연결만 다시 검사한다. 사람이
작성을 시작하기 전 빈 표를 의도적으로 다시 만들 때만 `--overwrite-review-output`을
사용한다. 공식 원문을 새로 받는 `--force`도 기존 검토표가 있으면 중단된다. 원본 전체
기록과 API 검색 응답은 Git에서 제외된 `.research-cache`에 둔다.
