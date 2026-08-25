# 정해진 입력 형식의 전체 실행 예제

환자 정보, 시험 조건과 실험용 답을 JSON으로 읽어 검색부터 질문 뒤 재판정까지
실행한다. 외부 언어모델은 사용하지 않는다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-screening `
  --patient examples\general_screening\patient.json `
  --trials examples\general_screening\trials.jsonl `
  --answers examples\general_screening\answers.json `
  --provider deterministic `
  --output runs\general-screening-example
```

| 파일 | 내용 |
|---|---|
| `patient.json` | 처음부터 확인된 환자 정보, 부족한 정보와 환자 상황 |
| `trials.jsonl` | 한 줄에 한 시험씩 저장한 참가 조건 |
| `answers.json` | 확인 행동 뒤에만 공개하는 합성 답 |

`--answers`를 빼면 터미널에서 답을 직접 입력한다. 간단한 문장은 환자가 말한 정보로
저장된다. 공식 검사값처럼 값, 날짜와 자료 출처가 필요한 경우에는 한 줄짜리 JSON을
입력할 수 있다.

```json
{"statement":"공식 HbA1c 결과는 6.4%였다.","concept":"hba1c","value":6.4,"unit":"%","event_date":"2026-08-20","recorded_date":"2026-08-20","source_type":"official_verification","source_location":"official-result#hba1c","verification_status":"verified"}
```

`quit`을 입력하면 현재 상태를 `session.json`에 저장한다. 이어서 실행할 때는 같은 환자와
시험 파일을 사용하고 `--resume runs\general-screening-example\session.json`을 붙인다.

새 검사처럼 선택이나 승인이 필요한 방법은 자동 실행하지 않는다. 저장된 세션을 다시
열 때 환자 선택은 `--approve-patient-choice`, 담당자 승인은 `--authorize-clinician`으로
기록한다.

기본 검색은 전달한 시험 파일 안에서만 실행한다. TrialGPT 공개 검색 자료를 준비했다면
`--candidate-search trialgpt`와 검색 문서·저장 경로를 지정할 수 있다. 검색 결과에 있어도
`trials.jsonl`에 구조화 조건이 없는 시험은 참가 조건 판단에 넣지 않는다.
