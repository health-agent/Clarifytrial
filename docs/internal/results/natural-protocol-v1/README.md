# 자유 문장 준비의 논리 보존 — 탐색적 구성요소 평가

2026-10-23 실행. 모든 문장과 환자 값은 저자가 만든 합성 자료다. 공개 시험 원문이나
임상가 주석을 표집한 독립 NLP 평가가 아니다. 결과의 기준은
[검증 결과 11절](../../CLARIFYTRIAL_VALIDATION_RESULTS.md)이다.

15문장 중 명확한 수치 정답이 있는 12문장에는 경계값과 누락 정보를 포함한 185관측이
있다. 이 가운데 정답이 확정·제외인 관측은 115개다. 나머지 3문장·33관측은 IF 범위,
AND/OR 모호성, 표현하지 않은 감염 조건을 살핀다. 이 33개에는 임상적 정오를 부여하지
않았다. 같은 문장에서 파생한 관측을 독립 환자로 세거나 신뢰구간을 만들지 않았다.

| 비교 | 185관측 상태 일치 | 115관측 올바른 정리 | 불필요한 보류 | 잘못 제외 | 정답 미정인데 결론 |
|---|---:|---:|---:|---:|---:|
| 기존 모델 제안 + 기존 코드 | 109 | 41 | 72 | 2 | 2 |
| 기존 모델 제안 + 새 코드 재생 | 115 | 45 | 70 | 0 | 0 |
| 새 모델 제안 + 기존 코드 재생 | 122 | 68 | 39 | 8 | 16 |
| 새 모델 제안 + 새 코드 | 182 | 112 | 3 | 0 | 0 |

잘못 확정한 관측은 네 비교에서 모두 0이었다. 새 제안을 기존 코드에 넣을 때에는
기존 스키마가 받지 않는 `criterion_key`와 `logic_groups`만 제거했다. 수치·문구는
바꾸지 않았다. 프롬프트·계약·컴파일러가 함께 바뀐 실제 모델 비교이므로 차이 전체를
한 요소의 효과로 돌리지 않는다.

같은 수동 원자 제안을 양쪽 코드에 넣은 비교는 122→157/185, 잘못 제외 8→0,
미정에서 성급한 결론 16→0이었다. 논리 묶음이 없는 다중 행 개수 조건은 새 코드에서도
보류됐다. 이는 모델 오류율 실험과 구분하는 결정적 재현이다.

실제 모델은 두 방식 모두 `gpt-5.6-sol`, 추론 `medium`, SDK/runtime `0.147.0`이었다.
문장당 방식별 한 번, 총 30번 추론했고 계약/실행 실패는 없었다. 원문만 전달한 도구
없는 격리 추론이며 임상 판단·환자 구조화 모델은 호출하지 않았다. 실제 모델은 새 계약에서
12문장 중 11문장의 모든 관측을 맞췄다. ECOG 세 관측은 원문 논리는 맞았지만
`ECOG performance status`와 고정 환자 코드 `ecog`를 연결하지 못해 보류됐다.
결과를 본 뒤 개념명을 바꿔 주 점수를 다시 계산하지 않았다.

`model-old.json`, `model-new.json`에는 실제 제안·사용량·합성 관측이 있다. `summary.json`은
수동 제안 및 두 방향 재생을 포함한 집계, `case_results.csv`는 문장별 분해다. 원시 추적은
작업 폴더의 `runs/natural-logic-20261023/`에 남겼다. 모델 사용량 합계는 기존
221,618토큰, 새 방식 240,835토큰이며 단일 실행의 속도·비용 우위로 해석하지 않는다.

## 재현

원자 제안과 기존 모델 제안의 새 코드 재생에는 모델 계정이 필요 없다.

```powershell
python scripts/run_natural_protocol_logic_research.py --mode scripted --output runs/natural-replay/scripted-new
python scripts/run_natural_protocol_logic_research.py --mode replay --replay docs/internal/results/natural-protocol-v1/model-old.json --output runs/natural-replay/old-proposals-new-code
git archive --format=zip --output=runs/baseline-c25e002.zip c25e002 src prompts
Expand-Archive -LiteralPath runs/baseline-c25e002.zip -DestinationPath runs/baseline-c25e002
python scripts/run_natural_protocol_logic_research.py --mode replay --source-root runs/baseline-c25e002 --strip-logic-draft --replay docs/internal/results/natural-protocol-v1/model-new.json --output runs/natural-replay/new-proposals-old-code
```

실제 모델 재실행은 `--mode model --model gpt-5.6-sol --effort medium`과 Codex 인증이
필요하며 새 출력 폴더를 사용한다. 한 번의 생성 결과를 일반적인 모델 성능으로 간주하지
않는다. 이번 환경에서는 구형 runtime과 사용자 설정의 호환을 위해 기존 공개 모델 목록의
복사본에서 누락된 `supports_parallel_tool_calls`를 `false`로 채우고
`--runtime-catalog`로 전달했다. 실행 프로세스에만 `features.context_management=false`를
적용했다. 사용자 설정·인증 파일은 수정하지 않았으며 목록 복사본은 배포하지 않는다.

이 결과는 의학적 조언이 아닌 참고용입니다. 임상시험 참가 가능성은 해당 시험의 최신 기준과 전체 자료를 바탕으로 다시 확인해야 합니다.
