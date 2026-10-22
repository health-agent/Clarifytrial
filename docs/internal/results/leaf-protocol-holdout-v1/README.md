# 외부 주석의 새 24문구 평가 — 완료

앞의 [48문구 비교와 v9 개발](../leaf-protocol-v1/) 뒤 코드를 고정하고, 겹치지 않는
시험 24개의 문구에서 기존 `c25e002`와 수정 v9을 실제로 비교했다. 임상 외부 검증이
아니며 같은 Leaf 배포 자료의 제한한 수치 부분 해석 평가다. 일반 AND/OR 성능이나
전체 프로토콜의 판정을 검증하지 않았다.

## 모델 실행 전에 정한 범위

기존 프로젝트의 시험 번호 184개와 앞선 48개를 합한 232개를 제외 대상으로 뒀다.
고정 난수 20261024로 범위·단일 나이·다른 단일 수치를 각 8개 골랐고 시험당 한 문구다.
코드는 이 문구의 모델 결과를 보기 전에 고정했다. 원문·외부 주석과 부분 참고식은
`cases.json`이다. 모집단을 대표하는 무작위 임상 표본이 아니다.

128개의 합성 경계/누락 관측 중 일반 BETWEEN의 끝점 관례가 갈리는 16개는 정오
채점에서 별도 보존했다. 나머지 112개 중 참고식의 정리 가능은 88, 정보 부족은 24개다.
연구 집단·시점·일반적인 측정 명칭이 참고식에 충분히 표현되지 않은 6문구를 실행 전에
표시했다. 원문을 지우거나 개념명을 사후 정답에 맞추지 않았다. 실제 환자 데이터는 없다.

## 결과

두 방식 각각 24회, 총 48회가 정상 완료했다. 모델 `gpt-5.6-sol`, medium과
SDK/runtime 0.147.0은 앞 단계와 같다. 모델에는 참고식·주석·합성 정답을 주지 않았다.

| 실제 실행 | 문구 평균 일치 | 관측 일치 /112 | 정리 일치 /88 | 정리 가능 중 보류 | 참고와 반대 확정/제외 | 참고 미정에서 결론 |
|---|---:|---:|---:|---:|---:|---:|
| 기존 | 69.10% | 78 | 54 | 34 | 0 /0 | 0 |
| v9 | 57.99% | 65 | 41 | 47 | 0 /0 | 0 |

문구 평균 차이 v9−기존은 -11.11%p, 고정 계층을 유지한 문구 단위 5,000회
부트스트랩 범위는 -25.70–2.43%p였다. 사전에 표시한 6문구를 뺀 18문구에서도
-8.80%p[-26.39,9.26]였다. 단위는 독립 합성 환자가 아닌 시험당 한 원문 문구다.
항상 보류하는 기준의 문구 평균은 22.22%, 정리 일치는 0이다.

원문 가드가 미지원한 문구는 기존 4, v9 9개였고 채점 관측이 모두 일치한 문구는
14/11개였다. 끝점 참고가 모호한 관측에서 결론 낸 수는 10/8개였으며 이를 임상
오류나 안전성으로 채점하지 않는다. 한 번씩 생성한 결과라 모델 생성의 변동성도 남는다.

v9은 새 문구에서 기존보다 일반적으로 낫다는 근거를 얻지 못했다. BMI 범위 뒤의
모집 표현, 나이를 나타내는 문법과 개념·시점 연결에서 보류가 남았다. 시점이 붙은
조건을 단순한 현재 값으로 바꾸어 일치를 올리지 않는다. 일부 수치 참고식과의 불일치는
의학적 오판과 다르므로 결과를 임상 적격 정확도로 부르지 않는다.

## 재현과 파일

```powershell
python scripts/build_leaf_protocol_probe.py --source runs/leaf-prepared --exclude-cases docs/internal/results/leaf-protocol-v1/cases.json --seed 20261024 --followup-subset --output runs/leaf-followup
python scripts/run_natural_protocol_logic_research.py --mode model --cases runs/leaf-followup/cases.json --source-root runs/frozen-code --output runs/leaf-followup-model --runtime-catalog runs/natural-logic-20261023/runtime-catalog.json
python scripts/analyze_leaf_protocol_probe.py --cases runs/leaf-followup/cases.json --output runs/leaf-followup --candidate v9 --method old=runs/old/results.json --method v9=runs/v9/results.json
```

보존한 실제 제안은 `model_old_results.json`, `model_v9_results.json`, 문구·계층 지표는
`case_metrics.csv`, `stratum_metrics.csv`, 집계와 범위는 `summary.json`이다. 실제 두
실행의 토큰 합계는 731,570이며 일반적인 모델 비용·속도 추정이 아니다. 원시는
`runs/leaf-model-20261023/followup-old/`, `followup-v9/`다. 고정 v9 코드는 같은 상위
폴더의 `baseline-v9-development/`에서 실행했다. 공용 실행기가 SDK 프로세스 트리에
메모리 1.5GiB·동적 CPU 한도를 적용했고 두 실행은 종료 0으로 마쳤다. 진행 중인 호출은 없다.

주석은 UW Bio-NLP의 공개 Leaf 자료에서 왔으며 받은 날짜는 2026-10-23이다.
MIT 허가 문구는 `LICENSE.upstream`, 원본·revision·수집 방법은 프로젝트 `DATA_SOURCES.md`를 따른다.

이 결과는 의학적 조언이 아닌 참고용입니다. 임상시험 참가 가능성은 해당 시험의 최신 기준과 전체 자료를 바탕으로 다시 확인해야 합니다.
