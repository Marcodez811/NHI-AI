#!/usr/bin/env node
/* Render source-backed charts as atomic, high-resolution PNG files. */

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const sharp = require("sharp");

const DEFAULT_WIDTH_IN = 8;
const DEFAULT_HEIGHT_IN = 4.5;
const DEFAULT_PPI = 200;
const MAX_PIXELS_PER_SIDE = 8000;
const PALETTE = ["#005BAC", "#008E7A", "#E6812D", "#7C5CC4", "#C54E5B", "#617385"];

function fail(message) { throw new Error(message); }
function xml(value) { return String(value).replace(/[&<>\"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&apos;" })[c]); }
function paint(value, index) { return typeof value === "string" && /^#[0-9A-Fa-f]{6}$/.test(value) ? value : PALETTE[index % PALETTE.length]; }
function finite(value, label) { const parsed = Number(value); if (!Number.isFinite(parsed)) fail(`${label} must be a finite number`); return parsed; }

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!name?.startsWith("--") || value === undefined) fail("options must be --name value pairs");
    if (options[name] !== undefined) fail(`duplicate option: ${name}`);
    options[name] = value;
  }
  const input = options["--input"];
  const svgInput = options["--svg"];
  const output = options["--output"];
  const allowed = new Set(["--input", "--svg", "--output", "--width-in", "--height-in", "--ppi"]);
  for (const name of Object.keys(options)) if (!allowed.has(name)) fail(`unknown option: ${name}`);
  if ((!input && !svgInput) || (input && svgInput)) fail("provide exactly one of --input chart.json or --svg chart.svg");
  if (!output) fail("--output is required");
  const widthIn = finite(options["--width-in"] ?? DEFAULT_WIDTH_IN, "--width-in");
  const heightIn = finite(options["--height-in"] ?? DEFAULT_HEIGHT_IN, "--height-in");
  const ppi = finite(options["--ppi"] ?? DEFAULT_PPI, "--ppi");
  if (widthIn <= 0 || heightIn <= 0) fail("chart dimensions must be positive");
  if (ppi < 200) fail("--ppi must be at least 200 for slide readability");
  const width = Math.round(widthIn * ppi);
  const height = Math.round(heightIn * ppi);
  if (width > MAX_PIXELS_PER_SIDE || height > MAX_PIXELS_PER_SIDE) fail(`output dimensions must not exceed ${MAX_PIXELS_PER_SIDE}px per side`);
  return { input, svgInput, output, widthIn, heightIn, ppi, width, height };
}

function validateChart(chart) {
  if (!chart || !["bar", "line"].includes(chart.kind) || !Array.isArray(chart.categories) || !chart.categories.length || !Array.isArray(chart.series) || !chart.series.length) {
    fail("chart JSON requires kind ('bar' or 'line'), non-empty categories, and non-empty series");
  }
  if (chart.categories.length > 16) fail("built-in charts support at most 16 categories; use --svg for a custom layout");
  if (chart.series.length > 6) fail("built-in charts support at most 6 series; use --svg for a custom layout");
  if (chart.categories.some((item) => typeof item !== "string" || !item.trim())) fail("chart categories must be non-empty strings");
  for (const series of chart.series) {
    if (!series || typeof series.name !== "string" || !series.name.trim() || !Array.isArray(series.values) || series.values.length !== chart.categories.length) {
      fail("each series needs a name and one value per category");
    }
    if (series.values.some((item) => item !== null && (typeof item !== "number" || !Number.isFinite(item)))) fail("chart values must be finite numbers or null for missing data");
  }
  if (!chart.series.some((series) => series.values.some((value) => value !== null))) fail("chart must contain at least one numeric value");
  return chart;
}

function niceStep(span, targetTicks = 6) {
  if (!Number.isFinite(span) || span <= 0) return 1;
  const raw = span / targetTicks;
  const power = 10 ** Math.floor(Math.log10(raw));
  const normalized = raw / power;
  const factor = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10;
  return factor * power;
}

function axis(values, includeZero) {
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  if (includeZero) { minimum = Math.min(0, minimum); maximum = Math.max(0, maximum); }
  if (minimum === maximum) {
    const expansion = minimum === 0 ? 1 : Math.abs(minimum) * 0.2 || 1;
    minimum -= expansion;
    maximum += expansion;
    if (includeZero) minimum = Math.min(0, minimum);
  }
  const step = niceStep(maximum - minimum);
  const lower = Math.floor(minimum / step) * step;
  const upper = Math.ceil(maximum / step) * step;
  const ticks = [];
  for (let value = lower, count = 0; value <= upper + step * 1e-9 && count < 20; value += step, count += 1) ticks.push(Math.abs(value) < step * 1e-10 ? 0 : value);
  return { lower, upper, step, ticks };
}

function formatter(step) {
  const magnitude = Math.abs(step);
  if (magnitude >= 1e7 || magnitude < 1e-4) return (value) => value === 0 ? "0" : value.toExponential(2).replace("e+", "e");
  const decimals = Math.min(8, Math.max(0, Math.ceil(-Math.log10(magnitude)) + (magnitude / 10 ** Math.floor(Math.log10(magnitude)) === 2.5 ? 1 : 0)));
  return (value) => new Intl.NumberFormat("zh-TW", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(Object.is(value, -0) ? 0 : value);
}

function resolveFontFamily() {
  const requested = process.env.PPTX_CJK_FONT?.trim();
  const query = requested || "Noto Sans CJK TC, sans-serif";
  let family;
  try { family = execFileSync("fc-match", ["-f", "%{family}", query], { encoding: "utf8" }).split(",")[0].trim(); }
  catch (error) { fail(`cannot resolve chart font '${query}' with fontconfig: ${error.message}`); }
  if (!family) fail(`cannot resolve chart font '${query}'`);
  if (requested) {
    const wanted = requested.toLowerCase().replace(/[^a-z0-9\u3400-\u9fff]/g, "");
    const found = family.toLowerCase().replace(/[^a-z0-9\u3400-\u9fff]/g, "");
    if (!found.includes(wanted) && !wanted.includes(found)) fail(`configured chart font '${requested}' is unavailable (fontconfig selected '${family}')`);
  }
  return family;
}

function createTextMeasurer(fontFamily, ppi) {
  const cache = new Map();
  return async (value, sizePt, weight = 400) => {
    const text = String(value || " ");
    const key = `${text}\0${sizePt}\0${weight}`;
    if (cache.has(key)) return cache.get(key);
    let measured;
    try {
      const metadata = await sharp({ text: { text, font: `${fontFamily} ${weight >= 600 ? "Bold " : ""}${sizePt}`, dpi: ppi, rgba: true } }).metadata();
      measured = { width: metadata.width || 0, height: metadata.height || sizePt * ppi / 72 };
    } catch (error) { fail(`cannot measure chart text with font '${fontFamily}': ${error.message}`); }
    cache.set(key, measured);
    return measured;
  };
}

function segments(value) {
  const text = String(value).trim();
  if (/\s/u.test(text)) return text.split(/(\s+)/u).filter(Boolean);
  if (global.Intl?.Segmenter) return [...new Intl.Segmenter("zh-TW", { granularity: "grapheme" }).segment(text)].map((item) => item.segment);
  return Array.from(text);
}

async function wrapMeasured(value, maxWidth, maxLines, label, measure, sizePt, weight = 400) {
  const tokens = segments(value);
  const lines = [];
  let line = "";
  for (const token of tokens) {
    const candidate = line + token;
    if (!line || (await measure(candidate.trimEnd(), sizePt, weight)).width <= maxWidth) line = candidate;
    else { lines.push(line.trim()); line = token.trimStart(); }
  }
  if (line || !lines.length) lines.push(line.trim());
  if (lines.some((item) => !item || item === String(value).trim() && tokens.length > 1 && (maxWidth <= 0))) fail(`${label} cannot fit the built-in layout`);
  if (lines.length > maxLines || await measure(lines.reduce((a, b) => a.length > b.length ? a : b, ""), sizePt, weight).then((item) => item.width) > maxWidth) {
    fail(`${label} is too wide for the built-in layout; shorten it, enlarge the chart, or use --svg`);
  }
  return lines;
}

function tspans(lines, x, y, lineHeight, anchor = "middle") {
  return `<text x="${x}" y="${y}" class="axis" text-anchor="${anchor}">${lines.map((line, index) => `<tspan x="${x}" dy="${index ? lineHeight : 0}">${xml(line)}</tspan>`).join("")}</text>`;
}

async function layoutBuiltIn(chart, width, height, ppi = DEFAULT_PPI) {
  validateChart(chart);
  const fontFamily = resolveFontFamily();
  const measure = createTextMeasurer(fontFamily, ppi);
  const px = (points) => points * ppi / 72;
  const pad = px(14);
  const titleSize = 14;
  const labelSize = 10;
  const titleLines = chart.title ? await wrapMeasured(chart.title, width - 2 * pad, 2, "title", measure, titleSize, 700) : [];
  const legendColumns = Math.min(3, chart.series.length);
  const legendRows = Math.ceil(chart.series.length / legendColumns);
  const legendCellWidth = (width - 2 * pad) / legendColumns;
  const legendLines = await Promise.all(chart.series.map((series) => wrapMeasured(series.name, legendCellWidth - px(18), 2, `series '${series.name}'`, measure, labelSize)));
  const categoryCellWidth = (width - 2 * pad) / chart.categories.length;
  const categoryLines = await Promise.all(chart.categories.map((label) => wrapMeasured(label, categoryCellWidth * 0.9, 3, `category '${label}'`, measure, labelSize)));
  const values = chart.series.flatMap((series) => series.values).filter((value) => value !== null);
  const domain = axis(values, chart.kind === "bar");
  const format = formatter(domain.step);
  const labels = domain.ticks.map(format);
  if (new Set(labels).size !== labels.length) fail("axis labels are not distinct; use --svg for a custom numeric format");
  const tickWidths = await Promise.all(labels.map((label) => measure(label, labelSize)));
  const unitText = chart.unit ? `單位：${chart.unit}` : "";
  const unitWidth = unitText ? (await measure(unitText, labelSize)).width : 0;
  if (unitWidth > width - 2 * pad) fail("unit label is too wide for the built-in layout; shorten it or use --svg");
  const titleLineHeight = px(17);
  const labelLineHeight = px(12);
  const top = pad + titleLines.length * titleLineHeight + (unitText ? labelLineHeight + px(5) : px(5));
  const categoryHeight = Math.max(...categoryLines.map((lines) => lines.length)) * labelLineHeight;
  const legendHeight = legendRows * (Math.max(...legendLines.map((lines) => lines.length)) * labelLineHeight + px(5));
  const bottom = categoryHeight + legendHeight + px(26);
  const left = pad + Math.max(...tickWidths.map((item) => item.width)) + px(8);
  const right = pad;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  if (plotWidth < width * 0.5 || plotHeight < height * 0.4) fail("measured labels leave too little plot area (requires at least 50% width and 40% height); shorten labels, enlarge the chart, or use --svg");
  return { bottom, categoryLines, domain, fontFamily, format, labelLineHeight, labelSize, left, legendColumns, legendLines, legendRows, pad, plotHeight, plotWidth, right, titleLineHeight, titleLines, titleSize, top, unitText };
}

async function builtInSvg(chart, width, height, ppi = DEFAULT_PPI) {
  const layout = await layoutBuiltIn(chart, width, height, ppi);
  const { bottom, categoryLines, domain, fontFamily, format, labelLineHeight, labelSize, left, legendColumns, legendLines, pad, plotHeight, plotWidth, right, titleLineHeight, titleLines, titleSize, top, unitText } = layout;
  const px = (points) => points * ppi / 72;
  const y = (value) => top + ((domain.upper - value) / (domain.upper - domain.lower)) * plotHeight;
  const x = (index) => left + ((index + 0.5) / chart.categories.length) * plotWidth;
  const grid = domain.ticks.map((value) => `<g data-tick-value="${value}"><line x1="${left}" y1="${y(value)}" x2="${width - right}" y2="${y(value)}" class="grid"/><text x="${left - px(5)}" y="${y(value) + px(3)}" class="axis" text-anchor="end">${xml(format(value))}</text></g>`).join("");
  const categories = categoryLines.map((lines, index) => tspans(lines, x(index), top + plotHeight + labelLineHeight, labelLineHeight)).join("");
  let marks = "";
  if (chart.kind === "bar") {
    const groupWidth = (plotWidth / chart.categories.length) * 0.72;
    const barWidth = groupWidth / chart.series.length;
    marks = chart.series.flatMap((series, seriesIndex) => series.values.map((value, index) => {
      if (value === null) return "";
      const baseline = y(0);
      const barY = Math.min(y(value), baseline);
      return `<rect data-value="${value}" x="${x(index) - groupWidth / 2 + seriesIndex * barWidth}" y="${barY}" width="${Math.max(barWidth - px(2), 2)}" height="${Math.abs(y(value) - baseline)}" rx="${px(1)}" fill="${paint(series.color, seriesIndex)}"/>`;
    })).join("");
  } else {
    marks = chart.series.map((series, seriesIndex) => {
      let active = false;
      let pathData = "";
      let dots = "";
      series.values.forEach((value, index) => {
        if (value === null) { active = false; return; }
        pathData += `${active ? "L" : "M"}${x(index)},${y(value)} `;
        dots += `<circle data-value="${value}" cx="${x(index)}" cy="${y(value)}" r="${px(2.2)}" fill="${paint(series.color, seriesIndex)}"/>`;
        active = true;
      });
      return `<path d="${pathData}" class="line" stroke="${paint(series.color, seriesIndex)}"/>${dots}`;
    }).join("");
  }
  const legendWidth = plotWidth / legendColumns;
  const categoryRowCount = Math.max(...categoryLines.map((item) => item.length));
  const legendLineCount = Math.max(...legendLines.map((item) => item.length));
  const legend = legendLines.map((lines, index) => {
    const column = index % legendColumns;
    const row = Math.floor(index / legendColumns);
    const legendX = left + column * legendWidth;
    const legendY = height - bottom + categoryRowCount * labelLineHeight + px(20) + row * (legendLineCount * labelLineHeight + px(5));
    return `<rect x="${legendX}" y="${legendY - px(7)}" width="${px(7)}" height="${px(7)}" rx="${px(1)}" fill="${paint(chart.series[index].color, index)}"/>${tspans(lines, legendX + px(10), legendY, labelLineHeight, "start")}`;
  }).join("");
  const title = titleLines.length ? `<text x="${pad}" y="${pad + px(12)}" class="title">${titleLines.map((line, index) => `<tspan x="${pad}" dy="${index ? titleLineHeight : 0}">${xml(line)}</tspan>`).join("")}</text>` : "";
  const unit = unitText ? `<text x="${left}" y="${top - px(5)}" class="unit">${xml(unitText)}</text>` : "";
  const zero = domain.lower <= 0 && domain.upper >= 0 ? `<line x1="${left}" y1="${y(0)}" x2="${width - right}" y2="${y(0)}" class="zero"/>` : "";
  return `<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><style>.title{font:700 ${px(titleSize)}px '${xml(fontFamily)}',sans-serif;fill:#16324F}.unit,.axis{font:${px(labelSize)}px '${xml(fontFamily)}',sans-serif;fill:#48627A}.grid{stroke:#D9E3EA;stroke-width:${px(.7)}}.zero{stroke:#8FA3B5;stroke-width:${px(1)}}.line{fill:none;stroke-width:${px(2.5)};stroke-linecap:round;stroke-linejoin:round}</style><rect width="100%" height="100%" fill="#FFFFFF"/>${title}${unit}${grid}${zero}${categories}${marks}${legend}</svg>`;
}

function validateCustomSvg(source, width, height) {
  if (!/^\s*(?:<\?xml[^>]*>\s*)?<svg\b/i.test(source)) fail("custom input must be an SVG document");
  if (/<!DOCTYPE|<!ENTITY|<(?:image|script|foreignObject)\b|(?:href|xlink:href)\s*=|@import|url\s*\(/i.test(source)) fail("custom SVG must be self-contained and cannot use scripts, external content, entities, or url() references");
  const match = source.match(/\bviewBox\s*=\s*["']\s*[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?\s+[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s+([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)["']/i);
  if (!match) fail("custom SVG requires a numeric viewBox");
  const viewWidth = Number(match[1]);
  const viewHeight = Number(match[2]);
  if (!(viewWidth > 0 && viewHeight > 0)) fail("custom SVG viewBox dimensions must be positive");
  if (Math.abs(viewWidth / viewHeight - width / height) / (width / height) > 0.01) fail("custom SVG viewBox aspect ratio does not match the requested output dimensions");
  return source;
}

async function render(options) {
  const source = options.input
    ? await builtInSvg(validateChart(JSON.parse(fs.readFileSync(options.input, "utf8"))), options.width, options.height, options.ppi)
    : validateCustomSvg(fs.readFileSync(options.svgInput, "utf8"), options.width, options.height);
  const destination = path.resolve(options.output);
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  const temporary = path.join(path.dirname(destination), `.${path.basename(destination)}.${process.pid}.tmp`);
  try {
    await sharp(Buffer.from(source)).resize(options.width, options.height, { fit: "fill" }).png().toFile(temporary);
    fs.renameSync(temporary, destination);
  } finally {
    try { fs.unlinkSync(temporary); } catch (error) { if (error.code !== "ENOENT") throw error; }
  }
  return destination;
}

async function main(argv = process.argv.slice(2)) { return render(parseArgs(argv)); }

if (require.main === module) main().catch((error) => { console.error(`chart render failed: ${error.message}`); process.exitCode = 1; });

module.exports = { axis, builtInSvg, createTextMeasurer, formatter, layoutBuiltIn, main, niceStep, parseArgs, render, resolveFontFamily, validateChart, validateCustomSvg, wrapMeasured };
