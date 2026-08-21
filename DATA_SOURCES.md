# Data Sources

현재 저장소에는 연구용 원본 데이터셋을 포함하지 않는다. 외부 자료를 추가할 때는
출처, 사용 목적, 이용 조건과 받은 날짜를 이 문서에 기록한다.

| 자료 | 계획한 용도 | 현재 상태 |
|---|---|---|
| ClinicalTrials.gov API v2 | 임상시험 원문과 모집 정보 | 2026-08-21 공개 시험 15건 연결 |
| TrialGPT Criterion Annotations | 조건별 판단과 근거 평가 | 2026-08-20 공개 원본 1,015행 연결 |
| TREC Clinical Trials 2021·2022 | 후보 검색과 순위 평가 | 2026-08-21 TrialGPT 공개 검색 재현 완료 |
| 새 ClarifyTrial v5 합성 자료 | 후보 유지, 현재 확정, 다음 행동과 재판정 평가 | 공개 조건 기반 합성 환자 30명·마스크 60회 구현 |

환자 사례는 합성 자료만 사용한다. 실제 환자 기록, 개인식별정보, 자격 증명, API 키,
비공개 임상시험 자료를 저장소에 추가하지 않는다.

`examples/stale_lab`은 구조 검사를 위해 연구자가 작성한 가상 환자와 가상 시험이다.
의료 전문가가 합의한 평가자료나 실제 환자 자료로 취급하지 않는다.

## ClinicalTrials.gov API v2

- 공식 자료: https://clinicaltrials.gov/data-api/api
- 이용 조건: https://clinicaltrials.gov/about-site/terms-conditions
- 표시 출처: ClinicalTrials.gov, U.S. National Library of Medicine
- 받은 날짜: 2026-08-21
- API 버전: 2.0.5
- 원본 자료 시각: 2026-08-20T09:00:05
- 로컬 위치: `.research-cache/clinicaltrials-v5`
- 범위: 2형 당뇨병, 유방암, 주요우울장애 각 5건, 총 15건
- 파생자료: `configs/interactive_public_benchmark_v1.json`

원본 연구기록은 수정하지 않는다. 파생자료에는 원문의 객관적으로 계산 가능한 일부
조건만 수치·기간 규칙으로 옮겼다. 원문 15건과 구조화 조건 80개의 식별자와 문구를
실행 전에 대조한다. 로컬 원본과 실행 결과는 Git에 넣지 않으며, 파생자료는 연구자가
작성한 것이므로 의료 전문가 합의 정답이나 ClinicalTrials.gov의 해석으로 표현하지 않는다.

## TrialGPT Criterion Annotations

- 공식 자료: https://huggingface.co/datasets/ncbi/TrialGPT-Criterion-Annotations
- 이용 조건: public domain
- 받은 날짜: 2026-08-20
- 받은 방법: Hugging Face 공개 rows API
- 로컬 위치: `.research-cache/trialgpt/criterion_annotations.jsonl`
- 확인 규모: 조건 1,015개, 환자 53명, 환자-시험 105조합, 임상시험 103개
- 알려진 결함: 조건 원문이 비어 있는 행 1개는 모델 실행 표본에서 제외
- `training` 열: 공식 카드에 뜻이 설명돼 있지 않으며 학습·평가 분할로 사용하지 않음

로컬 캐시는 Git에 넣지 않는다. 실행할 때 출처 정보와 집계값을
`.research-cache/trialgpt/source_metadata.json`에 함께 저장한다. 시험 제목, 질환,
중재와 요약은 TrialGPT 공개 저장소의 SIGIR corpus와 시험 ID로 연결한다. 전문가
라벨과 공개 TrialGPT 결과는 모델 입력에서 제외하고 호출이 끝난 뒤 채점할 때만
사용한다.

원본 대조 결과 `training=true` 881행은 공개 GPT 라벨과 전문가 라벨이 모두 같고,
`false` 134행은 5행만 같다. 이 열은 공개 출력과 전문가 평가의 일치 여부를 거의
그대로 반영하므로 독립 평가 표시로 해석하지 않는다.

조건 원문이 모두 있는 104개 환자-시험 조합은 개발 20명·20조합, 개발 환자와
겹치지 않는 평가 33명·64조합, 개발 환자의 다른 시험 20조합으로 분리한다. 세부
실행 결과는 [검증 결과](docs/internal/CLARIFYTRIAL_VALIDATION_RESULTS.md)에 있다.

자료의 구체적인 라벨 구조와 선택 기준은
[CLARIFYTRIAL_DATASETS.md](docs/internal/CLARIFYTRIAL_DATASETS.md)에서 관리한다.

## TREC Clinical Trials 2021·2022

- 공식 자료: [TREC 2021](https://trec.nist.gov/data/trials2021.html),
  [TREC 2022](https://trec.nist.gov/data/trials2022.html)
- 사용 목적: TrialGPT의 BM25, MedCPT와 순위 결합 재현 및 후보 시험 검색 평가
- 받은 날짜: 2026-08-21
- 로컬 위치: `.research-cache/TrialGPT/dataset/trec_2021`,
  `.research-cache/TrialGPT/dataset/trec_2022`
- 2021 말뭉치: 고유 시험 26,149개, SHA-256
  `01692c847b2da798c57a8e0a74273ec262a7e42ad3f02b4ff5a87a6442462f9c`
- 2022 말뭉치: 고유 시험 26,581개, SHA-256
  `baf63d82df91d3c1bf1f8ec53c6747d9585a4c81e221a4e1bbb3042da64be76a`
- 판정 자료: 2021 35,832행, 2022 35,394행
- 검색어: TrialGPT 저장소가 공개한 GPT-4 생성 검색어를 그대로 사용
- 논문 수치 대조: TrialGPT 논문의 공개 Source Data 중 `Fig. 2b`

원본 말뭉치, 검색 벡터와 실행 결과는 용량 때문에 Git에 넣지 않는다. 같은 자료를
다시 받을 수 있도록 경로, 파일 해시, 실행 설정과 패키지 버전만 저장소에 기록한다.

## 선택 검토의 공개 웹 자료

- 사용 날짜: 2026-08-21
- 사용 목적: 일반 의학 용어, 질환·치료 관계와 통상적인 기록 관행 확인
- 허용 범위: 공공 의료기관, 전문 지침과 논문 검색
- 금지 범위: 환자 문장, 환자·시험 식별자, 조건 원문, TrialGPT와 정답 라벨 검색
- 실행 결과: 개발 20조합의 집중 검토에서 검색 12회, 정답 추가 0개
- 기록 위치: `runs/trialgpt-strong-review-focused-dev20/case-results.jsonl`

검색 결과의 본문은 저장하지 않고 검색어, 제목과 URL만 실행 기록에 남긴다. 실행
자료는 Git에서 무시한다. 인터넷 검토는 성능을 낮춰 현행 구조에서 기각했다.

## 팀 masked-eval 기준선

- 저장소: [Seohvvan/Healthcare](https://github.com/Seohvvan/Healthcare)
- 확인 브랜치: `masked-eval`
- 확인 날짜: 2026-08-21
- 참고 범위: 후보 시험 고정, 문장 단위 사실 마스킹, 질문 뒤 재판정과 회복 지표
- 사용하지 않을 정답: LLM이 환자 설명을 늘리며 새로 만든 생활·임상 사실,
  완전 기록에서 나온 LLM 판정
- 이용 조건: 공개 브랜치에서 라이선스 파일을 확인하지 못함

작성자의 명시적 허락이나 라이선스가 추가되기 전에는 코드를 저장소에 복사하지
않는다. 같은 비교가 필요하면 공개 입출력 설명에 맞춰 독립 구현한다.
