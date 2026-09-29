import { AppShell } from "../../components/app-shell/app-shell";
import { WorkspaceProvider } from "../../components/workspace/WorkspaceProvider";
import { ChatSessionsProvider } from "../../lib/hooks/useChatSessions";
import { ChatEngineProvider } from "../../lib/hooks/useChatEngine";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <WorkspaceProvider>
      <ChatSessionsProvider>
        <ChatEngineProvider>
          <AppShell>{children}</AppShell>
        </ChatEngineProvider>
      </ChatSessionsProvider>
    </WorkspaceProvider>
  );
}
