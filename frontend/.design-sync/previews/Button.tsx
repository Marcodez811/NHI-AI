import { ArrowUp, Download, Plus, Trash2 } from "lucide-react";
import { Button } from "nhi-ai-ui";

export const Variants = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button>建立報告</Button>
    <Button variant="outline">取消</Button>
    <Button variant="secondary">儲存草稿</Button>
    <Button variant="ghost">略過</Button>
    <Button variant="destructive">刪除</Button>
    <Button variant="link">查看資料來源</Button>
  </div>
);

export const Sizes = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button size="xs" variant="outline">極小</Button>
    <Button size="sm" variant="outline">小</Button>
    <Button variant="outline">預設</Button>
    <Button size="lg" variant="outline">大</Button>
  </div>
);

export const WithIcons = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button variant="outline"><Plus data-icon="inline-start" />新對話</Button>
    <Button variant="outline"><Download data-icon="inline-start" />下載</Button>
    <Button variant="destructive"><Trash2 data-icon="inline-start" />刪除檔案</Button>
  </div>
);

export const IconOnly = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button size="icon" aria-label="送出"><ArrowUp /></Button>
    <Button size="icon-sm" variant="ghost" aria-label="新增"><Plus /></Button>
    <Button size="icon" disabled aria-label="送出"><ArrowUp /></Button>
  </div>
);

export const Disabled = () => (
  <div className="flex flex-wrap items-center gap-2">
    <Button disabled>建立報告</Button>
    <Button variant="outline" disabled>取消</Button>
  </div>
);
