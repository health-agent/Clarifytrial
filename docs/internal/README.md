# ClarifyTrial 문서

현행 실험 수치와 해석 범위는 [검증 결과](CLARIFYTRIAL_VALIDATION_RESULTS.md)에 모여
있다. 개발 과정에서 대체된 내용은 `trash`에 보관돼 있으며 현재 설계의 근거로 쓰지
않는다.

## 읽는 순서와 현행 기준

새 세션은 저장소 `AGENTS.md`를 읽은 뒤 다음 순서로 이어간다.

1. [현재 상태와 인계](CURRENT_STATUS.md) — 활성 요구사항, 연구 맥락, 원고 변경 상태와 다음 행동
2. [최종 포스터 원고](CLARIFYTRIAL_KOSMI_POSTER_MANUSCRIPT.md) — 최신 HWP의 실제 문장·표·참고문헌, 제안의 적용 여부
3. [연구계획 v5](CLARIFYTRIAL_RESEARCH_PLAN_V5.md) — 연구 질문, 기여와 탐색적 확장의 범위
4. [프로그램 설계](CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md) — 코드·모델 역할과 상태 갱신·종료 조건
5. [검증 결과](CLARIFYTRIAL_VALIDATION_RESULTS.md) — 분모, 비교 방법, 결과와 해석 범위
6. [실험자료](CLARIFYTRIAL_DATASETS.md) — 자료 출처와 합성 환자·연결 구조·환자 제한의 구분
   - 확장 연구를 이어갈 때 [통합 논문 원고](CLARIFYTRIAL_EXTENDED_MANUSCRIPT.md)를 추가로 읽는다.
     핵심 방법·합성 검증 원고이며 선정된 HWP를 대체하지 않는다. 새 방법의 기준은 검증 16절,
     이전 평가 중심 종합은 9–15절이다.
7. 발표를 다룰 때 [현행 설명·질의응답과 과거 발표 구성](CLARIFYTRIAL_REPORT_PRESENTATION_PACKET.md)을
   추가로 읽는다. 첫 절만 10월 연구의 현행 설명이며 뒤의 과거 슬라이드 지시는 참고 기록이다.

2026-09-05의 원고 기준은 사용자가 최종본으로 지정한
`C:\Users\kll45\Downloads\Clarifytrial 포스터.hwp`다. 내부 원고에는 02:07:54 저장본을
옮겼다. 이후 원본 변경이 있으면 다시 읽는다. 예전 v2·v3 원고와 PDF, 대화에서 제안만
한 문장은 현행 HWP를 대체하지 않는다. 실험의 원래 조건과 계산은 검증 문서·원자료가
기준이며, 원고에 설명이 빠졌다면 그 차이를 밝힌다.

각 역할에는 위 현행 파일 하나를 유지한다. 새로운 세션이라는 이유로 별도의 현행
핸드오프 파일을 만들지 않는다. 문서에 있는 과거 실행·제출 계획은 새 작업 권한이 아니다.

## 연구와 현재 결과

2026-10-22 사용자가 포스터 선정과 11월 말 발표를 알렸다. 현재 핵심 방법은
[연구계획 14절](CLARIFYTRIAL_RESEARCH_PLAN_V5.md), 구현·합성 실증은
[검증 결과 16절](CLARIFYTRIAL_VALIDATION_RESULTS.md)에 있다. 이전 개발 비교는
8–15절에 보존한다. 선정된 9월 HWP와 새 연구 결과를 구분한다.

| 문서 | 내용 |
|---|---|
| [현재 상태](CURRENT_STATUS.md) | 구현된 범위, 최신 결과와 남은 일 |
| [연구계획 v5](CLARIFYTRIAL_RESEARCH_PLAN_V5.md) | 연구 질문, 실험 방법과 결과 해석 기준 |
| [검증 결과](CLARIFYTRIAL_VALIDATION_RESULTS.md) | 자료 규모, 실행 조건, 수치와 해석 범위 |
| [연구 설명·질의응답](CLARIFYTRIAL_REPORT_PRESENTATION_PACKET.md) | 첫 절은 10월 연구의 쉬운 설명·3분 대본·분모·질의응답. 뒤는 과거 발표 기록. 실제 포스터 조판은 미완료 |
| [KOSMI 포스터 논문 원고](CLARIFYTRIAL_KOSMI_POSTER_MANUSCRIPT.md) | 최종 HWP의 초록·본문·표·그림 연결·참고문헌과 남은 편집 제안 |
| [통합 논문 원고](CLARIFYTRIAL_EXTENDED_MANUSCRIPT.md) | 관측 증거·위험 교정·선택적 재확인의 핵심 방법과 동일 모형 비교의 향상. 미제출·임상 검증 미완료 |

## 프로그램 구조

| 문서 | 내용 |
|---|---|
| [프로그램 설계](CLARIFYTRIAL_AGENT_ARCHITECTURE_REDESIGN.md) | 코드와 언어모델의 역할, 한 사례의 실행 순서와 실패 처리 |
| [시험 검색 구현](CLARIFYTRIAL_RAG_EVALUATION_IMPLEMENTATION_PLAN.md) | 공개 임상시험 검색, 순위 계산과 검색 평가 |
| [실행과 실험 안내](CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md) | 예제 실행, 일괄 평가, 결과표와 그림 생성 명령 |

## 자료와 선행연구

| 문서 | 내용 |
|---|---|
| [실험자료](CLARIFYTRIAL_DATASETS.md) | 공개 시험, 합성 환자와 통제 실험의 구성 |
| [자료 출처](../../DATA_SOURCES.md) | 공개 주소, 받은 날짜, 이용 조건과 저장 위치 |
| [선행연구](RESEARCH_KNOWLEDGE_BASE.md) | 이미 알려진 방법, 가져온 부분과 남은 연구 경계 |
| [논문과 공개 코드](CLARIFYTRIAL_AGENT_SOURCE_INDEX.md) | 논문별 근거, 공개 코드와 현재 구조의 연결 |

## 결과 파일

| 위치 | 내용 |
|---|---|
| [발표용 결과표](results/presentation-evidence-v2/) | 질문 전후 변화, 정보 연결 구조, 환자 제한과 확인 방법 표 |
| [KOSMI 보완 측정](results/kosmi-poster-evidence-v1/) | 적격·부적격 방향별 확인 예산 결과와 환자 단위 비교 |
| [모델 역할 비교](results/independent-new-trial-agent-evaluation-v1/report.md) | 새 시험에서 코드와 언어모델 실행 결과, 호출 수와 토큰 |
| [선정 후 정보 선택 비교](results/acquisition-research-v1/) | 기존 환자 20명의 제약·횟수·선택 손실과 한 단계·전체 예산 계획 비교; 개발 진단 완료 |
| [새 시험과 근거 획득 비교](results/paper-acquisition-v1/) | 완료. 새 평가 시험 24건·합성 기본 벡터 288개에서 경로 품질·분포·방문 민감도. 선택한 부분조건 범위 |
| [같은 원문의 논리 확장](results/paper-logic-v2/) | 탐색 분석 완료. 범위·대안·예외, 시험별 상반된 변화·환자별 첫 후보·방문과 교차 재표집. 자동 NLP 정확도나 새 독립 시험 검증이 아님 |
| [합성 문장의 논리 준비](results/natural-protocol-v1/) | 완료. 저자 문장 15개·실제 모델 30회와 제안/코드 분해. 독립 NLP 표집이 아님 |
| [공개 문구의 준비 범위](results/public-clause-v1/) | 완료. 같은 평가 시험의 24문구·실제 모델 48회. 보류 비용과 후속 개발 재생 |
| [자료원 대조와 공유 오류](results/corroboration-v1/) | 완료. 같은 합성 벡터의 오류·복사본·대조 비용과 사후 자원 상한 |
| [조건별 결론의 자료원 대조](results/direction-corroboration-v1/) | 완료. 균등 오답에서 같은 행동의 판정 완화와 재계획을 분리. 공유 오류·방문·남은 오판 |
| [외부 주석의 원문 준비](results/leaf-protocol-v1/) | 완료. 48문구·실제 추론 96회, 제안/코드 재생과 v9의 개발 수정. 주석과 원문의 차이 보존 |
| [고정 v9의 새 24문구](results/leaf-protocol-holdout-v1/) | 완료. 개발 시험과 분리한 실제 추론 48회. 일반 우위 미재현·보류와 참고식 한계 |
| [위험 기반 선택적 재확인](results/risk-aware-acquisition-v1/README.md) | 완료. 새 24시험·58비교식, 정책 전체 교정과 독립 합성 평가. 같은 모형의 한 단계 대비 +3.10%p·방문 17.34% 감소, 오판·분포 변화 한계 |
| [발표용 대화형 데모](demo/clarifytrial-presentation-demo.html) | 상태 읽기, 다음 행동 결정, 확인 도구 실행, 상태 갱신과 반복·종료 판단을 잇는 합성 실행 |

## 용어

| 표현 | 뜻 |
|---|---|
| 구조화한 조건 | 수치, 날짜, 예·아니오와 논리 관계를 코드가 계산할 수 있게 나눈 참가 조건 |
| 후보 유지 | 현재 정보가 부족해도 참가 가능성이 남아 있어 계속 확인할 시험 |
| 확인 완료 | 현재 확보한 자료로 이번 사전 검토 범위의 조건을 확인한 상태 |
| 추가 확인 필요 | 후보로 남아 있지만 날짜, 출처, 검사나 병력 확인이 더 필요한 상태 |
| 확인 대기 | 허용된 질문 횟수를 썼거나 현재 이용할 수 있는 방법이 없어 답을 기다리는 상태 |
| 외부 언어모델 | GPT나 Claude처럼 문장을 해석하고 생성하는 별도 모델 |

개발 과정의 긴 기록과 대체된 문서는 [보관 자료](trash/)에서 확인할 수 있다.
