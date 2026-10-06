import { CircleAlert, Info } from "lucide-react";
import { Alert, AlertAction, AlertDescription, AlertTitle, Button } from "nhi-ai-ui";

export const Default = () => (
  <Alert className="max-w-xl">
    <Info />
    <AlertTitle>文件建立索引中</AlertTitle>
    <AlertDescription>加入知識庫後需數分鐘建立索引，完成前搜尋不到這份文件。</AlertDescription>
  </Alert>
);

export const Destructive = () => (
  <Alert variant="destructive" className="max-w-xl">
    <CircleAlert />
    <AlertTitle>上傳失敗</AlertTitle>
    <AlertDescription>檔案超過大小上限（20 MB），請壓縮後再試一次。</AlertDescription>
  </Alert>
);

export const WithAction = () => (
  <Alert className="max-w-xl">
    <Info />
    <AlertTitle>有 3 份文件尚未完成處理</AlertTitle>
    <AlertDescription>處理完成後會自動出現在搜尋結果中。</AlertDescription>
    <AlertAction><Button size="xs" variant="outline">查看</Button></AlertAction>
  </Alert>
);
