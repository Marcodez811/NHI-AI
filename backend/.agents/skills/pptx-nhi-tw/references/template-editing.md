# Template editing

- Inspect a supplied template first: `python .agents/skills/pptx-nhi-tw/scripts/thumbnail.py template/example.pptx template-thumbs`. Preserve official cover, footer, and CI elements; do not substitute a generic theme when an agency template exists.
- Duplicate slides with `.agents/skills/pptx-nhi-tw/scripts/add_slide.py`; never copy `slideN.xml` manually. Complete additions/deletions/reordering before text edits, then run `.agents/skills/pptx-nhi-tw/scripts/clean.py` after `<p:sldIdLst>` is final.
- For XML editing use a namespace-preserving parser such as `defusedxml.minidom`. Keep one `<a:p>` per list item, retain paragraph properties, preserve whitespace with `xml:space="preserve"`, and do not flatten styled text with `text_frame.text`.
- Validate a template-derived result against its original: `python .agents/skills/pptx-nhi-tw/scripts/office/validate.py output/presentation.pptx --original template/example.pptx`.
