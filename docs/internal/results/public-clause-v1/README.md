# 공개 문구의 자동 준비 — 탐색적 재사용 평가

2026-10-23 실행. 10월 22일 받은 동일 공개 시험의 평가 쪽 24건에서 시험당 원문 묶음
하나를 골랐다. 선언 비교식 수가 가장 많은 묶음, 동률이면 앞선 행을 선택했다. 독립 NLP
주석·새 외부 시험·임상 정답이 아니다. 원문 조건을 일부만 계산한 기존 저자 선언과
자동 준비의 관계를 살핀 구성요소 검사다. 비수치 문장 판단 모델은 호출하지 않았다.

자료는 `data/paper_source_benchmark_v2/public_clause_probe_v1.json`, 생성기는
`scripts/build_public_clause_probe.py`다. 경계·누락의 448관측 중 선언한 부분 해석에서
정리 가능한 관측은 358개, 미정은 90개다. 큰 두 묶음은 고정 난수로 각각 129관측을
뽑았다. 같은 문장과 수치의 파생 관측을 독립 환자로 세거나 신뢰구간을 만들지 않았다.
본문 기준은 [검증 결과 12절](../../CLARIFYTRIAL_VALIDATION_RESULTS.md)이다.

| 비교 | 상태 일치 /448 | 문장 전체 일치 /24 | 올바른 정리 /358 | 정리 가능했지만 보류 | 선언과 다른 제외 | 선언 미정인데 결론 |
|---|---:|---:|---:|---:|---:|---:|
| 항상 보류 | 90 | 0 | 0 | 358 | 0 | 0 |
| 기존 제안·기존 코드 | 169 | 7 | 92 | 241 | 25 | 13 |
| 새 제안·고정 v7 | 146 | 7 | 56 | 302 | 0 | 0 |
| 새 제안·기존 평평한 코드 | 153 | 6 | 79 | 248 | 31 | 16 |
| 기존 제안·v7 재생 | 111 | 7 | 21 | 337 | 0 | 0 |
| 기존 제안·후속 v8 재생 | 149 | 8 | 59 | 299 | 0 | 0 |

반대 방향 확정은 모두 0이었다. v8은 새 제안에서 v7과 동일했다. 공개 문구에서는
합성 문장의 개선이 재현되지 않았다. v7은 오판을 줄이면서 정리 기회도 놓쳤다.
문장별 일치율을 동일 가중한 평균도 기존 49.93%, v7 43.48%였다. 보류만 늘리는 것과
범위·의미를 보존하면서 정리하는 것을 구분한다.

v7의 13문장은 원문 문법·미지원 문맥 때문에 보류됐다(정리 가능 참고 관측 287개).
통과한 11문장의 추가 보류 15개는 용어·문맥 또는 단위 연결 차이였다. 예를 들어
`HbA1c at Screening`의 시점 문맥을 임의로 제거해 단순 동의어로 취급하지 않았다.
넓은 참고 해석은 수치만 다루므로 이 보류를 모두 임상적 오류로 단정하지 않는다.

실제 호출은 방식별 24회, 동일 `gpt-5.6-sol`/`medium`, SDK/runtime `0.147.0`이었다.
원래 코드 `c25e002` 다음 새 코드 `24ee3b3`를 실행했고 후속 v8과 분리했다. 총 사용량은
360,718 / 384,333토큰이었다. v8은 겹친 인용에서 명시한 개념·부호·수치·단위가 모두
유일하게 보이는 경우와 행 끝의 AND/OR 연속을 처리했다. 원문에 남는 시점·임상 단어는
지우지 않는다. 결과를 본 뒤 고친 탐색적 재생이며 독립 검증으로 승격하지 않았다.

`model-old.json`, `model-v7.json`은 실제 제안과 관측이다. `summary.json`과
`case_results.csv`에는 고정 코드 비교와 재생·항상 보류 기준이 있다. 원시는
`runs/public-clause-20261023/`에 남겼다. 실제 자료의 전체 임상 자격·모집 상태 정확도나
언어모델을 포함한 전체 앱의 성능을 측정하지 않았다. 출처·수집 시각·권리는
[자료 출처](../../../../DATA_SOURCES.md)를 따른다.

## 재현

보존한 제안의 현행 코드 재생에는 모델 호출이 필요 없다.

```powershell
python scripts/build_public_clause_probe.py
python scripts/run_natural_protocol_logic_research.py --cases data/paper_source_benchmark_v2/public_clause_probe_v1.json --mode replay --replay docs/internal/results/public-clause-v1/model-old.json --output runs/public-replay/old-v8
python scripts/run_natural_protocol_logic_research.py --cases data/paper_source_benchmark_v2/public_clause_probe_v1.json --mode replay --replay docs/internal/results/public-clause-v1/model-v7.json --output runs/public-replay/new-v8
```

원래 코드와 v7은 `git archive`로 각각 `c25e002`, `24ee3b3`의 `src`·`prompts`를
프로젝트 `runs`에 풀고 `--source-root`로 지정한다. v7 제안을 구형 계약으로 보낼 때만
`--strip-logic-draft`로 지역 키·묶음을 제거한다. 실제 모델 재실행은 새 출력 폴더와
`--mode model`을 사용하며 한 번 생성한 결과와 달라질 수 있다. 원문을 새로 조회하지
않고 보존한 수집본을 사용한다. 실행기 호환 설정은 합성 문장 결과 README와 같다.

이 결과는 의학적 조언이 아닌 참고용입니다. 임상시험 참가 가능성은 해당 시험의 최신 기준과 전체 자료를 바탕으로 다시 확인해야 합니다.
