import { ScrollArea } from "nhi-ai-ui";

const sessions = ["114年第1季點值分析", "三高防治 888 進度", "急診壅塞八大策略", "離島醫療資源現況", "電子處方箋推動情形", "器官移植術後追蹤", "門住診部分負擔改革", "OPAT 門診靜脈抗生素", "ERAS 術後加速康復", "護理人員薪資提升"];

export const SessionList = () => (
  <ScrollArea className="h-56 w-64 rounded-lg border border-border">
    <div className="flex flex-col p-2 text-sm">
      <div className="px-2 pb-1 text-xs text-muted-foreground">最近對話</div>
      {sessions.map((s) => <div key={s} className="truncate rounded-lg px-2 py-1.5 text-muted-foreground hover:bg-accent/60">{s}</div>)}
    </div>
  </ScrollArea>
);
