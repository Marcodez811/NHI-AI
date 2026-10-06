import { Trash2 } from "lucide-react";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogMedia, AlertDialogTitle,
} from "nhi-ai-ui";

export const DeleteDocument = () => (
  <AlertDialog defaultOpen>
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogMedia><Trash2 /></AlertDialogMedia>
        <AlertDialogTitle>刪除「健康台灣三高防治888.pdf」？</AlertDialogTitle>
        <AlertDialogDescription>此操作會移除來源與檢索資料，無法復原。</AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel>取消</AlertDialogCancel>
        <AlertDialogAction variant="destructive">刪除</AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>
);
