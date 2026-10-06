import { Textarea } from "nhi-ai-ui";

export const States = () => (
  <div className="flex max-w-lg flex-col gap-3">
    <Textarea placeholder="問問健保署 AI…" />
    <Textarea defaultValue={"請整理 114 年第 1 季各分區的平均點值，\n並和去年同期比較。"} />
    <Textarea aria-invalid placeholder="內容不可為空白" />
    <Textarea disabled placeholder="回覆產生中…" />
  </div>
);
