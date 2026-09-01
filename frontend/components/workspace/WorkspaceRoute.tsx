"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { WorkspaceContent } from "./WorkspaceContent";
import { useWorkspace } from "./WorkspaceProvider";

const routeToView = {
  "/chat": "chat",
  "/knowledge": "files",
  "/slides": "slides",
} as const;

/** Route adapter: URLs choose a view while state remains in the shared shell. */
export function WorkspaceRoute() {
  const pathname = usePathname();
  const router = useRouter();
  const workspace = useWorkspace();
  const view = routeToView[pathname as keyof typeof routeToView] ?? "chat";

  useEffect(() => {
    if (workspace.view !== view) workspace.setView(view);
  }, [view, workspace]);

  return (
    <WorkspaceContent
      workspace={workspace}
      view={view}
      onBrowseSources={() => router.push("/knowledge")}
      onUploadSources={() => {
        workspace.openUpload();
        router.push("/knowledge");
      }}
    />
  );
}
