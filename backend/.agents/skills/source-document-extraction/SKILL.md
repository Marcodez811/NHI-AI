---
name: source-document-extraction
description: Extract PDF, DOCX, Markdown, and plain-text source documents into deterministic, structured JSON for source-to-PPTX workflows. Use when Codex needs to inspect document content, hierarchy, tables, figures, links, notes, locations, or embedded assets before creating slides; this skill reads source documents and writes only extraction artifacts.
---

# Source document extraction

Run `scripts/extract_sources.py` with one or more PDF, DOCX, Markdown, or plain-text paths (directories are traversed) and an explicit output directory. Treat its `manifest.json` as the run result; a nonzero exit means at least one source has `status: error`.

```bash
python3 .agents/skills/source-document-extraction/scripts/extract_sources.py source.docx source-folder --output-dir extracted
python3 .agents/skills/source-document-extraction/scripts/validate_sources.py extracted
```

Use the document index and chunk artifacts for slide planning. Preserve each block's `id`, `kind`, and `locator` in downstream provenance. The extraction stage must complete before authoring and must write all generated files below `work/extracted/`; it must not curate claims or mutate source content. Read [references/artifact-format.md](references/artifact-format.md) for fields, failure behavior, supported text parsing, and optional PDF dependencies. The machine-readable contract is [references/source-extraction.schema.json](references/source-extraction.schema.json).

After extraction, the backend validates the complete artifact tree and consolidates it into the frozen `work/evidence.json` EvidenceStore. Downstream authoring and review stages consume that file and its copied assets only. They must not reopen the original PDF, DOCX, Markdown, or text files during generation or revision.
