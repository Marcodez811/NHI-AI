/** Document and folder transport contracts. */
export {
  CATEGORY_LABELS,
  CATEGORY_VALUES,
  MAX_DOCUMENTS,
  documentDisplayName,
  fetchDocuments,
  getDocument,
  uploadDocument,
  fetchFolders,
  createFolder,
  renameFolder,
  deleteFolder,
  updateDocument,
  deleteDocument,
  getIngestionStatus,
  getDocumentDownloadUrl,
} from "../api";

export type {
  Category,
  DocumentRead,
  DocumentStatus,
  DocumentUpdate,
  DocumentListFilters,
  DocumentUploadResponse,
  FolderRead,
  IngestionJobRead,
  QaModeInfo,
} from "../api";
