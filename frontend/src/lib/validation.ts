/**
 * Client-side file validation.
 *
 * UX ONLY. This is not a security boundary (spec §16) — the backend re-checks
 * everything independently, including the file signature, which cannot be
 * verified here without reading the bytes.
 *
 * Rules come from GET /api/config/upload-limits so they cannot drift from the
 * server's own configuration.
 */
import type { UploadLimits } from "@/types/api";

export type ClientStatus = "ready" | "invalid";

export interface CheckedFile {
  file: File;
  name: string;
  size: number;
  status: ClientStatus;
  errorCode?: string;
  message?: string;
}

export function extensionOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function checkFiles(files: File[], limits: UploadLimits): CheckedFile[] {
  const seen = new Map<string, string>();

  return files.map((file) => {
    const ext = extensionOf(file.name);
    const base = { file, name: file.name, size: file.size };

    if (limits.legacy_extensions.includes(ext)) {
      const suggestion = ext === "doc" ? ".docx" : ".pptx";
      return {
        ...base,
        status: "invalid" as const,
        errorCode: "LEGACY_FORMAT_UNSUPPORTED",
        message: `.${ext} is not supported — save as ${suggestion}`,
      };
    }

    const allowed = [...limits.document_extensions, ...limits.image_extensions];
    if (!ext || !allowed.includes(ext)) {
      return {
        ...base,
        status: "invalid" as const,
        errorCode: "INVALID_FILE_TYPE",
        message: ext ? `.${ext} is not a supported format` : "File has no extension",
      };
    }

    if (file.size === 0) {
      return { ...base, status: "invalid" as const, errorCode: "FILE_EMPTY", message: "File is empty" };
    }

    const isImage = limits.image_extensions.includes(ext);
    const limitMb = isImage ? limits.max_image_size_mb : limits.max_document_size_mb;
    if (file.size > limitMb * 1024 * 1024) {
      return {
        ...base,
        status: "invalid" as const,
        errorCode: "FILE_TOO_LARGE",
        message: `Maximum size exceeded — ${limitMb} MB for ${isImage ? "images" : "documents"}`,
      };
    }

    // Within-batch duplicates. Name+size is a heuristic: the server hashes the
    // bytes and is the authority.
    const key = `${file.name}:${file.size}`;
    if (seen.has(key)) {
      return {
        ...base,
        status: "invalid" as const,
        errorCode: "DUPLICATE_IN_BATCH",
        message: "Already selected",
      };
    }
    seen.set(key, file.name);

    return { ...base, status: "ready" as const };
  });
}
