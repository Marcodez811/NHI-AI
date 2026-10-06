import { Separator } from "nhi-ai-ui";

export const Horizontal = () => (
  <div className="max-w-sm text-sm">
    <div className="font-medium">健保署 AI 助理</div>
    <div className="text-muted-foreground">搜尋知識庫並附上引用來源</div>
    <Separator className="my-3" />
    <div className="text-muted-foreground">最近對話</div>
  </div>
);

export const Vertical = () => (
  <div className="flex h-5 items-center gap-3 text-sm">
    <span>上傳的檔案</span><Separator orientation="vertical" /><span>產出的文件</span><Separator orientation="vertical" /><span>知識庫</span>
  </div>
);
