# Data Sources

현재 저장소에는 연구용 원본 데이터셋을 포함하지 않는다. 외부 자료를 추가할 때는
출처, 사용 목적, 이용 조건과 받은 날짜를 이 문서에 기록한다.

| 자료 | 계획한 용도 | 현재 상태 |
|---|---|---|
| ClinicalTrials.gov API v2 | 임상시험 원문과 모집 정보 | 개발 15건과 자연어 평가 본 시험 15건·예비 15건 연결 |
| 팀 공개 ClinicalTrials.gov 시험 모음 | 여러 질환의 넓은 후보 검색 | 2026-08-24 기준 1,931건 확인, 다음 평가 연결 예정 |
| TrialGPT Criterion Annotations | 조건별 판단과 근거 평가 | 2026-08-20 공개 원본 1,015행 연결 |
| TREC Clinical Trials 2021·2022 | 후보 검색과 순위 평가 | 2026-08-21 TrialGPT 공개 검색 재현 완료 |
| 새 ClarifyTrial v5 합성 자료 | 후보 유지, 현재 확정, 다음 행동과 재판정 평가 | 개발 30명과 새 평가 30명, 자연어 근거 상태 기록 구현 |

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

### 자연어 평가용 새 시험

- 받은 날짜: 2026-08-22
- API 버전: 2.0.5
- 원본 자료 시각: 2026-08-21T09:00:05
- 로컬 위치: `.research-cache/clinicaltrials-natural-evaluation-v1`
- 선정 규칙: `configs/natural_evaluation_source_selection_v1.json`
- 검토 초안: `data/natural_evaluation_v1/criterion_review.json`
- AI 전체 검토: `data/natural_evaluation_v1/ai_preliminary_review_polarity_audited.json`
- 단순 규칙 예비 정답: `data/natural_evaluation_v1/ai_preliminary_gold_conservative.json`
- 예비 시험 검토 초안과 사람 검토표: `data/natural_evaluation_v1/reserve_criterion_review.json`,
  `data/natural_evaluation_v1/reserve_reviewer_1.csv`, `reserve_reviewer_2.csv`
- 교체 뒤 예비 시험 구성: `data/natural_evaluation_v1/preliminary_trial_set.json`
- 새 합성 환자 짝: `data/natural_evaluation_v1/preliminary_patient_pairs.json`
- 개발용 합성 자연어 기록: `data/natural_evaluation_v1/preliminary_natural_records.json`
- 질문 정책 확정 뒤 만든 새 합성 환자와 기록: `data/natural_evaluation_v2/`
- 범위: 질환별 본 시험 5건과 예비 5건, 총 30건
- 본 시험의 자동 탐지 문구: 제2형 당뇨병 47개, 유방암 61개, 주요우울장애 56개,
  총 164개
- 사람이 확인할 본 시험 원문: 제2형 당뇨병 74줄, 유방암 105줄,
  주요우울장애 93줄, 총 272줄

모집 중 또는 모집 예정인 중재시험을 공식 API의 최근 갱신 순서로 질환별 100건씩
받았다. 기존 개발 시험 15건, 질환 조건명이 부정문으로만 나타난 시험, 해당 질환이
조건 목록에 없는 시험과 객관적 문구 후보가 4개 미만 또는 25개를 넘는 시험을 제외했다.
남은 시험은 모델 결과를 보기 전에 고정한 해시 순서로 본 시험 5건과 예비 5건을 골랐다.

164개 문구는 숫자, 기간, 측정값과 명시적 상태 표현으로 자동 표시한 줄이다. 자동
탐지가 빠뜨린 조건이 정답에서 사라지지 않도록 두 사람에게는 본 시험의 조건 원문
272줄을 모두 제공한다. 원문 확인이 끝나기 전에는 어느 숫자도 정답 조건 수나 모델
성능 분모로 사용하지 않는다. 전체 원본과 검색 응답은 Git에 넣지 않고, 저장소에는
출처 위치가 있는 검토표만 둔다.

AI 단독 예비 검토는 272줄 가운데 단순 규칙으로 안전하게 옮길 수 있는 59줄에서
62조건을 만들었다. 공개 원문의 글자 위치, 수치, 비교 방향과 단위를 다시 검사했지만
사람 두 명의 독립 확인은 받지 않았다. 따라서 이 파일은 합성 환자 제작용 예비 자료이며
의료 전문가 정답이나 ClinicalTrials.gov의 공식 해석이 아니다.

조건이 부족한 본 시험을 교체하기 위해 이미 저장한 유방암·우울장애 예비 시험 10건의
원문 231줄도 같은 방식으로 검토했다. 새 외부 자료를 받지 않았으며 원래 수집 시각과
ClinicalTrials.gov 이용 조건을 그대로 따른다. 교체 뒤 구성은 15건·92조건이다.
이 조건에서 만든 첫 환자 30명·근거 상태 짝 60회와 질문 정책 확정 뒤 만든 새 환자
30명의 기록은 모두 합성 자료다. 자료 상태에 따른
출처 허용 규칙은 평가용 정책이며 각 임상시험이 특정 기록 출처를 요구한다고 해석하지
않는다.

### 팀 공개 ClinicalTrials.gov 시험 1,931건

- 공개 위치: https://github.com/Seohvvan/Healthcare/blob/main/data/trials.jsonl
- 확인 날짜: 2026-08-24
- 원자료: ClinicalTrials.gov API v2
- 확인 규모: 고유 NCT ID 1,931개, 참가 조건 원문이 있는 시험 1,931개
- 저장 필드: NCT ID, 제목, 질환, 요약, 참가 조건, 성별, 최소·최대 나이, 모집 상태, 단계
- 계획한 용도: 여러 질환에서 관련 시험을 찾는 후보 검색
- 조건별 정답과 환자-시험 정답: 없음

첨부받은 파일과 공개 저장소 파일은 1,931개 JSON 행의 내용이 같고 줄바꿈 방식만
달랐다. 이 모음에는 모집이 끝난 시험도 포함되므로 현재 추천 후보를 만들 때는 모집
상태를 구분한다. 파일 안에는 수집에 사용한 질환 검색어와 정확한 원자료 시각이 들어
있지 않다. 최종 평가에 사용하기 전에는 같은 NCT ID의 최신 모집 상태를 공식 API에서
다시 확인하거나, 재현 가능한 검색어와 수집 시각으로 새 스냅샷을 만든다.

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
