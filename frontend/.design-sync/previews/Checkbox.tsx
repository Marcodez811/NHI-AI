import { Checkbox, Label } from "nhi-ai-ui";

export const States = () => (
  <div className="flex flex-col gap-3">
    <div className="flex items-center gap-2"><Checkbox id="c1" defaultChecked /><Label htmlFor="c1">個別醫院前瞻式預算分區共管試辦計畫.docx</Label></div>
    <div className="flex items-center gap-2"><Checkbox id="c2" /><Label htmlFor="c2">健康台灣三高防治888.pdf</Label></div>
    <div className="flex items-center gap-2"><Checkbox id="c3" disabled /><Label htmlFor="c3">急診照護品質方案.pdf（處理中）</Label></div>
    <div className="flex items-center gap-2"><Checkbox id="c4" aria-invalid /><Label htmlFor="c4">我已確認資料來源</Label></div>
  </div>
);
