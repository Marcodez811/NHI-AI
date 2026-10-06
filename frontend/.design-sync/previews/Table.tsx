import { Badge, Table, TableBody, TableCaption, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "nhi-ai-ui";

const rows = [
  ["臺北", "0.9349", "0.8976"], ["北區", "0.9519", "0.9048"], ["中區", "0.9413", "0.8953"],
  ["南區", "1.0018", "0.9505"], ["高屏", "1.0016", "0.9533"], ["東區", "1.0168", "0.9445"],
];

export const PointValues = () => (
  <Table className="max-w-xl">
    <TableCaption>114 年第 1 季各分區平均點值（元/點）</TableCaption>
    <TableHeader>
      <TableRow><TableHead>分區</TableHead><TableHead className="text-right">114Q1</TableHead><TableHead className="text-right">113Q1</TableHead></TableRow>
    </TableHeader>
    <TableBody>
      {rows.map(([area, now, prev]) => (
        <TableRow key={area}><TableCell>{area}</TableCell><TableCell className="text-right tabular-nums">{now}</TableCell><TableCell className="text-right tabular-nums text-muted-foreground">{prev}</TableCell></TableRow>
      ))}
    </TableBody>
    <TableFooter>
      <TableRow><TableCell>全國</TableCell><TableCell className="text-right tabular-nums">0.9594</TableCell><TableCell className="text-right tabular-nums">0.9148</TableCell></TableRow>
    </TableFooter>
  </Table>
);

export const FileList = () => (
  <Table className="max-w-2xl">
    <TableHeader>
      <TableRow><TableHead>名稱</TableHead><TableHead>類型</TableHead><TableHead>大小</TableHead><TableHead>狀態</TableHead></TableRow>
    </TableHeader>
    <TableBody>
      <TableRow><TableCell>114年第1季醫院總額執行報告.pdf</TableCell><TableCell>PDF</TableCell><TableCell>471 KB</TableCell><TableCell><Badge variant="secondary">已加入知識庫</Badge></TableCell></TableRow>
      <TableRow><TableCell>急診照護品質方案.docx</TableCell><TableCell>Word</TableCell><TableCell>94 KB</TableCell><TableCell><Badge variant="outline">僅此對話</Badge></TableCell></TableRow>
    </TableBody>
  </Table>
);
