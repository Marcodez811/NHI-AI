import {
  Button,
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Field,
  FieldLabel,
  Input,
} from "nhi-ai-ui";

export const RenameConversation = () => (
  <Dialog defaultOpen>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>重新命名對話</DialogTitle>
        <DialogDescription>新名稱只會顯示在你的對話清單中。</DialogDescription>
      </DialogHeader>
      <Field>
        <FieldLabel htmlFor="title">對話名稱</FieldLabel>
        <Input id="title" defaultValue="114年第1季點值分析" />
      </Field>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" />}>取消</DialogClose>
        <Button>儲存</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
);
