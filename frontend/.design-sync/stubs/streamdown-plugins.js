// Sync-bundle stand-in for @streamdown/code, @streamdown/math and @streamdown/mermaid.
// Those pull in shiki, KaTeX and Mermaid (~19 MB bundled, over the upload cap); chat
// previews only need markdown text, so the plugins are left undefined here.
// The real app is unaffected: only .design-sync/build.mjs resolves to this file.
export const code = undefined;
export const math = undefined;
export const mermaid = undefined;
