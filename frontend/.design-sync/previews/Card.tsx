import { Download, FileText } from "lucide-react";
import { Badge, Button, Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "nhi-ai-ui";

export const Default = () => (
  <Card className="max-w-md">
    <CardHeader>
      <CardTitle>114年第1季點值分析報告</CardTitle>
      <CardDescription>由 AI 助理於 2026/10/06 產出</CardDescription>
      <CardAction><Badge variant="secondary">報告</Badge></CardAction>
    </CardHeader>
    <CardContent className="text-sm text-muted-foreground">
      全國平均點值 0.9594 元/點，各分區皆較去年同期提升，南區、高屏與東區已超過 1 元。
    </CardContent>
    <CardFooter className="gap-2">
      <Button size="sm" variant="outline"><FileText data-icon="inline-start" />預覽</Button>
      <Button size="sm"><Download data-icon="inline-start" />下載 Word</Button>
    </CardFooter>
  </Card>
);

export const Small = () => (
  <Card size="sm" className="max-w-xs">
    <CardHeader>
      <CardTitle>知識庫文件</CardTitle>
      <CardDescription>可供 AI 助理搜尋的文件數</CardDescription>
    </CardHeader>
    <CardContent className="text-3xl font-semibold tabular-nums">128</CardContent>
  </Card>
);
