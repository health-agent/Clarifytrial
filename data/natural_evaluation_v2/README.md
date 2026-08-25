# 자유 문장 질문 정책 개발자료 v2

상태: 과거 개발자료

v1의 시험 15건과 구조화 조건 92개는 유지하고, 질문 순서를 정한 뒤 새로운 합성 환자를
만든 자료다.

| 파일 | 내용 |
|---|---|
| `preliminary_patient_pairs.json` | 질환별 20명, 총 60명의 근거 충분·불충분 짝 120개 |
| `preliminary_natural_records.json` | 같은 120개 상태를 기록형 문장으로 표현한 합성 자료 |

각 질환의 1~10번 환자는 질문 순서 개발에, 11~20번 환자는 개발 뒤 점검에 사용했다.
환자 기록은 모두 합성이며 임상값은 구조화 상태에서만 가져왔다. 충분한 자료와 부족한
자료의 짝은 임상값과 문장 순서가 같고 자료 출처와 확인 상태만 다르다.

현재 대표 결과는 더 넓은 10개 질환 공개 시험 자료와 기존 자료에 겹치지 않는 새 시험
자료를 사용한다. 이 폴더의 값은 자유 문장 입력 연결의 과거 개발 결과로만 남긴다.

다음 명령으로 자유 문장 기록의 값과 자료 상태가 구조화 원본과 같은지 검사한다.

```powershell
.\.venv\Scripts\clarifytrial.exe audit-natural-evaluation-records `
  --patient-pairs data\natural_evaluation_v2\preliminary_patient_pairs.json `
  --records data\natural_evaluation_v2\preliminary_natural_records.json
```
