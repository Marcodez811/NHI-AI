import { ChevronDown, Download } from "lucide-react";
import { Button, ButtonGroup, ButtonGroupSeparator, ButtonGroupText } from "nhi-ai-ui";

export const Horizontal = () => (
  <ButtonGroup>
    <Button variant="outline">上傳的檔案</Button>
    <Button variant="outline">產出的文件</Button>
  </ButtonGroup>
);

export const SplitButton = () => (
  <ButtonGroup>
    <Button><Download data-icon="inline-start" />下載 Word</Button>
    <ButtonGroupSeparator />
    <Button size="icon" aria-label="其他格式"><ChevronDown /></Button>
  </ButtonGroup>
);

export const WithText = () => (
  <ButtonGroup>
    <ButtonGroupText>會期</ButtonGroupText>
    <Button variant="outline">11-4</Button>
  </ButtonGroup>
);

export const Vertical = () => (
  <ButtonGroup orientation="vertical">
    <Button variant="outline">簡答版</Button>
    <Button variant="outline">詳答版</Button>
  </ButtonGroup>
);
