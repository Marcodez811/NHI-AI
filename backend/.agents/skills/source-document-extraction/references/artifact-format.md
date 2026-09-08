# Artifact format

`manifest.json` has `format_version`, `documents`, and `status`. Every document entry points to its per-document `index.json`; blocks are split into ordered chunk files of fewer than 40,000 characters. A single table is never split, so an oversized table is emitted whole with a warning. The stable document id is a SHA-256 of source bytes. Each block id is a SHA-256 of document id, kind, locator, and normalized content.

Block kinds are `heading`, `paragraph`, `list_item`, `table`, `figure`, and `footnote`. A DOCX locator identifies part and OOXML-style location. A PDF locator identifies page and bounding box when the active backend supplies one. `assets` contains copied DOCX media and any available extracted visual artifact. Each copied asset has an `id` in `sha256:<hex>` form and a relative `path`; DOCX assets also preserve the relationship IDs used by figure blocks so the consolidated EvidenceStore can retain block-to-asset references.

The extractor accepts `.docx`, `.pdf`, `.md`, `.markdown`, `.txt`, and `.text`. Markdown headings, list items, and tables receive their structural block kinds; plain text is emitted as ordered paragraphs. Text is decoded as UTF-8 with an optional BOM, and a decoding failure is an extraction error. Empty text is retained as a warning-bearing document so the backend can decide whether the source set is usable.

The extractor accepts tracked changes by emitting inserted/current text and excluding deleted text. It emits warnings for recoverable omissions. It reports `status: error` for unsupported sources, unsafe/corrupt/encrypted inputs, missing PDF parsing dependencies, or unavailable OCR required for a low/no-text PDF page. It exits nonzero if any source errors, while retaining successful artifacts and warning-bearing partial results.

PDF extraction uses `pdfplumber` when installed; `pypdf` provides a text-only fallback. Low/no-text pages require both `pypdfium2` and `pytesseract`; OCR is run at 300 DPI with `chi_tra+eng` and warns below 60 mean confidence. Optional packages: `pdfplumber`, `pypdf`, `pypdfium2`, `pytesseract` (plus the system Tesseract language data).
