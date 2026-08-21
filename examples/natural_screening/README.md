# 자연어 입력 연결 예제

이 폴더는 개인 식별 정보가 없는 합성 사례다. 환자 기록과 임상시험 조건은 모델에
보이지만, 확인 뒤에 공개될 검사 결과는 `hidden_answers.json`에 따로 둔다.

흐름은 다음과 같다.

1. 환자 기록에서 제2형 당뇨병과 과거 HbA1c를 원문 인용과 함께 정리한다.
2. 세 합성 시험 가운데 당뇨병 시험 두 건을 검색한다.
3. 두 시험에 공통으로 필요한 최근 HbA1c를 한 번 확인한다.
4. 새 결과를 두 시험에 함께 반영하고 추천 목록을 다시 만든다.

다음 명령은 ChatGPT 구독 연결을 사용하므로 실제 모델 호출이 발생한다.

```powershell
.\.venv\Scripts\clarifytrial.exe run-natural-screening `
  --request examples\natural_screening\request.json `
  --candidate-search local-bm25 `
  --trial-sources examples\natural_screening\trial_sources.json `
  --hidden-answers examples\natural_screening\hidden_answers.json `
  --output runs\natural-screening `
  --provider codex-subscription `
  --confirm-model-run
```

`local-bm25`는 전체 연결을 작게 확인하기 위한 검색이다. 연구 비교에서는 공개 TREC
자료와 TrialGPT 재현 캐시를 넣어 `--candidate-search trialgpt`를 사용한다. 결과는
`result.json`, 단계별 입력·출력은 `trace.jsonl`에 저장된다.
