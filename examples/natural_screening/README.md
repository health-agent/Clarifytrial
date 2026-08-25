# 자유 문장 입력 연결 예제

개인식별정보가 없는 합성 환자 기록과 합성 시험 조건을 언어모델로 정리한 뒤 공통 실행
흐름에 넘긴다. 확인 뒤 공개할 검사 결과는 `hidden_answers.json`에 따로 저장돼 있다.

실행 순서는 다음과 같다.

1. 환자 기록에서 제2형 당뇨병과 과거 당화혈색소를 근거 문구와 함께 정리한다.
2. 합성 시험 세 건 가운데 당뇨병 시험 두 건을 찾는다.
3. 두 시험에 공통으로 필요한 최근 당화혈색소를 한 번 확인한다.
4. 새 결과를 두 시험에 반영하고 목록을 다시 만든다.

다음 명령은 ChatGPT 구독 연결을 사용하므로 외부 모델 호출이 발생한다.

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

`local-bm25`는 이 작은 예제 안에서 같은 단어가 겹치는 시험을 찾는 검색이다. 공개 검색
성능을 비교할 때는 TREC 자료와 TrialGPT 검색 저장본을 지정하고
`--candidate-search trialgpt`를 사용한다.

최종 결과는 `result.json`, 단계별 입력과 출력은 `trace.jsonl`에 저장된다.
