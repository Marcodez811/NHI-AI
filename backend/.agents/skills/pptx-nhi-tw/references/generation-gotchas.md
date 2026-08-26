# Generation gotchas

- Set PPTXGenJS layout before adding slides. Use six-digit uppercase hex without `#`; use `transparency` for fills/images and `opacity` for shadows.
- Use one fresh PPTXGenJS instance and fresh options/shadow objects per output. Set text `margin: 0` when alignment is exact; do not add CJK character spacing by default.
- Keep charts editable with `addChart()`. Supply titles, zh-TW labels, data labels, and deliberate colors. For stacked charts use `ctr`, `inEnd`, or `inBase`; combo secondary axes require both category and value-axis definitions.
- Never pass ICNS, JXL, HEIF, or HEIC files directly to PPTXGenJS. Its transitive `image-size` parser has known denial-of-service advisories for those formats; convert them to PNG with the pinned `sharp` release before calling `addImage()`.
- Favor restrained government-briefing design: source-informed palette, generous whitespace, readable contrast, and a consistent motif. Avoid marketing language, decorative stripes, dense text-only slides, and italic CJK emphasis.
- Generate to `output/presentation.pptx`, then run the evidence and QA workflow before release.
