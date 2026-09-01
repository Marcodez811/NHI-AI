import { AppShell } from "../../components/app-shell/app-shell";
import { WorkspaceProvider } from "../../components/workspace/WorkspaceProvider";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <WorkspaceProvider>
      <AppShell>{children}</AppShell>
    </WorkspaceProvider>
  );
}
