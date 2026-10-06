import { ConfirmDialog } from "nhi-ai-ui";

export const DeleteConversation = () => (
  <ConfirmDialog
    open
    title="刪除對話「114年第1季點值分析」？"
    description="此操作無法復原。對話中使用的檔案會保留在「我的檔案」。"
    onConfirm={() => {}}
    onCancel={() => {}}
  />
);
