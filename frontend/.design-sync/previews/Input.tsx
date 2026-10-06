import { Input } from "nhi-ai-ui";

export const States = () => (
  <div className="flex max-w-sm flex-col gap-3">
    <Input placeholder="搜尋知識庫文件…" />
    <Input defaultValue="個別醫院前瞻式預算分區共管試辦計畫" />
    <Input aria-invalid defaultValue="無效的分類" />
    <Input disabled placeholder="處理中，暫時無法編輯" />
  </div>
);

export const File = () => (
  <div className="max-w-sm"><Input type="file" /></div>
);
