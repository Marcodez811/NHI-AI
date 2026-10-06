# 健保署 AI design system: how to build with it

This is the UI kit of 健保署 AI (National Health Insurance Administration, Taiwan), an internal
assistant for agency staff: chat with a knowledge base, files, reports. **All UI text is Traditional
Chinese** (繁體中文), dates use ROC years (114年第1季), and screens never show internal ids.

## Setup

- Everything is on `window.NhiAiUi`: the components listed in this README, plus every
  [lucide](https://lucide.dev) icon. **Use the `…Icon` names for icons** (`PlusIcon`,
  `FileTextIcon`, `TrashIcon`, `DownloadIcon`), because `Badge` and `Table` are our components,
  not icons.
- No root provider is needed. Wrap tooltips in `TooltipProvider`.
- Light theme by default: white page (`bg-background`), near-white sidebar (`bg-sidebar`), and
  green only as an accent. Dark mode exists: put `className="dark"` on an ancestor.
- Font: `system-ui`. Do not load web fonts.

## Composition idiom (Base UI, not Radix)

- Triggers and closers take a `render` prop instead of `asChild`:
  `<DialogClose render={<Button variant="outline" />}>取消</DialogClose>`,
  `<DropdownMenuTrigger render={<Button variant="ghost" size="icon-sm" />}>…</DropdownMenuTrigger>`.
- Overlays open with `open`/`onOpenChange` (controlled) or `defaultOpen`.
- Icons inside a `Button` get `data-icon="inline-start"` or `data-icon="inline-end"`.
- `Button` variants: `default` (green), `outline`, `secondary`, `ghost`, `destructive` (light
  red), `link`. Sizes: `xs`, `sm`, `default`, `lg`, `icon`, `icon-xs`, `icon-sm`, `icon-lg`.
- Destructive confirmations use `ConfirmDialog` (`open`, `title`, `description`, `onConfirm`,
  `onCancel`) or the `AlertDialog` parts, never `window.confirm`.
- Forms: `Field` > `FieldLabel` + `Input`/`Textarea`/`Select` + `FieldDescription` or
  `FieldError`; group them with `FieldSet`/`FieldGroup`.
- Chat screens: compose `ChatEmptyState` (with `ChatComposer` as its child), `ChatMessageList`
  and `ChatComposer`. See their `.prompt.md` files for the message and attachment shapes.

## Styling: Tailwind utility classes on theme tokens

Style your own layout with Tailwind classes. Colours come from theme tokens only; never use raw hex
values or Tailwind palette colours like `green-600`.

| Purpose | Classes |
| --- | --- |
| Surfaces | `bg-background`, `bg-card`, `bg-muted`, `bg-accent`, `bg-sidebar`, `bg-primary/10` (light green tint) |
| Text | `text-foreground`, `text-muted-foreground`, `text-primary`, `text-destructive` |
| Lines | `border border-border`, `border-b`, `border-dashed`, `ring-1` |
| Layout | `flex`, `grid`, `grid-cols-{1–6}`, `gap-{1–8}`, `items-center`, `justify-between`, `mx-auto`, `max-w-{sm…7xl}` |
| Spacing | `p/px/py/m/mx/my-{0–24}`, `space-y-{1–8}` |
| Type | `text-{xs…4xl}`, `font-{medium,semibold}`, `tracking-tight`, `truncate`, `tabular-nums` |
| Shape | `rounded-{md,lg,xl,2xl,full}`, `shadow-{xs,sm,md}` |
| Responsive | `sm:`/`md:`/`lg:` prefixes on layout, grid, padding and max-width |

Only classes present in `styles.css` take effect, so prefer the vocabulary above. The token values
are the `--background`, `--primary`, `--muted`, `--sidebar` and other variables at the top of
`styles.css`.

## Example

```jsx
const { Button, Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter, Badge, DownloadIcon } = window.NhiAiUi;

<div className="mx-auto grid max-w-4xl gap-4 p-6 md:grid-cols-2">
  <Card>
    <CardHeader>
      <CardTitle>114年第1季點值分析報告</CardTitle>
      <CardDescription>由 AI 助理於 2026/10/06 產出</CardDescription>
    </CardHeader>
    <CardContent className="text-sm text-muted-foreground">全國平均點值 0.9594 元/點，高於去年同期。</CardContent>
    <CardFooter className="justify-between">
      <Badge variant="secondary">報告</Badge>
      <Button size="sm"><DownloadIcon data-icon="inline-start" />下載 Word</Button>
    </CardFooter>
  </Card>
</div>
```
