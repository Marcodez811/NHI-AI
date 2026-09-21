const pptxgen = require("pptxgenjs");

async function main() {
  const [output, heatmap, dualAxis, logo, correctedValue = "3.6"] = process.argv.slice(2);
  if (!output || !heatmap || !dualAxis) throw new Error("expected output PPTX and two chart PNG paths");
  const pptx = new pptxgen();
  pptx.layout = "LAYOUT_WIDE";
  pptx.author = "NHI-AI integration test";
  pptx.subject = "Mixed chart rendering fixture";
  pptx.title = "圖表與引用整合測試";
  pptx.lang = "zh-TW";
  pptx.theme = { headFontFace: "Noto Sans CJK TC", bodyFontFace: "Noto Sans CJK TC", lang: "zh-TW" };

  const footer = (slide, value) => slide.addText(value, { x: 0.75, y: 7.05, w: 11.8, h: 0.2, fontFace: "Noto Sans CJK TC", fontSize: 9, color: "617385", margin: 0 });
  const title = (slide, value) => slide.addText(value, { x: 0.75, y: 0.45, w: 11.8, h: 0.55, fontFace: "Noto Sans CJK TC", fontSize: 26, bold: true, color: "16324F", margin: 0 });

  let slide = pptx.addSlide();
  slide.background = { color: "FFFFFF" };
  slide.addText("圖表與引用整合測試", { x: 1.1, y: 2.5, w: 11.1, h: 0.8, fontFace: "Noto Sans CJK TC", fontSize: 32, bold: true, align: "center", color: "16324F", margin: 0 });
  if (logo) slide.addImage({ path: logo, x: 10.9, y: 0.42, w: 1.6, h: 0.3694 });
  slide.addNotes("封面不含資料引用或工作流程說明。");

  slide = pptx.addSlide();
  title(slide, "原生可編輯圖表");
  slide.addChart("bar", [{ name: "件數", labels: ["112年", "113年", "114年"], values: [80, 100, 120] }], { x: 1.2, y: 1.35, w: 10.8, h: 4.9, catAxisLabelFontFace: "Noto Sans CJK TC", valAxisLabelFontFace: "Noto Sans CJK TC", showLegend: false, showTitle: true, title: "年度申報件數", showValue: true });
  footer(slide, "資料來源：年度報告.pdf，PDF 第 12 頁");
  slide.addNotes("主張：年度件數為80、100、120件。\n資料來源：年度報告.pdf，PDF 第 12 頁\n單位：件。");

  slide = pptx.addSlide();
  title(slide, "資料圖像：熱圖");
  slide.addImage({ path: heatmap, x: 1.0, y: 1.25, w: 8, h: 4.5 });
  footer(slide, "資料來源：政策說明.docx，〈給付範圍〉");
  slide.addNotes(`熱圖值：1.2、2.4、${correctedValue}、2.1、1.3、3.0、4.2、2.8、1.1。\n資料來源：政策說明.docx，〈給付範圍〉。\n單位：示範分數；無遺漏值。`);

  slide = pptx.addSlide();
  title(slide, "資料圖像：雙軸組合圖");
  slide.addImage({ path: dualAxis, x: 1.0, y: 1.25, w: 8, h: 4.5 });
  footer(slide, "資料來源：政策附件.md，〈年度趨勢〉，第 20–28 行");
  slide.addNotes("案件量：80、100、120件；比率：6%、9%、11%。\n資料來源：政策附件.md，〈年度趨勢〉，第 20–28 行。\n無遺漏值；比率分母依來源定義。");

  await pptx.writeFile({ fileName: output });
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
