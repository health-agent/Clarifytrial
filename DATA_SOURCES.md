# Data Sources

현재 저장소에는 연구용 원본 데이터셋을 포함하지 않는다. 외부 자료를 추가할 때는
출처, 사용 목적, 이용 조건과 받은 날짜를 이 문서에 기록한다.

| 자료 | 계획한 용도 | 현재 상태 |
|---|---|---|
| ClinicalTrials.gov API v2 | 임상시험 원문과 모집 정보 | 연결 전 |
| TrialGPT Criterion Annotations | 조건별 판단과 근거 평가 | 연결 전 |
| TREC Clinical Trials 2021·2022 | 후보 검색과 순위 평가 | 연결 전 |
| 새 ClarifyTrial v5 합성 자료 | 후보 유지, 현재 확정, 다음 행동과 재판정 평가 | 오래된 검사 예시 1건 구현, 확장 전 |

환자 사례는 합성 자료만 사용한다. 실제 환자 기록, 개인식별정보, 자격 증명, API 키,
비공개 임상시험 자료를 저장소에 추가하지 않는다.

`examples/stale_lab`은 구조 검사를 위해 연구자가 작성한 가상 환자와 가상 시험이다.
의료 전문가가 합의한 평가자료나 실제 환자 자료로 취급하지 않는다.

자료의 구체적인 라벨 구조와 선택 기준은
[CLARIFYTRIAL_DATASETS.md](docs/internal/CLARIFYTRIAL_DATASETS.md)에서 관리한다.
