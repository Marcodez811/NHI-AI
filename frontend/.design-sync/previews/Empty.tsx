import { FolderOpen, Upload } from "lucide-react";
import { Button, Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "nhi-ai-ui";

export const NoFiles = () => (
  <Empty className="max-w-lg border border-dashed">
    <EmptyHeader>
      <EmptyMedia variant="icon"><FolderOpen /></EmptyMedia>
      <EmptyTitle>還沒有上傳的檔案</EmptyTitle>
      <EmptyDescription>在對話中上傳的檔案會出現在這裡，可以重複用在其他對話。</EmptyDescription>
    </EmptyHeader>
    <EmptyContent>
      <Button size="sm"><Upload data-icon="inline-start" />上傳檔案</Button>
    </EmptyContent>
  </Empty>
);

export const TextOnly = () => (
  <Empty className="max-w-lg">
    <EmptyHeader>
      <EmptyTitle>沒有符合的文件</EmptyTitle>
      <EmptyDescription>試試其他關鍵字，或清除分類篩選。</EmptyDescription>
    </EmptyHeader>
  </Empty>
);
