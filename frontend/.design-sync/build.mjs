// Builds the wrapper package in .design-sync/pkg for the claude.ai/design sync.
// Run from frontend/: `node .design-sync/build.mjs` (needs .ds-sync/node_modules
// for esbuild; see .design-sync/NOTES.md).
//   1. dist/index.js   - ESM bundle of pkg/index.ts; npm packages stay external
//                        so the converter resolves them from frontend/node_modules.
//   2. dist/types/     - .d.ts tree from tsc, used for the props contracts.
//   3. dist/styles.css - app/globals.css compiled by Tailwind, plus the authored
//                        previews and a safelisted utility vocabulary.
import { execFileSync } from "node:child_process";
import { mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const pkg = join(here, "pkg");
const dist = join(pkg, "dist");
const requireFromRoot = createRequire(join(root, "package.json"));
const esbuild = createRequire(join(root, ".ds-sync", "package.json"))("esbuild");

rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });

// 1. JS bundle. `@/` imports are resolved through the app's tsconfig paths; every
// other bare import (react, @base-ui/react, lucide-react, ...) stays external.
await esbuild.build({
  entryPoints: [join(pkg, "index.ts")],
  outfile: join(dist, "index.js"),
  bundle: true,
  format: "esm",
  jsx: "automatic",
  target: "es2020",
  tsconfig: join(root, "tsconfig.json"),
  logLevel: "warning",
  plugins: [{
    name: "externalize-packages",
    setup(build) {
      // Heavy streamdown plugins -> local stub (see stubs/streamdown-plugins.js).
      build.onResolve({ filter: /^@streamdown\/(code|math|mermaid)$/ }, () =>
        ({ path: join(here, "stubs", "streamdown-plugins.js") }));
      build.onResolve({ filter: /^[^./@]|^@[^/]+\// }, (args) =>
        args.path.startsWith("@/") ? undefined : { path: args.path, external: true });
    },
  }],
});

// 2. Declarations. A temporary tsconfig keeps the app's settings and paths but
// emits declarations for the barrel's import graph only.
const tsconfig = join(pkg, "tsconfig.build.json");
writeFileSync(tsconfig, JSON.stringify({
  extends: "../../tsconfig.json",
  compilerOptions: {
    noEmit: false,
    declaration: true,
    emitDeclarationOnly: true,
    incremental: false,
    rootDir: "../..",
    outDir: "dist/types",
    baseUrl: "../..",
  },
  include: ["index.ts", "../../next-env.d.ts"],
}, null, 2));
try {
  execFileSync(process.execPath, [requireFromRoot.resolve("typescript/bin/tsc"), "-p", tsconfig], { stdio: "inherit" });
} finally {
  rmSync(tsconfig, { force: true });
}
// tsc keeps `@/x` specifiers verbatim; rewrite them to relative paths so the
// type extractor (which knows nothing about tsconfig paths) can follow them.
const typesRoot = join(dist, "types");
for (const file of readdirSync(typesRoot, { recursive: true })) {
  if (!file.endsWith(".d.ts")) continue;
  const path = join(typesRoot, file);
  const text = readFileSync(path, "utf8");
  const fixed = text.replace(/(from\s+|import\()(["'])@\/([^"']+)\2/g, (_, lead, quote, target) => {
    let rel = relative(dirname(path), join(typesRoot, target)).split("\\").join("/");
    if (!rel.startsWith(".")) rel = `./${rel}`;
    return `${lead}${quote}${rel}${quote}`;
  });
  if (fixed !== text) writeFileSync(path, fixed);
}

// 3. CSS. Tailwind v4 only emits utilities it finds in the scanned sources, and
// it skips dot-directories, so on its own the output would carry just the classes
// the app already uses. Designs built in claude.ai/design write their own layout
// glue, so the sync CSS also scans the authored previews and safelists a common
// utility vocabulary (`@source inline` expands the brace patterns).
const SAFELIST = [
  "{flex,inline-flex,grid,block,inline-block,hidden,contents}",
  "{flex-row,flex-col,flex-wrap,flex-1,flex-none,shrink-0,grow}",
  "{items,self}-{start,center,end,stretch,baseline}",
  "justify-{start,center,end,between,around}",
  "grid-cols-{1,2,3,4,5,6,12}", "col-span-{1,2,3,4,5,6,full}",
  "{p,px,py,pt,pb,pl,pr,m,mx,my,mt,mb,ml,mr,gap,gap-x,gap-y,space-x,space-y}-{0,0.5,1,1.5,2,2.5,3,4,5,6,8,10,12,16,20,24}",
  "{mx,ml,mr,my}-auto",
  "{w,h}-{full,auto,fit,screen,px,4,5,6,8,10,12,16,20,24,32,40,48,56,64,72,80,96}",
  "w-{1/2,1/3,2/3,1/4,3/4,1/5,2/5,3/5,4/5}", "min-h-{0,full,screen}", "min-w-0",
  "max-w-{xs,sm,md,lg,xl,2xl,3xl,4xl,5xl,6xl,7xl,full,prose,none}", "max-h-{64,80,96,full,screen}",
  "size-{3,3.5,4,5,6,8,9,10,12,16}",
  "text-{xs,sm,base,lg,xl,2xl,3xl,4xl,5xl}", "font-{normal,medium,semibold,bold}",
  "text-{left,center,right}", "leading-{none,tight,snug,normal,relaxed,loose}", "tracking-{tight,normal,wide}",
  "{truncate,whitespace-nowrap,whitespace-pre-wrap,break-words,break-all,tabular-nums,uppercase,underline}",
  "line-clamp-{1,2,3,4}",
  "{bg,text,border,ring,fill,stroke}-{background,foreground,card,card-foreground,popover,popover-foreground,primary,primary-foreground,secondary,secondary-foreground,muted,muted-foreground,accent,accent-foreground,destructive,border,input,ring,sidebar,sidebar-foreground,sidebar-accent,sidebar-border}",
  "{bg,text,border}-{primary,muted,accent,destructive,foreground}/{5,10,20,30,50,60,80}",
  "{border,border-t,border-b,border-l,border-r,border-x,border-y,border-0,border-2,border-dashed}",
  "rounded-{none,sm,md,lg,xl,2xl,3xl,full}", "shadow-{none,xs,sm,md,lg}", "ring-{0,1,2}",
  "{relative,absolute,fixed,sticky}", "{inset,top,right,bottom,left}-0", "z-{0,10,20,30,40,50}",
  "overflow-{hidden,auto,x-auto,y-auto}", "opacity-{50,60,70,80}",
  "{sm,md,lg,xl}:{flex,grid,hidden,block,flex-row,flex-col,items-center,justify-between,w-auto}",
  "{sm,md,lg,xl}:grid-cols-{2,3,4,6}", "{sm,md,lg}:{p,px,py,gap}-{4,6,8,10}",
  "{sm,md,lg}:max-w-{md,lg,xl,2xl,3xl,4xl}", "{sm,md,lg}:text-{sm,base,lg,xl,2xl,3xl,4xl}",
  "hover:{bg-muted,bg-accent,text-foreground,underline}",
];
const postcss = requireFromRoot("postcss");
const tailwind = requireFromRoot("@tailwindcss/postcss");
const input = join(root, "app", "globals.css");
const extraSources = [
  `@source "../.design-sync/previews";`,
  ...SAFELIST.map((pattern) => `@source inline(${JSON.stringify(pattern)});`),
].join("\n");
const css = readFileSync(input, "utf8") + "\n\n/* design-sync additions */\n" + extraSources + "\n";
const result = await postcss([tailwind({ base: root })]).process(css, { from: input, to: join(dist, "styles.css") });
writeFileSync(join(dist, "styles.css"), result.css);

console.log(`built ${dist}`);
