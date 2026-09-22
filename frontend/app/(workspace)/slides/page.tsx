import { WorkspaceRoute } from "../../../components/workspace/WorkspaceRoute";
import { Suspense } from "react";

export default function SlidesPage() {
  return (
    <Suspense fallback={null}>
      <WorkspaceRoute />
    </Suspense>
  );
}
