import { notFound } from "next/navigation";
import { AgentDevConsole } from "../../../components/dev/AgentDevConsole";

// This is intentionally dynamic so a trusted development deployment can
// toggle the route with its runtime environment rather than a build artifact.
export const dynamic = "force-dynamic";

export default function AgentRunsPage() {
    if (process.env.ENABLE_AGENT_DEV_ROUTES !== "true") notFound();
    return <AgentDevConsole />;
}

