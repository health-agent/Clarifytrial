import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputBase = path.join(
  root,
  "docs",
  "internal",
  "diagrams",
  "clarifytrial-kosmi-workflow",
);

const runtimeModules = process.env.RUNTIME_NODE_MODULES;
if (!runtimeModules) {
  throw new Error("RUNTIME_NODE_MODULES is required to render the PNG output.");
}
const runtimeRequire = createRequire(path.join(runtimeModules, "runtime-loader.cjs"));
const sharp = runtimeRequire("sharp");

const W = 1200;
const H = 760;
const ink = "#17324D";
const pale = "#F2F5F8";
const tint = "#E6ECF1";
const white = "#FFFFFF";
const font = "Malgun Gothic, Noto Sans KR, sans-serif";

function escapeXml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function rect(x, y, width, height, options = {}) {
  const {
    fill = white,
    stroke = ink,
    strokeWidth = 3,
    radius = 18,
    dash = "",
  } = options;
  return `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="${strokeWidth}"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`;
}

function textLine(x, y, value, options = {}) {
  const {
    size = 34,
    weight = 600,
    anchor = "middle",
    fill = ink,
    letterSpacing = 0,
  } = options;
  return `<text x="${x}" y="${y}" font-family="${font}" font-size="${size}" font-weight="${weight}" fill="${fill}" text-anchor="${anchor}" letter-spacing="${letterSpacing}">${escapeXml(value)}</text>`;
}

function arrow(x1, y1, x2, y2, options = {}) {
  const { width = 4, label = "", labelX = (x1 + x2) / 2, labelY = y1 - 16 } = options;
  return [
    `<path d="M ${x1} ${y1} L ${x2} ${y2}" fill="none" stroke="${ink}" stroke-width="${width}" stroke-linecap="round" marker-end="url(#arrow)"/>`,
    label ? textLine(labelX, labelY, label, { size: 27, weight: 500 }) : "",
  ].join("\n");
}

function pathArrow(d, options = {}) {
  const { width = 4, dash = "" } = options;
  return `<path d="${d}" fill="none" stroke="${ink}" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round"${dash ? ` stroke-dasharray="${dash}"` : ""} marker-end="url(#arrow)"/>`;
}

function node(x, y, width, height, title, subtitle = "", options = {}) {
  const { fill = white, dark = false, titleSize = 38, subtitleSize = 29 } = options;
  const fg = dark ? white : ink;
  const top = subtitle ? y + height / 2 - 9 : y + height / 2 + 13;
  const parts = [
    rect(x, y, width, height, { fill: dark ? ink : fill, stroke: ink, strokeWidth: 3.2, radius: 20 }),
    textLine(x + width / 2, top, title, { size: titleSize, weight: 700, fill: fg }),
  ];
  if (subtitle) {
    parts.push(textLine(x + width / 2, top + 46, subtitle, { size: subtitleSize, weight: 400, fill: fg }));
  }
  return parts.join("\n");
}

function stackedNode(x, y, width, height, lines, options = {}) {
  const {
    fill = white,
    dark = false,
    size = 39,
    subtitle = "",
    subtitleSize = 25,
  } = options;
  const fg = dark ? white : ink;
  const titleLines = Array.isArray(lines) ? lines : [lines];
  const lineHeight = 47;
  const subtitleGap = subtitle ? 35 : 0;
  const totalTitleHeight = (titleLines.length - 1) * lineHeight;
  const firstY = y + height / 2 - totalTitleHeight / 2 - subtitleGap / 2 + 13;
  const parts = [
    rect(x, y, width, height, { fill: dark ? ink : fill, stroke: ink, strokeWidth: 3.2, radius: 18 }),
  ];
  titleLines.forEach((line, index) => {
    parts.push(textLine(x + width / 2, firstY + index * lineHeight, line, { size, weight: 700, fill: fg }));
  });
  if (subtitle) {
    parts.push(textLine(x + width / 2, firstY + totalTitleHeight + 43, subtitle, { size: subtitleSize, weight: 400, fill: fg }));
  }
  return parts.join("\n");
}

const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-labelledby="title desc">
  <title id="title">ClarifyTrial 상호작용형 사전선별 흐름</title>
  <desc id="desc">환자 정보와 후보 시험을 입력해 초기 상태를 만든 뒤, 미정 시험과 확인 기회가 남아 있으면 다음 정보와 허용된 확인 방법을 선택하고 답과 근거를 얻어 연결된 조건만 다시 판단하는 순환 구조다.</desc>
  <defs>
    <marker id="arrow" viewBox="0 0 12 12" refX="10" refY="6" markerWidth="10" markerHeight="10" orient="auto-start-reverse">
      <path d="M 1 1 L 11 6 L 1 11 z" fill="${ink}"/>
    </marker>
  </defs>
  <rect width="${W}" height="${H}" fill="${white}"/>

  ${stackedNode(20, 42, 220, 132, ["환자·시험", "입력"], { fill: pale, size: 39 })}
  ${arrow(240, 108, 270, 108)}
  ${stackedNode(270, 42, 245, 132, ["후보 검색", "초기 판단"], { size: 39 })}
  ${arrow(515, 108, 545, 108)}

  ${rect(545, 28, 315, 160, { fill: tint, strokeWidth: 4, radius: 20 })}
  ${textLine(702.5, 72, "현재 상태", { size: 41, weight: 700 })}
  <line x1="570" y1="91" x2="835" y2="91" stroke="${ink}" stroke-width="2" opacity="0.35"/>
  <line x1="702.5" y1="104" x2="702.5" y2="143" stroke="${ink}" stroke-width="2" opacity="0.35"/>
  ${textLine(624, 132, "후보 유지", { size: 27, weight: 600 })}
  ${textLine(781, 132, "확인 상태", { size: 27, weight: 600 })}
  ${textLine(702.5, 168, "미정 · 부족 정보 · 남은 횟수", { size: 22, weight: 400 })}

  ${arrow(860, 108, 890, 108, { label: "종료", labelX: 1015, labelY: 30 })}
  ${stackedNode(890, 42, 290, 132, ["결과·근거", "저장"], { dark: true, size: 39 })}

  ${pathArrow("M 630 188 L 630 250 L 165 250 L 165 410", { width: 4 })}
  ${textLine(405, 233, "미정 시험 · 확인 횟수 남음", { size: 27, weight: 500 })}

  ${rect(20, 305, 1160, 425, { fill: pale, strokeWidth: 3, radius: 26 })}
  ${textLine(55, 355, "추가 확인 순환", { size: 39, weight: 700, anchor: "start" })}
  ${textLine(1135, 354, "새 상태에서 반복", { size: 27, weight: 500, anchor: "end" })}

  ${stackedNode(45, 420, 240, 175, ["다음 정보", "선택"], { size: 39, subtitle: "영향 범위 비교", subtitleSize: 24 })}
  ${arrow(285, 507, 330, 507)}
  ${stackedNode(330, 420, 240, 175, ["확인 경로", "선택"], { size: 39, subtitle: "환자 제약 반영", subtitleSize: 24 })}
  ${arrow(570, 507, 615, 507)}
  ${stackedNode(615, 420, 240, 175, ["확인 도구", "실행"], { size: 39, subtitle: "답·근거 획득", subtitleSize: 24 })}
  ${arrow(855, 507, 900, 507)}
  ${stackedNode(900, 420, 240, 175, ["관련 조건만", "갱신"], { fill: tint, size: 39, subtitle: "상태 재계산", subtitleSize: 24 })}

  ${pathArrow("M 1020 420 L 1020 390 L 775 390 L 775 192", { width: 4 })}

  ${textLine(600, 675, "기존 기록 · 환자 답변 · 공식 결과 · 새 검사 · 의료진 확인", { size: 27, weight: 400 })}

</svg>`;

await fs.writeFile(`${outputBase}.svg`, svg, "utf8");
await sharp(Buffer.from(svg))
  .resize({ width: W * 2, height: H * 2, fit: "fill" })
  .png({ compressionLevel: 9, adaptiveFiltering: true })
  .toFile(`${outputBase}.png`);

console.log(`${outputBase}.svg`);
console.log(`${outputBase}.png`);
