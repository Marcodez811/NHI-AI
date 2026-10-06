import { Checkbox, Input, Label } from "nhi-ai-ui";

export const WithInput = () => (
  <div className="flex max-w-sm flex-col gap-2">
    <Label htmlFor="session-title">對話名稱</Label>
    <Input id="session-title" defaultValue="三高防治 888 進度" />
  </div>
);

export const WithCheckbox = () => (
  <div className="flex items-center gap-2">
    <Checkbox id="only-chat" defaultChecked />
    <Label htmlFor="only-chat">只用於此對話</Label>
  </div>
);
