"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { WorkspaceContent } from "./WorkspaceContent";
import { useWorkspace } from "./WorkspaceProvider";

const routeToView = {
  "/chat": "chat",
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
  if (segments.length !== 1) return "chat";
  return routeToView[`/${segments[0]}` as keyof typeof routeToView] ?? "chat";
}

/** Route adapter: URLs choose a view while state remains in the shared shell. */
export function WorkspaceRoute() {
  const pathname = usePathname();
  const router = useRouter();
  const workspace = useWorkspace();
  const view = resolveWorkspaceView(pathname);
  const routeSegments = pathname.split("/").filter(Boolean);
  const slideJobId = view === "slides" && routeSegments.length === 2
    ? routeSegments[1]
    : null;
  const newChatHandled = useRef(false);

  useEffect(() => {
    const newChatRequested = new URLSearchParams(window.location.search).get("new") === "1";
    if (view !== "chat" || !newChatRequested) {
      newChatHandled.current = false;
      return;
    }
    if (newChatHandled.current) return;
    // State updates can render again before router.replace removes the query.
    // Claim this request before resetting state, including Strict Mode replay.
    newChatHandled.current = true;
    workspace.startNewChat();
    router.replace("/chat");
  }, [router, view, workspace]);

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
      onUploadSources={() => {
        workspace.openUpload();
        router.push("/knowledge");
      }}
    />
  );
}
