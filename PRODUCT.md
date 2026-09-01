# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Policy analysts working with National Health Insurance policy material.

## Product Purpose

健保署 AI is an internal, document-centered workspace for asking grounded questions, managing source documents, and generating policy presentations from selected evidence.

## Positioning

The workspace connects source-document selection, evidence-grounded chat, and presentation generation in one workflow, with backend jobs and citations preserving the relationship between answers and source material.

## Operating Context

Analysts upload and organize policy documents, wait for background indexing, select eligible sources, ask scoped questions, and create validated slide artifacts. The application includes a trusted local developer telemetry view for sanitized agent-run status.

## Capabilities and Constraints

- Chat supports scoped retrieval, streaming responses, citations, and insufficient-evidence states.
- The knowledge base supports folders, document metadata, indexing status, upload, download, update, and deletion.
- Slide generation is an asynchronous workflow with readiness checks, phase history, retry, failure, and download states.
- Production UI copy is Traditional Chinese; technical identifiers may remain English.
- Existing backend endpoints, payloads, job semantics, and environment contracts are preserved.

## Brand Commitments

Retain the 健保署 AI name and the existing blue, light/dark CSS-variable theme while standardizing implementation on shadcn/base-nova components.

## Evidence on Hand

- Existing workspace implementation and UI critique at `.impeccable/critique/2026-08-26T08-26-35Z__frontend-app-page-tsx.md`.
- Real document, chat, slide-job, and sanitized agent telemetry contracts in `frontend/lib/api.ts` and the backend API.

## Product Principles

- Source evidence should remain visible and actionable.
- Workflow readiness and failure recovery should be explicit.
- Analysts should be able to move between research and presentation work without losing their working set.
- Dense operational information should remain scannable and keyboard accessible.

## Accessibility & Inclusion

Support keyboard navigation, visible focus, semantic labels, focus-managed dialogs, reduced-motion behavior, responsive layouts, and readable contrast in both themes.
