// Barrel for the claude.ai/design sync: the shadcn/Base UI kit in components/ui
// plus the presentational chat pieces. Components that need Next.js routing
// (sidebar, app shell) are left out because they cannot render outside the app.
export * from "../../components/ui/alert";
export * from "../../components/ui/alert-dialog";
export * from "../../components/ui/badge";
export * from "../../components/ui/button";
export * from "../../components/ui/button-group";
export * from "../../components/ui/card";
export * from "../../components/ui/checkbox";
export * from "../../components/ui/confirm-dialog";
export * from "../../components/ui/dialog";
export * from "../../components/ui/dropdown-menu";
export * from "../../components/ui/empty";
export * from "../../components/ui/field";
export * from "../../components/ui/input";
export * from "../../components/ui/label";
export * from "../../components/ui/scroll-area";
export * from "../../components/ui/select";
export * from "../../components/ui/separator";
export * from "../../components/ui/skeleton";
export * from "../../components/ui/switch";
export * from "../../components/ui/table";
export * from "../../components/ui/textarea";
export * from "../../components/ui/tooltip";
export { ChatAttachmentChip } from "../../components/chat/ChatAttachmentChip";
export { ChatComposer } from "../../components/chat/ChatComposer";
export { ChatEmptyState } from "../../components/chat/ChatEmptyState";
export { ChatMessageList } from "../../components/chat/ChatMessageList";
export { ChatModelPicker } from "../../components/chat/ChatModelPicker";
export { ProviderIcon } from "../../components/chat/ProviderIcon";
