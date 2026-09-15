"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { WorkspaceContent } from "./WorkspaceContent";
import { useWorkspace } from "./WorkspaceProvider";

const routeToView = {
  "/chat": "chat",
  "/knowledge": "files",
  "/slides": "slides",
  "/workflows": "workflows",
} as const;

/** Route adapter: URLs choose a view while state remains in the shared shell. */
export function WorkspaceRoute() {
  const pathname = usePathname();
  const router = useRouter();
  const workspace = useWorkspace();
  const view = routeToView[pathname as keyof typeof routeToView] ?? "chat";
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
      onBrowseSources={() => router.push("/knowledge")}
      onUploadSources={() => {
        workspace.openUpload();
        router.push("/knowledge");
      }}
    />
  );
}
