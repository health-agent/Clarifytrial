"""Render a self-contained 16:9 ClarifyTrial presentation demo page."""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path

from render_presentation_terminal_demo import DemoData, DemoDataError, load_demo_data


def _number(value: float) -> str:
    return f"{value:g}"


def _safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def render_web_demo(data: DemoData) -> str:
    old_value = f"{_number(data.historical_value)}{data.historical_unit}"
    new_value = f"{_number(data.value)}{data.unit}"
    first_threshold = f"{_number(data.first.threshold)}{data.unit} 미만"
    second_threshold = f"{_number(data.second.threshold)}{data.unit} 미만"
    payload = {
        "historicalEvidence": {
            "date": data.historical_event_date,
            "value": old_value,
        },
        "newEvidence": {
            "date": data.event_date,
            "value": new_value,
            "source": data.source_label,
        },
        "trials": [
            {
                "trialId": data.first.trial_id,
                "threshold": first_threshold,
                "finalStatus": data.first.final_status,
            },
            {
                "trialId": data.second.trial_id,
                "threshold": second_threshold,
                "finalStatus": data.second.final_status,
            },
        ],
    }
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ClarifyTrial 발표 데모</title>
  <style>
    :root {{
      color-scheme: light;
      --navy: #223957;
      --navy-2: #162a45;
      --blue: #4e78ad;
      --blue-soft: #e9f1fb;
      --teal: #258c82;
      --teal-soft: #e2f4f0;
      --red: #ad4b55;
      --red-soft: #fae9eb;
      --ink: #18263a;
      --muted: #687b95;
      --line: #cbd8e8;
      --paper: #ffffff;
      --canvas: #edf2f8;
      --shadow: 0 16px 34px rgba(29, 52, 82, .12);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ width: 100%; height: 100%; margin: 0; overflow: hidden; }}
    body {{
      display: grid;
      place-items: center;
      background: var(--canvas);
      color: var(--ink);
      font-family: "Pretendard", "Noto Sans KR", "Malgun Gothic", sans-serif;
    }}
    button {{ font: inherit; }}
    .demo {{
      width: min(100vw, 177.7778vh);
      aspect-ratio: 16 / 9;
      padding: 2.6% 3.1% 2.2%;
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      gap: 2.1%;
      background:
        radial-gradient(circle at 89% 10%, rgba(37, 140, 130, .10), transparent 23%),
        linear-gradient(180deg, #fbfdff 0%, #f5f8fc 100%);
      box-shadow: 0 24px 80px rgba(29, 52, 82, .22);
      position: relative;
    }}
    .topbar {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 2rem; }}
    .eyebrow {{ margin: 0 0 .35rem; color: var(--teal); font-size: clamp(12px, 1vw, 20px); font-weight: 800; letter-spacing: .08em; }}
    h1 {{ margin: 0; color: var(--navy-2); font-size: clamp(25px, 2.25vw, 48px); line-height: 1.12; letter-spacing: -.035em; }}
    .controls {{ display: flex; gap: .55rem; align-items: center; }}
    .control {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: .65rem 1rem;
      background: rgba(255,255,255,.88);
      color: var(--navy);
      font-size: clamp(11px, .8vw, 16px);
      font-weight: 750;
      cursor: pointer;
    }}
    .control.primary {{ border-color: var(--navy); background: var(--navy); color: white; }}
    .control:focus-visible {{ outline: 4px solid rgba(78, 120, 173, .28); outline-offset: 2px; }}
    .patient-note {{
      display: grid;
      grid-template-columns: auto 1fr auto;
      align-items: center;
      gap: 1.2rem;
      min-height: 12.5%;
      padding: 1.2% 1.5%;
      border: 1.5px solid #a9bdd5;
      border-radius: 16px;
      background: rgba(255,255,255,.94);
      box-shadow: 0 7px 20px rgba(29,52,82,.08);
    }}
    .patient-title {{ color: var(--navy); font-size: clamp(13px, 1.05vw, 21px); font-weight: 850; white-space: nowrap; }}
    .evidence-row {{ display: flex; flex-wrap: wrap; gap: .55rem 1.1rem; align-items: center; font-size: clamp(12px, .92vw, 19px); }}
    .evidence-chip {{ display: inline-flex; align-items: center; gap: .4rem; border-radius: 999px; padding: .45rem .75rem; background: #f1f5fa; color: var(--muted); font-weight: 680; }}
    .evidence-chip.new {{
      opacity: 0;
      transform: translateY(9px);
      background: var(--teal-soft);
      color: #176c65;
      transition: opacity .45s ease, transform .45s ease;
    }}
    .demo.has-new-evidence .evidence-chip.new {{ opacity: 1; transform: translateY(0); }}
    .synthetic {{ color: var(--muted); font-size: clamp(10px, .72vw, 14px); font-weight: 700; white-space: nowrap; }}
    .pipeline {{
      display: grid;
      grid-template-columns: 1.15fr .10fr .92fr .10fr .92fr .10fr .92fr .10fr 1.18fr;
      align-items: stretch;
      min-height: 0;
    }}
    .card {{
      min-width: 0;
      border: 1.4px solid var(--line);
      border-radius: 18px;
      background: rgba(255,255,255,.92);
      box-shadow: 0 9px 24px rgba(29,52,82,.08);
      opacity: .43;
      transform: translateY(4px);
      transition: opacity .35s ease, transform .35s ease, border-color .35s ease, box-shadow .35s ease;
      overflow: hidden;
    }}
    .card.is-active, .card.is-complete {{ opacity: 1; transform: translateY(0); }}
    .card.is-active {{ border-color: var(--blue); box-shadow: 0 15px 34px rgba(52,91,139,.20); }}
    .card-head {{
      min-height: 21%;
      padding: 6.5% 7%;
      display: flex;
      align-items: center;
      gap: .6rem;
      background: var(--blue-soft);
      color: var(--navy);
      font-size: clamp(13px, 1.05vw, 21px);
      font-weight: 850;
    }}
    .number {{ display: grid; place-items: center; width: 1.75em; height: 1.75em; border-radius: 50%; background: white; color: var(--blue); flex: 0 0 auto; }}
    .card-body {{ height: 79%; padding: 8%; display: flex; flex-direction: column; gap: 7%; justify-content: center; }}
    .candidate .card-head {{ background: #eef3fa; }}
    .trial {{ padding: 6%; border: 1px solid var(--line); border-radius: 13px; background: white; }}
    .trial + .trial {{ margin-top: 7%; }}
    .trial-id {{ color: var(--navy); font-size: clamp(12px, .92vw, 18px); font-weight: 850; }}
    .criterion {{ margin-top: .45rem; color: var(--ink); font-size: clamp(11px, .82vw, 16px); font-weight: 680; line-height: 1.35; }}
    .status {{ margin-top: .45rem; color: var(--muted); font-size: clamp(10px, .72vw, 14px); font-weight: 700; }}
    .fact-name {{ color: var(--navy-2); font-size: clamp(16px, 1.35vw, 27px); font-weight: 900; line-height: 1.2; }}
    .fact-impact {{ color: var(--teal); font-size: clamp(11px, .84vw, 17px); font-weight: 800; }}
    .route-label {{ color: var(--navy); font-size: clamp(14px, 1.18vw, 23px); font-weight: 900; }}
    .route-detail, .recalc-detail {{ color: var(--muted); font-size: clamp(10px, .74vw, 15px); line-height: 1.45; font-weight: 650; }}
    .answer {{
      padding: 8%;
      border-radius: 13px;
      background: var(--teal-soft);
      color: #176c65;
      font-size: clamp(14px, 1.22vw, 25px);
      font-weight: 900;
      text-align: center;
      opacity: 0;
      transform: scale(.94);
      transition: opacity .4s ease, transform .4s ease;
    }}
    .demo.has-new-evidence .answer {{ opacity: 1; transform: scale(1); }}
    .recalc-count {{ color: var(--blue); font-size: clamp(26px, 2.5vw, 50px); line-height: 1; font-weight: 950; }}
    .arrow {{ display: grid; place-items: center; color: #aab9cb; font-size: clamp(22px, 2.1vw, 43px); transition: color .35s ease, transform .35s ease; }}
    .arrow.is-complete {{ color: var(--blue); transform: translateX(4px); }}
    .applications .card-head {{ background: #e4f3ee; color: #176c65; }}
    .result {{ padding: 6%; border-radius: 13px; border: 1px solid var(--line); background: white; }}
    .result + .result {{ margin-top: 7%; }}
    .result-line {{ display: flex; align-items: center; justify-content: space-between; gap: .7rem; }}
    .result-name {{ color: var(--navy); font-size: clamp(11px, .9vw, 18px); font-weight: 850; }}
    .before, .after {{ font-size: clamp(10px, .72vw, 14px); font-weight: 850; }}
    .before {{ color: var(--muted); }}
    .after {{ display: none; border-radius: 999px; padding: .32rem .55rem; }}
    .after.remove {{ background: var(--red-soft); color: var(--red); }}
    .after.confirm {{ background: var(--teal-soft); color: #176c65; }}
    .demo.is-finished .before {{ display: none; }}
    .demo.is-finished .after {{ display: inline-flex; }}
    .result-reason {{ margin-top: .5rem; color: var(--muted); font-size: clamp(9px, .66vw, 13px); line-height: 1.35; }}
    .shared-evidence {{
      margin-top: auto;
      padding: 6%;
      border-radius: 13px;
      background: #f2f6fb;
      color: var(--muted);
      font-size: clamp(9px, .68vw, 14px);
      font-weight: 700;
      line-height: 1.4;
      opacity: 0;
      transition: opacity .4s ease;
    }}
    .demo.is-finished .shared-evidence {{ opacity: 1; }}
    .footer {{ display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 1rem; }}
    .takeaway {{
      padding: .85% 1.3%;
      border-radius: 12px;
      background: var(--navy);
      color: white;
      font-size: clamp(12px, 1vw, 20px);
      font-weight: 800;
      text-align: center;
    }}
    .progress {{ color: var(--muted); font-size: clamp(10px, .72vw, 14px); font-weight: 800; min-width: 8em; text-align: right; }}
    .sr-only {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }}
    @media (max-width: 1000px) {{ .demo {{ padding: 2.2%; }} .controls {{ gap: .25rem; }} .control {{ padding: .45rem .65rem; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ transition-duration: .001ms !important; animation-duration: .001ms !important; }} }}
  </style>
</head>
<body>
  <main class="demo" id="demo" data-stage="0" aria-label="ClarifyTrial 질문 뒤 재판정 데모">
    <header class="topbar">
      <div>
        <p class="eyebrow">DEMO · 합성 사례</p>
        <h1>한 번 확인한 정보로 두 시험을 각각 다시 판단</h1>
      </div>
      <nav class="controls" aria-label="데모 조작">
        <button class="control primary" id="play" type="button">자동 재생</button>
        <button class="control" id="next" type="button">다음</button>
        <button class="control" id="reset" type="button">처음</button>
        <button class="control" id="fullscreen" type="button">전체 화면</button>
      </nav>
    </header>

    <section class="patient-note" aria-label="합성 환자 상태">
      <div class="patient-title">환자 상태</div>
      <div class="evidence-row">
        <span class="evidence-chip">기존 기록 · {escape(data.historical_event_date)} · 당화혈색소 · HbA1c {escape(old_value)} · 최신 기준에는 오래됨</span>
        <span class="evidence-chip new">새 근거 · {escape(data.event_date)} · {escape(data.source_label)} · 당화혈색소 · HbA1c {escape(new_value)}</span>
      </div>
      <div class="synthetic">SYNTHETIC · 외부 모델 호출 없음</div>
    </section>

    <section class="pipeline" aria-label="ClarifyTrial 데모 실행 흐름">
      <article class="card candidate" data-step="0">
        <div class="card-head">임상시험 후보</div>
        <div class="card-body">
          <div class="trial">
            <div class="trial-id">{escape(data.first.trial_id)}</div>
            <div class="criterion">최근 14일 공식 당화혈색소 · {escape(first_threshold)}</div>
            <div class="status">후보 유지 · 추가 확인 필요</div>
          </div>
          <div class="trial">
            <div class="trial-id">{escape(data.second.trial_id)}</div>
            <div class="criterion">최근 14일 공식 당화혈색소 · {escape(second_threshold)}</div>
            <div class="status">후보 유지 · 추가 확인 필요</div>
          </div>
        </div>
      </article>
      <div class="arrow" data-arrow="1" aria-hidden="true">→</div>

      <article class="card" data-step="1">
        <div class="card-head"><span class="number">1</span>공통 정보 선택</div>
        <div class="card-body">
          <div class="fact-name">최근 공식<br>당화혈색소 결과</div>
          <div class="fact-impact">미확정 시험 2개 · 조건 2개</div>
          <div class="route-detail">두 시험이 같은 한 정보를 기다리고 있어 한 번만 확인합니다.</div>
        </div>
      </article>
      <div class="arrow" data-arrow="2" aria-hidden="true">→</div>

      <article class="card" data-step="2">
        <div class="card-head"><span class="number">2</span>확인 방법</div>
        <div class="card-body">
          <div class="route-label">기존 공식 결과</div>
          <div class="route-detail">새 검사 없음 · 추가 방문 없음<br>{escape(data.question)}</div>
          <div class="answer">{escape(new_value)}<br><small>{escape(data.event_date)}</small></div>
        </div>
      </article>
      <div class="arrow" data-arrow="3" aria-hidden="true">→</div>

      <article class="card" data-step="3">
        <div class="card-head"><span class="number">3</span>관련 조건 재판정</div>
        <div class="card-body">
          <div class="recalc-count">2 / 2</div>
          <div class="recalc-detail">새 근거와 연결된 조건 두 개만 코드로 다시 계산합니다.</div>
          <div class="fact-impact">다른 조건은 다시 계산하지 않음</div>
        </div>
      </article>
      <div class="arrow" data-arrow="4" aria-hidden="true">→</div>

      <article class="card applications" data-step="3">
        <div class="card-head">판단 갱신</div>
        <div class="card-body">
          <div class="result">
            <div class="result-line">
              <span class="result-name">{escape(data.first.trial_id)}</span>
              <span class="before">추가 확인 필요</span>
              <span class="after remove">조건 불충족</span>
            </div>
            <div class="result-reason">{escape(new_value)}는 {escape(first_threshold)} 기준을 충족하지 않음</div>
          </div>
          <div class="result">
            <div class="result-line">
              <span class="result-name">{escape(data.second.trial_id)}</span>
              <span class="before">추가 확인 필요</span>
              <span class="after confirm">확인 완료</span>
            </div>
            <div class="result-reason">{escape(new_value)}는 {escape(second_threshold)} 기준을 충족</div>
          </div>
          <div class="shared-evidence">같은 공식 결과를 두 시험이 함께 사용<br>{escape(data.source_label)} · {escape(data.event_date)}</div>
        </div>
      </article>
    </section>

    <footer class="footer">
      <div class="takeaway">공통 정보는 한 번만 확인하고, 시험마다 다른 기준으로 다시 판단합니다.</div>
      <div class="progress" id="progress" aria-live="polite">시작 상태 · 1/4</div>
    </footer>
    <div class="sr-only" id="announcement" aria-live="assertive"></div>
  </main>

  <script id="demo-data" type="application/json">{_safe_json(payload)}</script>
  <script>
    (() => {{
      const demo = document.getElementById('demo');
      const progress = document.getElementById('progress');
      const announcement = document.getElementById('announcement');
      const playButton = document.getElementById('play');
      const nextButton = document.getElementById('next');
      const resetButton = document.getElementById('reset');
      const fullscreenButton = document.getElementById('fullscreen');
      const labels = ['시작 상태', '공통 정보 선택', '확인 결과 반영', '관련 조건 재판정'];
      let stage = 0;
      let startupTimer = null;
      let timer = null;

      function stop() {{
        if (startupTimer !== null) window.clearTimeout(startupTimer);
        if (timer !== null) window.clearInterval(timer);
        startupTimer = null;
        timer = null;
        playButton.textContent = '자동 재생';
      }}

      function setStage(value) {{
        stage = Math.max(0, Math.min(3, value));
        demo.dataset.stage = String(stage);
        demo.classList.toggle('has-new-evidence', stage >= 2);
        demo.classList.toggle('is-finished', stage >= 3);
        demo.querySelectorAll('[data-step]').forEach((element) => {{
          const elementStage = Number(element.dataset.step);
          element.classList.toggle('is-active', elementStage === stage);
          element.classList.toggle('is-complete', elementStage < stage);
        }});
        demo.querySelectorAll('[data-arrow]').forEach((element) => {{
          element.classList.toggle('is-complete', Number(element.dataset.arrow) <= stage);
        }});
        progress.textContent = `${{labels[stage]}} · ${{stage + 1}}/4`;
        announcement.textContent = labels[stage];
        if (stage === 3) stop();
      }}

      function next() {{ setStage(stage === 3 ? 0 : stage + 1); }}

      function play() {{
        stop();
        setStage(0);
        playButton.textContent = '재생 중';
        startupTimer = window.setTimeout(() => {{
          startupTimer = null;
          setStage(1);
        }}, 700);
        timer = window.setInterval(() => {{
          if (stage >= 3) {{ stop(); return; }}
          setStage(stage + 1);
        }}, 3000);
      }}

      playButton.addEventListener('click', play);
      nextButton.addEventListener('click', () => {{ stop(); next(); }});
      resetButton.addEventListener('click', () => {{ stop(); setStage(0); }});
      fullscreenButton.addEventListener('click', async () => {{
        if (document.fullscreenElement) await document.exitFullscreen();
        else await demo.requestFullscreen();
      }});
      document.addEventListener('keydown', (event) => {{
        if (event.key === 'ArrowRight' || event.key === ' ') {{ event.preventDefault(); stop(); next(); }}
        if (event.key === 'Home') {{ event.preventDefault(); stop(); setStage(0); }}
      }});
      document.addEventListener('fullscreenchange', () => {{
        fullscreenButton.textContent = document.fullscreenElement ? '전체 화면 종료' : '전체 화면';
      }});
      setStage(0);
      if (new URLSearchParams(window.location.search).get('autoplay') === '1') play();
    }})();
  </script>
</body>
</html>
"""


def render(input_path: Path, output_path: Path) -> Path:
    data = load_demo_data(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_web_demo(data), encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("runs/presentation-demo-interactive-20260830/result.json"),
        help="Saved interactive screening result JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/internal/demo/clarifytrial-presentation-demo.html"),
        help="Output self-contained HTML path.",
    )
    args = parser.parse_args()
    try:
        path = render(args.input, args.output)
    except DemoDataError as error:
        parser.exit(2, f"오류: {error}\n")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
