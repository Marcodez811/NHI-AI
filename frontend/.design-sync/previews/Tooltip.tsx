import { Plus } from "lucide-react";
import { Button, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "nhi-ai-ui";

export const Open = () => (
  <TooltipProvider>
    <div className="flex h-24 items-end justify-center">
      <Tooltip defaultOpen>
        <TooltipTrigger render={<Button size="icon" variant="outline" aria-label="新增" />}><Plus /></TooltipTrigger>
        <TooltipContent>上傳檔案或從知識庫加入</TooltipContent>
      </Tooltip>
    </div>
  </TooltipProvider>
);
