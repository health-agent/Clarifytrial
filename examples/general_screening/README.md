# 범용 구조화 입력 예제

실제 모델 없이 전체 구조를 실행한다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials examples\general_screening\trials.jsonl `
  --answers examples\general_screening\answers.json `
  --output runs\general-screening-example
```

`--answers`를 빼면 필요한 정보를 터미널에서 직접 입력한다. 답변 문장만 입력하거나,
수치와 날짜가 필요하면 다음과 같은 JSON 객체를 한 줄로 입력할 수 있다.

```json
{"statement":"공식 HbA1c 결과는 6.4%였다.","concept":"hba1c","value":6.4,"unit":"%","event_date":"2026-08-20","recorded_date":"2026-08-20","source_type":"official_verification","source_location":"official-result#hba1c","verification_status":"verified"}
```

문장만 입력하면 환자가 말한 내용으로 저장한다. 위처럼 공식 자료로 쓰려면 자료 종류,
자료 위치, 확인 상태와 날짜를 JSON에 직접 적어야 한다.

`quit`을 입력하면 `session.json`을 저장한다. 같은 환자·시험 파일과
`--resume runs\general-screening-example\session.json`을 지정하면 이어서 실행한다.
`unknown`으로 넘긴 정보를 나중에 다시 확인할 때는 `--retry-unavailable`을 붙인다.
새 검사처럼 별도 선택과 승인이 필요한 방법은 환자 선택과 담당자 승인을 각각
`--approve-patient-choice`, `--authorize-clinician`으로 기록한 뒤 재개한다.

기본 검색은 전달한 시험 파일 안에서만 실행한다. TrialGPT BM25·MedCPT 검색 자료를
준비했다면 `--candidate-search trialgpt`, `--trialgpt-corpus`, `--trialgpt-cache`를
같이 지정한다. 검색 결과에 있어도 `trials.jsonl`에 구조화 조건이 없는 시험은 판단
대상에 넣지 않는다.
