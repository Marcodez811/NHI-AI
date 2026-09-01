"use client";

import { AppShell } from "../app-shell/app-shell";
import type { WorkspaceController } from "../../lib/hooks/useWorkspaceController";
import { WorkspaceContent } from "./WorkspaceContent";

/** Backward-compatible composition entrypoint for non-route consumers. */
export function WorkspaceShell({ workspace }: { workspace: WorkspaceController }) {
  return (
    <AppShell>
      <WorkspaceContent workspace={workspace} view={workspace.view} />
    </AppShell>
  );
}
