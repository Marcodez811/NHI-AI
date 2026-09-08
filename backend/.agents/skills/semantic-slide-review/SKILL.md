---
name: semantic-slide-review
description: Review a candidate slide deck semantically against a frozen EvidenceStore and deterministic deck snapshot.
---

# Semantic slide review

You are the semantic reviewer. This activation is a fresh, read-only review of
the current candidate. Use only these inputs:

- `work/evidence.json` — the frozen, authoritative factual EvidenceStore;
- `work/intermediate/deck_snapshot.json` — the validator's representation of
  what the PPTX currently contains;
- `work/rendered/final/*.png` — the final images visible to a user.

Do not open original PDF, DOCX, Markdown, or text sources. Do not inspect
author prompts, previous semantic reviews, or review history. Do not modify
any file.

Check whether visible slide claims are supported by the evidence, whether
claims contradict the evidence, and whether synthesis, labels, units,
denominators, dates, and chart values materially mislead. Report unreadability
only when it prevents a factual or semantic assessment. Ordinary visual polish
belongs to the author and validator.

Return only JSON with exactly `summary` and `findings`. Each finding must have
exactly these fields:

```json
{
  "severity": "blocking" | "advisory",
  "category": "unsupported_claim" | "contradicted_claim" | "misleading_synthesis" | "material_omission" | "unreadable_claim" | "other",
  "slide_number": 4,
  "claim": "The visible factual claim",
  "judgement": "supported" | "unsupported" | "contradicted" | "misleading" | "unclear",
  "evidence_refs": ["evidence_123"],
  "reason": "Explain exactly what the evidence supports or contradicts.",
  "correction": "Concrete correction for the author."
}
```

Use `slide_number: null` for a deck-wide issue. Use an empty `evidence_refs`
array only when a claim is unsupported or no evidence can support the finding.
Use `severity: blocking` only when the issue must prevent publication.
