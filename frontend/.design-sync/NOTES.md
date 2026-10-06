# design-sync notes (健保署 AI → claude.ai/design)

Project: 「健保署 AI Design System」, https://claude.ai/design/p/8ff266f8-e575-4269-8e31-68f6a3b604a7

## How this repo is shaped for the sync

- The frontend is a Next.js app, not a published package. `.design-sync/pkg/` is a wrapper
  package (`nhi-ai-ui`): `index.ts` re-exports `components/ui/*` plus the presentational chat
  pieces. Components that need Next.js routing (app shell, sidebar sessions) are left out.
- `node .design-sync/build.mjs` (`cfg.buildCmd`) builds `pkg/dist/`:
  - an esbuild ESM bundle with npm packages external;
  - a tsc `.d.ts` tree, where `@/` specifiers are rewritten to relative paths because the type
    extractor ignores tsconfig paths;
  - the Tailwind CSS from `app/globals.css`.
  Run it from `frontend/` after `.ds-sync/` is staged, since it borrows esbuild from there.
- Commands (from `frontend/`, node via nvm):
  ```sh
  node .design-sync/build.mjs
  node .ds-sync/package-build.mjs --config .design-sync/config.json --node-modules ./node_modules --entry .design-sync/pkg/dist/index.js --out ./ds-bundle
  node .ds-sync/package-validate.mjs ./ds-bundle
  ```

## Gotchas fixed during the first sync (2026-10-06)

- **Bundle size:** `@streamdown/code|math|mermaid` pull in shiki, KaTeX and Mermaid, which made
  the bundle 19.2 MB, over the 12 MB upload cap. `build.mjs` resolves them to
  `stubs/streamdown-plugins.js` (undefined plugins). Chat markdown still renders; code
  highlighting, maths and diagrams do not in Claude Design. The bundle is about 3.4 MB.
- **Tailwind only emits classes found in scanned sources** and skips dot-directories.
  `build.mjs` therefore adds `@source "../.design-sync/previews"` and a `SAFELIST` of
  `@source inline(...)` patterns (layout, spacing, sizing, type, token colours, responsive).
  If designs need a utility that does nothing, add its pattern there. The conventions header
  tells the design agent which vocabulary exists.
- **Icons:** `cfg.extraEntries: ["lucide-react"]` puts every lucide icon on `window.NhiAiUi`.
  `Badge` and `Table` collide with our components (`[EXPORT_COLLISION]`, ours win), so the
  conventions say to use the `…Icon` names.
- **Headless Chromium on this WSL install** needed `sudo apt-get install -y libasound2t64` and
  a CJK font. Noto Sans TC was installed to `~/.local/share/fonts` without sudo. Without that
  font, every Chinese glyph renders as tofu in screenshots, while the real browser falls back to
  the system font. Playwright 1.63.0 matches the cached `chromium-1243`
  (`npm i playwright@1.63.0` in `.ds-sync/`).
- Groups: the UI kit lands in `general`, chat pieces in `chat`. We deliberately skipped docs
  files for regrouping, because a matched doc drops the generated Examples section from
  `.prompt.md`.
- Sub-parts (`DialogTitle`, `TableRow`, …) are separate exports and ship as floor cards. Their
  usage is shown in the parent component's preview.

## Known render warns

- `[RENDER_THIN]` on `Dialog`, `AlertDialog`, `ConfirmDialog`: these render in a portal, so the
  root measures 0px. The captures show the dialogs correctly. Benign.

## Re-sync risks

- `stubs/streamdown-plugins.js` must keep exporting `code`, `math` and `mermaid`. If
  `components/ai-elements/message.tsx` adds another heavy plugin, the bundle can blow past
  12 MB again.
- The previews in `.design-sync/previews/` hard-code data shapes (`ChatTurn`, `ChatAttachment`,
  `ChatModelOption`). If those types change in `lib/`, the chat previews may break silently.
  Re-check the chat cards.
- The safelist is a guess at what designs need. The design agent can still write classes
  outside it, and those render unstyled.
- The tooling assumes node 24 (nvm), Tailwind 4.3 and Base UI 1.7.
