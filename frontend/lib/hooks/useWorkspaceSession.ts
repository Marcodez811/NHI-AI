"use client";

import { useState } from "react";
import type { Category } from "../api/documents";
import type { Tone, View } from "../workspace/types";

/**
 * UI-only workspace state. Keeping this separate from transport effects makes
 * the route shell reusable while preserving the analyst's working set across
 * sibling workflow routes.
 */
export function useWorkspaceSession() {
  const [view, setView] = useState<View>("files");
  const [selected, setSelected] = useState<string[]>([]);
  const [folderId, setFolderId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [pending, setPending] = useState<File[]>([]);
  const [uploadCategory, setUploadCategory] = useState<Category>("bei_can");
  const [uploadFolderId, setUploadFolderId] = useState<string | null>(null);
  const [slideTitle, setSlideTitle] = useState("2026 健保政策重點整理");
  const [slideCount, setSlideCount] = useState(10);
  const [guidance, setGuidance] = useState("");
  const [tone, setTone] = useState<Tone>("formal");

  return {
    view,
    setView,
    selected,
    setSelected,
    folderId,
    setFolderId,
    query,
    setQuery,
    uploadOpen,
    setUploadOpen,
    pending,
    setPending,
    uploadCategory,
    setUploadCategory,
    uploadFolderId,
    setUploadFolderId,
    slideTitle,
    setSlideTitle,
    slideCount,
    setSlideCount,
    guidance,
    setGuidance,
    tone,
    setTone,
  };
}
