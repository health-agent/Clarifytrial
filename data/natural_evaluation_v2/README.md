# 새 질문 정책 평가용 합성 환자

이 폴더는 질문 정책 v3를 정한 뒤 새로 만든 합성 환자 자료다. 공개 시험과 조건은
`data/natural_evaluation_v1/preliminary_trial_set.json`의 15건·92조건을 그대로 쓴다.

| 파일 | 용도 |
|---|---|
| `preliminary_patient_pairs.json` | 질환별 20명, 총 60명의 근거 충분·불충분 짝 120회 |
| `preliminary_natural_records.json` | 같은 120회를 진료기록형·문장형으로 표현한 합성 기록 |

각 질환의 1~10번 환자는 개발 자료, 11~20번 환자는 새 평가 자료다. v3를 고른 뒤에는
11~20번 환자의 부족한 근거 기록 30회만 Sol `medium`으로 읽고 질문 정책을 비교했다.

환자 기록은 모두 합성이며 임상값은 구조화 상태표에서만 가져온다. 충분·부족 짝은
임상값과 문장 순서가 같고 자료 출처와 확인 상태만 다르다. 이 자료의 시험 조건은
AI 단독 예비 검토 상태이므로 의사 정답, 사람 두 명의 합의 정답이나 임상 성능
평가자료가 아니다.

다음 명령으로 값과 자료 상태가 원본 짝과 같은지 다시 검사한다.

```powershell
.\.venv\Scripts\clarifytrial.exe audit-natural-evaluation-records `
  --patient-pairs data\natural_evaluation_v2\preliminary_patient_pairs.json `
  --records data\natural_evaluation_v2\preliminary_natural_records.json
```

실행 결과와 해석 범위는 `docs/internal/CLARIFYTRIAL_VALIDATION_RESULTS.md` 25절에 있다.
