"use client";

import { WorkspaceShell } from "../components/workspace/WorkspaceShell";
import { useWorkspaceController } from "../lib/hooks/useWorkspaceController";

export default function Page() {
    const workspace = useWorkspaceController();
    return <WorkspaceShell workspace={workspace} />;
}
