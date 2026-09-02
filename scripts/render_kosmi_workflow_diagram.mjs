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
const H = 620;
const ink = "#000000";
const pale = "#FFFFFF";
const tint = "#FFFFFF";
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
    strokeWidth = 2,
    radius = 8,
    dash = "",
  } = options;
  return `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${radius}" fill="${fill}" stroke="${stroke}" stroke-width="${strokeWidth}"${dash ? ` stroke-dasharray="${dash}"` : ""}/>`;
}

function textLine(x, y, value, options = {}) {
  const {
    size = 28,
    weight = 500,
    anchor = "middle",
    fill = ink,
    letterSpacing = 0,
  } = options;
  return `<text x="${x}" y="${y}" font-family="${font}" font-size="${size}" font-weight="${weight}" fill="${fill}" text-anchor="${anchor}" letter-spacing="${letterSpacing}">${escapeXml(value)}</text>`;
}

function arrow(x1, y1, x2, y2, options = {}) {
  const { width = 2.2, label = "", labelX = (x1 + x2) / 2, labelY = y1 - 16 } = options;
  return [
    `<path d="M ${x1} ${y1} L ${x2} ${y2}" fill="none" stroke="${ink}" stroke-width="${width}" stroke-linecap="round" marker-end="url(#arrow)"/>`,
    label ? textLine(labelX, labelY, label, { size: 27, weight: 500 }) : "",
  ].join("\n");
}

function pathArrow(d, options = {}) {
  const { width = 2.2, dash = "" } = options;
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
    size = 31,
    subtitle = "",
    subtitleSize = 22,
  } = options;
  const fg = dark ? white : ink;
  const titleLines = Array.isArray(lines) ? lines : [lines];
  const lineHeight = 36;
  const subtitleGap = subtitle ? 35 : 0;
  const totalTitleHeight = (titleLines.length - 1) * lineHeight;
  const firstY = y + height / 2 - totalTitleHeight / 2 - subtitleGap / 2 + 13;
  const parts = [
    rect(x, y, width, height, { fill: dark ? ink : fill, stroke: ink, strokeWidth: 2, radius: 8 }),
  ];
  titleLines.forEach((line, index) => {
    parts.push(textLine(x + width / 2, firstY + index * lineHeight, line, { size, weight: 500, fill: fg }));
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
    <marker id="arrow" viewBox="0 0 12 12" refX="10" refY="6" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 1 1 L 11 6 L 1 11 z" fill="${ink}"/>
    </marker>
  </defs>
  <rect width="${W}" height="${H}" fill="${white}"/>

  ${stackedNode(25, 35, 210, 110, ["환자·시험", "입력"], { size: 26 })}
  ${arrow(235, 90, 275, 90)}
  ${stackedNode(275, 35, 250, 110, ["후보 검색", "조건별 판단"], { size: 26 })}
  ${arrow(525, 90, 565, 90)}
  ${stackedNode(565, 35, 305, 110, ["후보 유지 여부", "근거 충분 여부"], { size: 25 })}
  ${arrow(870, 90, 910, 90)}
  ${stackedNode(910, 35, 265, 110, ["결과 저장"], { size: 26 })}

  ${pathArrow("M 650 145 L 650 215 L 170 215 L 170 350")}

  ${rect(25, 250, 1150, 335, { fill: white, strokeWidth: 2, radius: 10 })}
  ${textLine(55, 295, "추가 확인", { size: 26, weight: 500, anchor: "start" })}

  ${stackedNode(55, 360, 230, 130, ["정보 선택"], { size: 26 })}
  ${arrow(285, 425, 335, 425)}
  ${stackedNode(335, 360, 230, 130, ["확인 방법 선택"], { size: 25 })}
  ${arrow(565, 425, 615, 425)}
  ${stackedNode(615, 360, 230, 130, ["정보 확인"], { size: 26 })}
  ${arrow(845, 425, 895, 425)}
  ${stackedNode(895, 360, 230, 130, ["관련 조건", "재판단"], { size: 25 })}

  ${pathArrow("M 1010 360 L 1010 325 L 790 325 L 790 150")}

</svg>`;

await fs.writeFile(`${outputBase}.svg`, svg, "utf8");
await sharp(Buffer.from(svg))
  .resize({ width: W * 2, height: H * 2, fit: "fill" })
  .png({ compressionLevel: 9, adaptiveFiltering: true })
  .toFile(`${outputBase}.png`);

console.log(`${outputBase}.svg`);
console.log(`${outputBase}.png`);
