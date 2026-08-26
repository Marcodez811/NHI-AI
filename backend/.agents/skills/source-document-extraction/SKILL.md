---
name: source-document-extraction
description: Extract DOCX and PDF source documents into deterministic, structured JSON for source-to-PPTX workflows. Use when Codex needs to inspect document content, hierarchy, tables, figures, links, notes, locations, or embedded assets before creating slides; this skill reads only and does not create or edit source documents.
---

# Source document extraction

Run `scripts/extract_sources.py` with one or more DOCX/PDF paths (directories are traversed) and an explicit output directory. Treat its `manifest.json` as the run result; a nonzero exit means at least one source has `status: error`.

```bash
python3 .agents/skills/source-document-extraction/scripts/extract_sources.py source.docx source-folder --output-dir extracted
python3 .agents/skills/source-document-extraction/scripts/validate_sources.py extracted
```

Use the document index and chunk artifacts for slide planning. Preserve each block's `id`, `kind`, and `locator` in downstream provenance. Read [references/artifact-format.md](references/artifact-format.md) for fields, failure behavior, and optional PDF dependencies. The machine-readable contract is [references/source-extraction.schema.json](references/source-extraction.schema.json).
