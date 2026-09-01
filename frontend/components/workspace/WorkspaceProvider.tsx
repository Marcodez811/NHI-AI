"use client";

import { createContext, useContext } from "react";
import {
  useWorkspaceController,
  type WorkspaceController,
} from "../../lib/hooks/useWorkspaceController";

const WorkspaceContext = createContext<WorkspaceController | null>(null);

/**
 * Keeps the user's working set alive while sibling workflow routes change.
 * State intentionally lives in memory; a full reload starts a fresh workspace.
 */
export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const workspace = useWorkspaceController();
  return <WorkspaceContext.Provider value={workspace}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceController {
  const workspace = useContext(WorkspaceContext);
  if (!workspace) {
    throw new Error("useWorkspace must be used inside WorkspaceProvider");
  }
  return workspace;
}
