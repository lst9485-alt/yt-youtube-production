import PptxGenJS from "/opt/homebrew/lib/node_modules/pptxgenjs/dist/pptxgen.es.js";

const pptx = new PptxGenJS();
pptx.layout = "LAYOUT_16x9";

const BG = "000000";
const YELLOW = "FFED00";
const BLACK = "000000";
const FONT = "Nanum Square";

// ── Slide 1 ──────────────────────────────────────────────
const s1 = pptx.addSlide();
s1.background = { color: BG };

// [A1] 코스피 vs 아파트
s1.addText("코스피 vs 아파트", {
  x: 1, y: 0.8, w: 8, h: 1.6,
  fontSize: 72, bold: true,
  color: BLACK, fill: { color: YELLOW },
  align: "center", valign: "middle",
  fontFace: FONT,
});

// [A2] 1억 있으면 어디에 넣을까?
s1.addText("1억 있으면 어디에 넣을까?", {
  x: 1, y: 3.0, w: 8, h: 1.4,
  fontSize: 54, bold: true,
  color: BLACK, fill: { color: YELLOW },
  align: "center", valign: "middle",
  fontFace: FONT,
});

// ── Slide 2 ──────────────────────────────────────────────
const s2 = pptx.addSlide();
s2.background = { color: BG };

// 상단 라벨
s2.addText("코스피 vs 아파트", {
  x: 2, y: 0.3, w: 6, h: 0.8,
  fontSize: 36, bold: true,
  color: BLACK, fill: { color: YELLOW },
  align: "center", valign: "middle",
  fontFace: FONT,
});

// [A1] 좌측 코스피
s2.addText("코스피\n1년 2배+", {
  x: 0.3, y: 1.5, w: 4.2, h: 2.5,
  fontSize: 52, bold: true,
  color: BLACK, fill: { color: YELLOW },
  align: "center", valign: "middle",
  fontFace: FONT,
});

// [A2] 우측 아파트
s2.addText("수도권 아파트\n10년 2~3배", {
  x: 5.5, y: 1.5, w: 4.2, h: 2.5,
  fontSize: 52, bold: true,
  color: BLACK, fill: { color: YELLOW },
  align: "center", valign: "middle",
  fontFace: FONT,
});

// 중간 구분선 (세로 직사각형)
s2.addShape(pptx.ShapeType.rect, {
  x: 4.9, y: 1.5, w: 0.05, h: 2.5,
  fill: { color: YELLOW },
  line: { color: YELLOW },
});

// ── Save ─────────────────────────────────────────────────
const outPath = "/Users/yunjitaegi/HQ/workspace/Projects/youtube/yt-youtube-production/2026-03-08/ppt-test.pptx";
await pptx.writeFile({ fileName: outPath });
console.log("Saved:", outPath);
