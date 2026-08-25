# 공개 시험 조건 기반 합성 평가자료

10개 질환의 공개 임상시험 50건에서 원문으로 확인할 수 있는 조건 202개를 골라
구조화하고, 그 조건에 맞춘 합성 환자 50명을 연결했다.

| 항목 | 수 |
|---|---:|
| 질환 | 10개 |
| 공개 임상시험 | 50건 |
| 구조화한 조건 | 202개 |
| 원문에서 개수 관계를 확인한 복합 조건 | 3개 시험·4묶음 |
| 합성 환자 | 50명 |
| 환자–시험 조합 | 250개 |
| 가상 환자 전체 상태에서 참가 가능 | 98개 |
| 가상 환자 전체 상태에서 참가 조건 불충족 | 152개 |

`trial_set.json`에는 시험, 조건, 원문 문구와 위치, 조건 사이의 `모두`, `하나 이상`,
`일정 개수 이상` 관계가 들어 있다. `patient_pairs.json`에는 가상 환자의 전체 상태와
일부 정보를 가린 시작 상태, 가린 답, 확인 방법, 질문 전후 예상 판단이 들어 있다.

환자마다 1개, 2개, 3개 또는 5개의 정보를 가렸다. 처음부터 모든 정보가 같은 방법으로
나오지 않도록 환자 답변, 병원 기록, 이미 받은 공식 결과, 새 비침습 검사를 섞었다.
급성 췌장염과 폐암 사례에는 새 검사와 기존 공식 결과 확인이 실제로 선택되는지 점검할
수 있도록 해당 사실을 미리 정한 위치에 배치했다. 환자 결과값을 보고 유리하게 위치를
바꾸지 않았다.

여러 수치나 여러 조건이 섞인 문장은 하나의 검사 기준으로 바꾸지 않는다. 나이와
검사값은 넓은 사람 범위 안에서 만들고, 경계 안팎의 값이 개발 환자나 별도 평가 환자
한쪽에만 몰리지 않도록 다섯 값의 배치 순서를 설정 파일에 고정했다.

202개 조건은 50개 시험의 전체 참가 조건이 아니다. 다음 범위만 실행 조건으로 옮겼다.

- ClinicalTrials.gov의 최소·최대 나이 칸
- 문장 전체가 명확한 임신·수유 및 중대한 활동성 감염 제외 조건
- 하나의 수치와 하나의 비교 방향이 분명한 조건
- 별도 해석 없이 참·거짓을 정할 수 있는 짧은 원문 조건
- 원문에서 `둘 중 하나`, `셋 중 둘`, `넷 중 둘`로 명시한 네 묶음

예외, 시험군별 조건, 여러 뜻이 섞인 긴 문장은 임의로 잘라 넣지 않았다. 따라서 이
자료의 결과는 구조화한 202개 조건에만 해당한다.

## 다시 만들기

```powershell
.\.venv\Scripts\clarifytrial.exe prepare-team-trials
.\.venv\Scripts\clarifytrial.exe select-team-evaluation-trials `
  --trials .research-cache\team-trials\trials.jsonl `
  --output runs\team-trial-expansion\selection.json
.\.venv\Scripts\clarifytrial.exe build-public-protocol-benchmark `
  --output runs\public-protocol-benchmark-rebuild
.\.venv\Scripts\clarifytrial.exe audit-public-protocol-benchmark
```

고정 원본과 이용 조건은 [DATA_SOURCES.md](../../DATA_SOURCES.md)에 정리돼 있다.
