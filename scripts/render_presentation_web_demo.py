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
    selected_delay = f"{_number(data.selected_delay_hours)}시간"
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
        "workflow": {
            "consideredOptionIds": list(data.considered_option_ids),
            "selectedOptionId": data.selected_option_id,
            "selectedDelayHours": data.selected_delay_hours,
            "removedOption": {
                "optionId": data.removed_option_id,
                "reason": data.removed_option_reason,
            },
            "stopReason": data.stop_reason,
        },
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
      --amber: #9a6b1d;
      --amber-soft: #fff4d8;
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
      padding: 2.35% 3.05% 1.9%;
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      gap: 1.55%;
      background:
        radial-gradient(circle at 89% 10%, rgba(37, 140, 130, .10), transparent 23%),
        linear-gradient(180deg, #fbfdff 0%, #f5f8fc 100%);
      box-shadow: 0 24px 80px rgba(29, 52, 82, .22);
      position: relative;
    }}
    .topbar {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 2rem; }}
    .eyebrow {{ margin: 0 0 .28rem; color: var(--teal); font-size: clamp(12px, .92vw, 19px); font-weight: 850; letter-spacing: .08em; }}
    h1 {{ margin: 0; color: var(--navy-2); font-size: clamp(24px, 2.05vw, 43px); line-height: 1.12; letter-spacing: -.035em; }}
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
    .state-bar {{
      display: grid;
      grid-template-columns: auto 1fr auto;
      align-items: center;
      gap: 1.2rem;
      padding: .95% 1.4%;
      border: 1.5px solid #a9bdd5;
      border-radius: 16px;
      background: rgba(255,255,255,.94);
      box-shadow: 0 7px 20px rgba(29,52,82,.08);
    }}
    .state-title {{ color: var(--navy); font-size: clamp(13px, 1vw, 20px); font-weight: 850; white-space: nowrap; }}
    .evidence-row {{ display: flex; flex-wrap: wrap; gap: .55rem 1.1rem; align-items: center; font-size: clamp(12px, .92vw, 19px); }}
    .evidence-chip {{ display: inline-flex; align-items: center; gap: .4rem; border-radius: 999px; padding: .38rem .7rem; background: #f1f5fa; color: var(--muted); font-weight: 680; }}
    .evidence-chip.new {{
      opacity: 0;
      transform: translateY(9px);
      background: var(--teal-soft);
      color: #176c65;
      transition: opacity .45s ease, transform .45s ease;
    }}
    .demo.has-new-evidence .evidence-chip.new {{ opacity: 1; transform: translateY(0); }}
    .context-stack {{ display: grid; gap: .2rem; justify-items: end; color: var(--muted); font-size: clamp(9px, .66vw, 13px); font-weight: 700; white-space: nowrap; }}
    .context-stack strong {{ color: var(--amber); font-weight: 850; }}
    .workflow {{
      min-height: 0;
      padding: 1.1% 1.15% .7%;
      display: grid;
      grid-template-rows: auto 1fr 14%;
      gap: 1.5%;
      border: 1.5px solid #bacbe0;
      border-radius: 20px;
      background: rgba(255,255,255,.72);
      box-shadow: 0 14px 34px rgba(29,52,82,.10);
    }}
    .workflow-heading {{ display: flex; align-items: center; justify-content: space-between; gap: 1rem; }}
    .workflow-title {{ display: flex; align-items: center; gap: .7rem; min-width: 0; }}
    .workflow-badge {{
      padding: .42rem .75rem;
      border-radius: 999px;
      background: var(--navy);
      color: white;
      font-size: clamp(11px, .78vw, 16px);
      font-weight: 850;
      white-space: nowrap;
    }}
    .workflow-note {{ color: var(--muted); font-size: clamp(10px, .72vw, 14px); font-weight: 680; }}
    .phase-list {{ color: var(--blue); font-size: clamp(10px, .74vw, 15px); font-weight: 850; white-space: nowrap; }}
    .loop-grid {{
      display: grid;
      grid-template-columns: 1.04fr .08fr 1.18fr .08fr .94fr .08fr 1.18fr;
      align-items: stretch;
      min-height: 0;
    }}
    .card {{
      min-width: 0;
      border: 1.4px solid var(--line);
      border-radius: 16px;
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
      min-height: 18%;
      padding: 4.8% 6%;
      display: flex;
      align-items: center;
      gap: .6rem;
      background: var(--blue-soft);
      color: var(--navy);
      font-size: clamp(13px, 1.05vw, 21px);
      font-weight: 850;
    }}
    .number {{ display: grid; place-items: center; width: 1.72em; height: 1.72em; border-radius: 50%; background: white; color: var(--blue); flex: 0 0 auto; }}
    .role {{ margin-left: auto; color: var(--muted); font-size: clamp(8px, .58vw, 12px); font-weight: 750; }}
    .card-body {{ height: 82%; padding: 5.5% 6%; display: flex; flex-direction: column; gap: 4.5%; justify-content: center; }}
    .observe .card-head {{ background: #eef3fa; }}
    .state-summary {{ display: grid; grid-template-columns: 1fr 1fr; gap: .45rem; }}
    .summary-cell {{ padding: .55rem; border-radius: 10px; background: #f2f6fb; text-align: center; }}
    .summary-value {{ display: block; color: var(--blue); font-size: clamp(17px, 1.35vw, 27px); line-height: 1; font-weight: 950; }}
    .summary-label {{ display: block; margin-top: .25rem; color: var(--muted); font-size: clamp(8px, .6vw, 12px); font-weight: 750; }}
    .trial {{ padding: 4.5%; border: 1px solid var(--line); border-radius: 11px; background: white; }}
    .trial + .trial {{ margin-top: 3.5%; }}
    .trial-line {{ display: flex; align-items: center; justify-content: space-between; gap: .5rem; }}
    .trial-id {{ color: var(--navy); font-size: clamp(10px, .8vw, 16px); font-weight: 850; }}
    .criterion {{ color: var(--ink); font-size: clamp(9px, .67vw, 13px); font-weight: 700; }}
    .status {{ margin-top: .25rem; color: var(--muted); font-size: clamp(8px, .58vw, 12px); font-weight: 700; }}
    .decision-block {{ padding: 4.5%; border-radius: 11px; border: 1px solid var(--line); background: white; }}
    .decision-label {{ color: var(--blue); font-size: clamp(8px, .6vw, 12px); font-weight: 900; letter-spacing: .04em; }}
    .decision-value {{ margin-top: .22rem; color: var(--navy-2); font-size: clamp(13px, 1vw, 20px); font-weight: 900; line-height: 1.2; }}
    .decision-reason {{ margin-top: .26rem; color: var(--muted); font-size: clamp(8px, .62vw, 12px); font-weight: 680; line-height: 1.35; }}
    .routes {{ display: grid; gap: .38rem; }}
    .route {{ display: grid; grid-template-columns: auto 1fr; gap: .45rem; align-items: center; padding: .45rem .55rem; border-radius: 9px; font-size: clamp(8px, .61vw, 12px); font-weight: 750; }}
    .route strong {{ display: block; color: inherit; }}
    .route span {{ color: var(--muted); font-size: .9em; }}
    .route.selected {{ background: var(--teal-soft); color: #176c65; }}
    .route.removed {{ background: #f2f4f7; color: #7a8797; }}
    .tool-name {{ color: var(--navy-2); font-size: clamp(14px, 1.12vw, 22px); font-weight: 900; }}
    .tool-detail {{ color: var(--muted); font-size: clamp(9px, .66vw, 13px); line-height: 1.4; font-weight: 680; }}
    .answer {{
      padding: 6%;
      border-radius: 11px;
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
    .tool-status {{ color: var(--teal); font-size: clamp(9px, .68vw, 14px); font-weight: 850; opacity: 0; transition: opacity .4s ease; }}
    .demo.has-new-evidence .tool-status {{ opacity: 1; }}
    .arrow {{ display: grid; place-items: center; color: #aab9cb; font-size: clamp(22px, 2.1vw, 43px); transition: color .35s ease, transform .35s ease; }}
    .arrow.is-complete {{ color: var(--blue); transform: translateX(4px); }}
    .update .card-head {{ background: #e4f3ee; color: #176c65; }}
    .recalc {{ padding: 4%; border-radius: 10px; background: #f1f6fb; color: var(--navy); font-size: clamp(9px, .68vw, 14px); font-weight: 800; line-height: 1.35; }}
    .recalc strong {{ color: var(--blue); font-size: 1.25em; }}
    .result {{ padding: 4.5%; border-radius: 11px; border: 1px solid var(--line); background: white; }}
    .result + .result {{ margin-top: 3.5%; }}
    .result-line {{ display: flex; align-items: center; justify-content: space-between; gap: .7rem; }}
    .result-name {{ color: var(--navy); font-size: clamp(11px, .9vw, 18px); font-weight: 850; }}
    .before, .after {{ font-size: clamp(10px, .72vw, 14px); font-weight: 850; }}
    .before {{ color: var(--muted); }}
    .after {{ display: none; border-radius: 999px; padding: .32rem .55rem; }}
    .after.remove {{ background: var(--red-soft); color: var(--red); }}
    .after.confirm {{ background: var(--teal-soft); color: #176c65; }}
    .demo.is-updated .before {{ display: none; }}
    .demo.is-updated .after {{ display: inline-flex; }}
    .result-reason {{ margin-top: .28rem; color: var(--muted); font-size: clamp(8px, .58vw, 12px); line-height: 1.3; }}
    .state-change {{ margin-top: auto; padding: .45rem; border-radius: 10px; background: var(--teal-soft); color: #176c65; text-align: center; font-size: clamp(9px, .66vw, 13px); font-weight: 900; opacity: 0; transition: opacity .4s ease; }}
    .demo.is-updated .state-change {{ opacity: 1; }}
    .loopback {{ position: relative; min-height: 0; opacity: .48; transition: opacity .4s ease; }}
    .loopback svg {{ position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }}
    .loop-path {{ fill: none; stroke: #aebed1; stroke-width: 2.4; stroke-linecap: round; transition: stroke .4s ease, stroke-width .4s ease; }}
    .loopback.is-active {{ opacity: 1; }}
    .loopback.is-active .loop-path {{ stroke: var(--teal); stroke-width: 3.2; }}
    .loop-copy {{ position: absolute; left: 9%; right: 2%; top: 3%; display: flex; align-items: center; justify-content: space-between; gap: 1rem; color: var(--muted); font-size: clamp(9px, .65vw, 13px); font-weight: 750; }}
    .loop-copy > span {{ padding: .18rem .42rem; border-radius: 6px; background: rgba(255,255,255,.92); }}
    .loop-copy strong {{ padding: .38rem .7rem; border-radius: 999px; background: #edf2f7; color: var(--muted); white-space: nowrap; transition: background .4s ease, color .4s ease; }}
    .loopback.is-active .loop-copy strong {{ background: var(--teal-soft); color: #176c65; }}
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
    @media (max-width: 1000px) {{ .demo {{ padding: 2.1%; }} .controls {{ gap: .25rem; }} .control {{ padding: .42rem .6rem; }} .workflow-note {{ display: none; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ transition-duration: .001ms !important; animation-duration: .001ms !important; }} }}
  </style>
</head>
<body>
  <main class="demo" id="demo" data-stage="0" aria-label="ClarifyTrial 에이전트 순환 데모">
    <header class="topbar">
      <div>
        <p class="eyebrow">DEMO · 상태 기반 실행</p>
        <h1>상태를 보고, 다음 행동을 고르고, 다시 판단</h1>
      </div>
      <nav class="controls" aria-label="데모 조작">
        <button class="control primary" id="play" type="button">자동 재생</button>
        <button class="control" id="next" type="button">다음</button>
        <button class="control" id="reset" type="button">처음</button>
        <button class="control" id="fullscreen" type="button">전체 화면</button>
      </nav>
    </header>

    <section class="state-bar" aria-label="합성 환자와 시험의 공유 상태">
      <div class="state-title">공유 상태</div>
      <div class="evidence-row">
        <span class="evidence-chip">기존 기록 · {escape(data.historical_event_date)} · 당화혈색소 · HbA1c {escape(old_value)} · 최신 기준에는 오래됨</span>
        <span class="evidence-chip new">새 근거 · {escape(data.event_date)} · {escape(data.source_label)} · 당화혈색소 · HbA1c {escape(new_value)}</span>
      </div>
      <div class="context-stack"><span>합성 사례 · 외부 모델 호출 없음</span><strong>환자 제한 · 새 검사·추가 방문 없음</strong></div>
    </section>

    <section class="workflow" aria-label="ClarifyTrial 에이전트 워크플로우">
      <div class="workflow-heading">
        <div class="workflow-title">
          <span class="workflow-badge">ClarifyTrial 에이전트 워크플로우</span>
          <span class="workflow-note">상태가 바뀔 때마다 다음 단계를 다시 정합니다.</span>
        </div>
        <div class="phase-list">상태 → 결정 → 도구 → 갱신 → 반복 판단</div>
      </div>

      <div class="loop-grid">
      <article class="card observe" data-step="0">
        <div class="card-head"><span class="number">1</span>현재 상태 읽기 <span class="role">공유 상태</span></div>
        <div class="card-body">
          <div class="state-summary">
            <div class="summary-cell"><span class="summary-value">2</span><span class="summary-label">미확정 시험</span></div>
            <div class="summary-cell"><span class="summary-value">1</span><span class="summary-label">필요한 정보</span></div>
          </div>
          <div class="trial">
            <div class="trial-line"><span class="trial-id">{escape(data.first.trial_id)}</span><span class="criterion">{escape(first_threshold)}</span></div>
            <div class="status">후보 유지 · 추가 확인 필요</div>
          </div>
          <div class="trial">
            <div class="trial-line"><span class="trial-id">{escape(data.second.trial_id)}</span><span class="criterion">{escape(second_threshold)}</span></div>
            <div class="status">후보 유지 · 추가 확인 필요</div>
          </div>
        </div>
      </article>
      <div class="arrow" data-arrow="1" aria-hidden="true">→</div>

      <article class="card" data-step="1">
        <div class="card-head"><span class="number">2</span>다음 행동 결정 <span class="role">계획 규칙</span></div>
        <div class="card-body">
          <div class="decision-block">
            <div class="decision-label">무엇을 확인할까?</div>
            <div class="decision-value">최근 공식 당화혈색소</div>
            <div class="decision-reason">시험 2개 · 조건 2개에 함께 필요</div>
          </div>
          <div class="decision-block">
            <div class="decision-label">어떻게 확인할까?</div>
            <div class="routes">
              <div class="route selected"><strong>✓ 기존 공식 결과</strong><span>예상 {escape(selected_delay)} · 방문 없음</span></div>
              <div class="route removed"><strong>× 새 검사</strong><span>환자 제한에 맞지 않아 제외</span></div>
            </div>
          </div>
        </div>
      </article>
      <div class="arrow" data-arrow="2" aria-hidden="true">→</div>

      <article class="card" data-step="2">
        <div class="card-head"><span class="number">3</span>확인 실행 <span class="role">확인 도구</span></div>
        <div class="card-body">
          <div class="tool-name">기존 공식 결과 확인</div>
          <div class="tool-detail">요청: {escape(data.question)}<br>새 검사 없음 · 추가 방문 없음</div>
          <div class="answer">{escape(new_value)}<br><small>{escape(data.event_date)}</small></div>
          <div class="tool-status">새 근거 1개를 공유 상태에 저장</div>
        </div>
      </article>
      <div class="arrow" data-arrow="3" aria-hidden="true">→</div>

      <article class="card update" data-step="3">
        <div class="card-head"><span class="number">4</span>상태 갱신 <span class="role">판정 코드</span></div>
        <div class="card-body">
          <div class="recalc">새 근거와 연결된 조건 <strong>2개</strong>를 다시 계산</div>
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
          <div class="state-change">미확정 시험 2개 → 0개</div>
        </div>
      </article>
      </div>

      <div class="loopback" data-loop="4" aria-label="반복 또는 종료 판단">
        <svg viewBox="0 0 1200 80" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <marker id="loop-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#258c82"/>
            </marker>
          </defs>
          <path class="loop-path" d="M 1145 4 C 1145 65, 55 65, 55 9" marker-end="url(#loop-arrow)"/>
        </svg>
        <div class="loop-copy">
          <span>미확정이 남으면 갱신된 상태로 돌아가 다음 행동을 다시 고릅니다.</span>
          <strong>이번 사례: 미확정 0개 · 종료</strong>
        </div>
      </div>
    </section>

    <footer class="footer">
      <div class="takeaway">상태를 보고 행동을 고르고, 도구가 돌려준 근거로 상태를 바꾼 뒤 계속할지 다시 판단합니다.</div>
      <div class="progress" id="progress" aria-live="polite">현재 상태 읽기 · 1/5</div>
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
      const labels = ['현재 상태 읽기', '다음 행동 결정', '확인 실행', '상태 갱신', '반복 또는 종료 판단'];
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
        stage = Math.max(0, Math.min(4, value));
        demo.dataset.stage = String(stage);
        demo.classList.toggle('has-new-evidence', stage >= 2);
        demo.classList.toggle('is-updated', stage >= 3);
        demo.classList.toggle('is-finished', stage >= 4);
        demo.querySelectorAll('[data-step]').forEach((element) => {{
          const elementStage = Number(element.dataset.step);
          element.classList.toggle('is-active', elementStage === stage);
          element.classList.toggle('is-complete', elementStage < stage);
        }});
        demo.querySelectorAll('[data-arrow]').forEach((element) => {{
          element.classList.toggle('is-complete', Number(element.dataset.arrow) <= stage);
        }});
        demo.querySelectorAll('[data-loop]').forEach((element) => {{
          element.classList.toggle('is-active', Number(element.dataset.loop) <= stage);
        }});
        progress.textContent = `${{labels[stage]}} · ${{stage + 1}}/5`;
        announcement.textContent = labels[stage];
        if (stage === 4) stop();
      }}

      function next() {{ setStage(stage === 4 ? 0 : stage + 1); }}

      function play() {{
        stop();
        setStage(0);
        playButton.textContent = '재생 중';
        startupTimer = window.setTimeout(() => {{
          startupTimer = null;
          setStage(1);
        }}, 700);
        timer = window.setInterval(() => {{
          if (stage >= 4) {{ stop(); return; }}
          setStage(stage + 1);
        }}, 2700);
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
        default=Path(
            "runs/presentation-demo-agent-loop-patient-aware-20260830/result.json"
        ),
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
