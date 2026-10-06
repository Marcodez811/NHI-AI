import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from "nhi-ai-ui";

const categories = [
  { value: "budget", label: "總額與點值" },
  { value: "payment", label: "支付標準" },
  { value: "policy", label: "政策計畫" },
  { value: "qa", label: "立院質詢" },
];

export const Closed = () => (
  <div className="flex max-w-xs flex-col gap-3">
    <Select items={categories}>
      <SelectTrigger aria-label="分類" className="w-full"><SelectValue placeholder="選擇分類" /></SelectTrigger>
      <SelectContent>{categories.map((c) => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}</SelectContent>
    </Select>
    <Select items={categories} defaultValue="budget">
      <SelectTrigger aria-label="分類" className="w-full"><SelectValue /></SelectTrigger>
      <SelectContent>{categories.map((c) => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}</SelectContent>
    </Select>
  </div>
);

export const Open = () => (
  <div className="max-w-xs">
    <Select items={categories} defaultValue="policy" defaultOpen>
      <SelectTrigger aria-label="分類" className="w-full"><SelectValue /></SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectLabel>知識庫分類</SelectLabel>
          {categories.map((c) => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}
        </SelectGroup>
      </SelectContent>
    </Select>
  </div>
);
