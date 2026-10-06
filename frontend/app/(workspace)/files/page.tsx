import { Suspense } from "react";
import { FilesPage } from "../../../components/files/FilesPage";

export default function MyFilesPage() {
  return (
    <Suspense fallback={null}>
      <FilesPage />
    </Suspense>
  );
}
