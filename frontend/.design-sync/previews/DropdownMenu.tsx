import { FolderOpen, Library, Paperclip, Pencil, Trash2, Zap } from "lucide-react";
import {
  Button, DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "nhi-ai-ui";

export const AttachMenu = () => (
  <DropdownMenu defaultOpen>
    <DropdownMenuTrigger render={<Button variant="outline" size="sm" />}>附加內容</DropdownMenuTrigger>
    <DropdownMenuContent align="start" className="w-56">
      <DropdownMenuItem><Paperclip />上傳檔案</DropdownMenuItem>
      <DropdownMenuItem><Library />從知識庫加入</DropdownMenuItem>
      <DropdownMenuItem><FolderOpen />從我的檔案加入</DropdownMenuItem>
      <DropdownMenuItem disabled>
        <Zap />使用技能
        <span className="ml-auto text-xs text-muted-foreground">即將推出</span>
      </DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
);

export const SessionActions = () => (
  <DropdownMenu defaultOpen>
    <DropdownMenuTrigger render={<Button variant="ghost" size="sm" />}>更多操作</DropdownMenuTrigger>
    <DropdownMenuContent align="start" className="w-40">
      <DropdownMenuGroup>
        <DropdownMenuLabel>對話</DropdownMenuLabel>
        <DropdownMenuItem><Pencil />重新命名</DropdownMenuItem>
      </DropdownMenuGroup>
      <DropdownMenuSeparator />
      <DropdownMenuItem variant="destructive"><Trash2 />刪除</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
);
