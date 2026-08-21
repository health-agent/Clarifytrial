# 자연어 평가 조건 검토 안내

이 폴더는 개발에 사용하지 않은 공개 임상시험 조건을 두 사람이 독립적으로 확인하기
위한 작업 묶음이다. 환자 자료는 없으며 ClinicalTrials.gov 공개 원문만 사용한다.

## 파일

| 파일 | 용도 |
|---|---|
| `criterion_review.json` | 코드가 읽는 원문 위치, 시험 정보와 검토 대기 상태 |
| `reviewer_1.csv` | 첫 번째 검토자가 작성할 표 |
| `reviewer_2.csv` | 두 번째 검토자가 작성할 표 |

두 CSV는 처음에는 내용이 같다. 검토자는 상대방 표를 보지 않고 자신의 파일만 작성한다.
자동 규칙이 객관적 조건일 가능성이 있다고 표시한 문구는 164개다. 이 규칙이 빠뜨린
조건까지 확인할 수 있도록 검토표에는 본 시험의 조건 원문 272줄을 모두 넣었다. 아직
어느 행도 정답이 아니다.

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
