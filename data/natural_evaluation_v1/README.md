# 자유 문장 조건 평가용 예비자료 v1

상태: 과거 개발자료

ClinicalTrials.gov의 공개 참가 조건을 자유 문장에서 구조화하는 기능을 개발할 때 만든
예비자료다. 현재 대표 성능은 이 폴더가 아니라 `data/public_protocol_benchmark_v1`과
`data/independent_new_trial_benchmark_v1`에서 계산한다.

## 자료 구성

| 파일 | 내용 |
|---|---|
| `criterion_review.json` | 본 시험 15건의 조건 원문과 위치 |
| `ai_preliminary_review_polarity_audited.json` | 원문 조건의 방향과 수치를 두 단계로 확인한 모델 초안 |
| `ai_preliminary_gold_conservative.json` | 코드로 바로 계산할 수 있다고 고른 62개 예비 조건 |
| `reserve_criterion_review.json` | 교체 후보 시험 10건의 조건 원문과 위치 |
| `ai_preliminary_reserve_review_polarity_audited.json` | 교체 후보 조건의 모델 초안 |
| `ai_preliminary_reserve_gold_conservative.json` | 교체 후보에서 고른 50개 예비 조건 |
| `preliminary_trial_set.json` | 조건이 적은 본 시험을 교체한 시험 15건과 조건 92개 |
| `preliminary_patient_pairs.json` | 합성 환자 30명의 근거 충분·불충분 짝 60개 |
| `preliminary_natural_records.json` | 같은 환자 상태를 자유 문장으로 표현한 합성 기록 |

`reviewer_1.csv`, `reviewer_2.csv`와 교체 후보용 CSV는 과거 검토 형식을 보존한 파일이다.
현재 구조화 조건 최종 평가에는 사용하지 않는다.

## 만들어진 과정

1. 세 질환에서 본 시험 15건과 교체 후보 시험 10건을 고정했다.
2. 본 시험 조건 원문 272줄과 교체 후보 조건 원문 231줄을 수치, 방향과 복합 관계로
   나누는 모델 초안을 만들었다.
3. 한 원문에 여러 뜻이 섞인 조건을 제외하고 코드로 바로 계산할 수 있는 조건만 남겼다.
4. 조건이 너무 적은 본 시험 6건을 미리 정한 교체 후보 순서에 따라 바꿨다.
5. 질환별 10명, 총 30명의 합성 환자를 만들고 같은 임상값에서 자료 출처와 확인 상태만
   다른 짝을 만들었다.

이 자료의 조건은 모델 예비 검토로 만든 개발 입력이다. 시험 전체 참가 조건이나 현재
최종 정답이 아니다. 여기서 질문 순서를 조정한 뒤 새 합성 환자를 만든 자료가
`data/natural_evaluation_v2`다.

## 다시 만들기

```powershell
.\.venv\Scripts\clarifytrial.exe build-natural-evaluation-conservative-gold `
  --source data\natural_evaluation_v1\criterion_review.json `
  --tiered-review data\natural_evaluation_v1\ai_preliminary_review_polarity_audited.json `
  --selection-config configs\natural_evaluation_source_selection_v1.json `
  --output data\natural_evaluation_v1\ai_preliminary_gold_conservative.json

.\.venv\Scripts\clarifytrial.exe build-natural-evaluation-trial-set
.\.venv\Scripts\clarifytrial.exe build-natural-evaluation-patient-pairs
.\.venv\Scripts\clarifytrial.exe audit-natural-evaluation-patient-pairs
```

원본 주소, 수집 시각과 이용 조건은 [자료 출처](../../DATA_SOURCES.md)에 있다.
