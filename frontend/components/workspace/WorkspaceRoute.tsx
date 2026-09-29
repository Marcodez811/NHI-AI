"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";
import { WorkspaceContent } from "./WorkspaceContent";
import { useWorkspace } from "./WorkspaceProvider";

const routeToView = {
  "/knowledge": "files",
  "/slides": "slides",
  "/news": "news",
  "/workflows": "workflows",
} as const;

type WorkspaceView = (typeof routeToView)[keyof typeof routeToView];

/** Match route segments so a job URL keeps the slides view in the shared shell. */
export function resolveWorkspaceView(pathname: string): WorkspaceView {
  const segments = pathname.split("/").filter(Boolean);
  if (segments[0] === "slides" && segments.length === 2) return "slides";
  if (segments.length !== 1) return "files";
  return routeToView[`/${segments[0]}` as keyof typeof routeToView] ?? "files";
}

/** Route adapter: URLs choose a view while state remains in the shared shell. Chat is a
 *  separate route tree (see `app/(workspace)/chat`) and does not go through this switch. */
export function WorkspaceRoute() {
  const pathname = usePathname();
  const router = useRouter();
  const workspace = useWorkspace();
  const view = resolveWorkspaceView(pathname);
  const routeSegments = pathname.split("/").filter(Boolean);
  const slideJobId = view === "slides" && routeSegments.length === 2
    ? routeSegments[1]
    : null;

  useEffect(() => {
    if (workspace.view !== view) workspace.setView(view);
  }, [view, workspace]);

  return (
    <WorkspaceContent
      workspace={workspace}
      view={view}
      slideJobId={slideJobId}
      onSlideStart={async () => {
        const created = await workspace.startSlides();
        if (created) router.push(`/slides/${encodeURIComponent(created.job_id)}`);
      }}
      onBrowseSources={() => router.push("/knowledge")}
    />
  );
}
