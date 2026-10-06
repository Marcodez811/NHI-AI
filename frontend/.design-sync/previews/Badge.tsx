import { Badge } from "nhi-ai-ui";

export const Variants = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Badge>已完成</Badge>
    <Badge variant="secondary">知識庫</Badge>
    <Badge variant="outline">PDF</Badge>
    <Badge variant="destructive">處理失敗</Badge>
    <Badge variant="ghost">草稿</Badge>
  </div>
);

export const InContext = () => (
  <div className="flex max-w-md items-center justify-between rounded-lg border border-border px-3 py-2 text-sm">
    <span className="truncate">114年第1季醫院總額執行報告.pdf</span>
    <Badge variant="secondary">已加入知識庫</Badge>
  </div>
);
