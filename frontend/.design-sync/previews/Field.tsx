import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel, FieldLegend, FieldSet, Input, Switch, Textarea } from "nhi-ai-ui";

export const Form = () => (
  <FieldSet className="max-w-md">
    <FieldLegend>加入知識庫</FieldLegend>
    <FieldGroup>
      <Field>
        <FieldLabel htmlFor="doc-name">文件名稱</FieldLabel>
        <Input id="doc-name" defaultValue="114年第1季醫院總額執行報告" />
        <FieldDescription>顯示在知識庫清單與資料來源中。</FieldDescription>
      </Field>
      <Field>
        <FieldLabel htmlFor="doc-note">備註</FieldLabel>
        <Textarea id="doc-note" placeholder="例如：資料截至 114 年 3 月" />
      </Field>
    </FieldGroup>
  </FieldSet>
);

export const WithError = () => (
  <Field data-invalid className="max-w-md">
    <FieldLabel htmlFor="category">分類</FieldLabel>
    <Input id="category" aria-invalid placeholder="請選擇分類" />
    <FieldError errors={[{ message: "加入知識庫前請先選擇分類。" }]} />
  </Field>
);

export const Horizontal = () => (
  <Field orientation="horizontal" className="max-w-md">
    <FieldLabel htmlFor="retrieval">開放 AI 助理搜尋</FieldLabel>
    <Switch id="retrieval" defaultChecked />
  </Field>
);
