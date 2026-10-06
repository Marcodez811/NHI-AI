import { Skeleton } from "nhi-ai-ui";

export const ListLoading = () => (
  <div className="flex max-w-md flex-col gap-3">
    {[0, 1, 2].map((i) => (
      <div key={i} className="flex items-center gap-3">
        <Skeleton className="size-9 rounded-lg" />
        <div className="flex flex-1 flex-col gap-1.5">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      </div>
    ))}
  </div>
);

export const CardLoading = () => (
  <div className="flex max-w-sm flex-col gap-2 rounded-xl border border-border p-4">
    <Skeleton className="h-5 w-2/3" />
    <Skeleton className="h-4 w-full" />
    <Skeleton className="h-4 w-5/6" />
  </div>
);
