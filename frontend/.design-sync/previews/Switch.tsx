import { Label, Switch } from "nhi-ai-ui";

export const States = () => (
  <div className="flex flex-col gap-3">
    <div className="flex items-center gap-2"><Switch id="s1" defaultChecked /><Label htmlFor="s1">開放 AI 助理搜尋</Label></div>
    <div className="flex items-center gap-2"><Switch id="s2" /><Label htmlFor="s2">顯示思考過程</Label></div>
    <div className="flex items-center gap-2"><Switch id="s3" disabled /><Label htmlFor="s3">自動加入知識庫（即將推出）</Label></div>
  </div>
);

export const Small = () => (
  <div className="flex items-center gap-2"><Switch size="sm" defaultChecked id="s4" /><Label htmlFor="s4">精簡模式</Label></div>
);
