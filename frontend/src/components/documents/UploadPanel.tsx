"use client";

import { AlertCircle, CheckCircle2, Copy, FileText, UploadCloud, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { apiFetch } from "@/lib/client-fetch";
import { checkFiles, formatBytes, type CheckedFile } from "@/lib/validation";
import type { FileResult, UploadLimits, UploadSummary } from "@/types/api";

type Phase = "selecting" | "uploading" | "results";

export function UploadPanel() {
  const [limits, setLimits] = useState<UploadLimits | null>(null);
  const [limitsError, setLimitsError] = useState<string | null>(null);
  const [selected, setSelected] = useState<CheckedFile[]>([]);
  const [phase, setPhase] = useState<Phase>("selecting");
  const [summary, setSummary] = useState<UploadSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Limits come from the server so client rules cannot drift from it (§16).
  useEffect(() => {
    // Plain `fetch`, unlike every other call on this screen: the
    // upload-limits route answers 401 UNAUTHORIZED when the *backend* is
    // unreachable as well as when the session is dead, so routing it
    // through `apiFetch` would sign people out over an outage.
    fetch("/api/config/upload-limits")
      .then((r) => r.json())
      .then((body) => {
        if (body.success) setLimits(body.data);
        else setLimitsError("Could not load upload limits.");
      })
      .catch(() => setLimitsError("Could not load upload limits."));
  }, []);

  const addFiles = useCallback(
    (incoming: FileList | null) => {
      if (!incoming || !limits) return;
      const combined = [...selected.map((s) => s.file), ...Array.from(incoming)];
      setSelected(checkFiles(combined, limits));
      setPhase("selecting");
      setSummary(null);
      setError(null);
    },
    [limits, selected],
  );

  function removeAt(index: number) {
    if (!limits) return;
    const remaining = selected.filter((_, i) => i !== index).map((s) => s.file);
    setSelected(checkFiles(remaining, limits));
  }

  const valid = selected.filter((f) => f.status === "ready");
  const invalid = selected.filter((f) => f.status === "invalid");

  async function submit() {
    if (!valid.length) return;
    setPhase("uploading");
    setError(null);

    const form = new FormData();
    // Invalid files are never sent (§16).
    valid.forEach((f) => form.append("files", f.file, f.file.name));

    try {
      const response = await apiFetch("/api/documents/upload", { method: "POST", body: form });
      const body = await response.json();

      if (!response.ok || !body.success) {
        setError(body.message ?? "Upload failed.");
        setPhase("selecting");
        return;
      }
      setSummary(body.data);
      setSelected([]);
      setPhase("results");
    } catch {
      setError("Could not reach the server.");
      setPhase("selecting");
    }
  }

  if (limitsError) {
    return (
      <div role="alert" className="rounded-[32px] bg-error-container px-8 py-6 text-on-error-container">
        {limitsError}
      </div>
    );
  }

  if (!limits) {
    return <div className="h-64 animate-pulse rounded-[32px] bg-surface-container" aria-busy />;
  }

  // --- Results state: the Stitch screen stops at the button; this is what
  //     happens after (docs/frontend/screens.md).
  if (phase === "results" && summary) {
    return (
      <div className="space-y-6">
        <div className="flex flex-wrap items-center gap-3">
          <SummaryPill label="accepted" count={summary.accepted} tone="COMPLETED" />
          <SummaryPill label="duplicate" count={summary.duplicates} tone="DUPLICATE" />
          <SummaryPill label="rejected" count={summary.rejected} tone="FAILED" />
        </div>

        <div className="overflow-hidden rounded-[32px] border border-outline-variant bg-surface-container-lowest">
          <ul className="divide-y divide-outline-variant">
            {summary.results.map((result, i) => (
              <ResultRow key={`${result.file_name}-${i}`} result={result} />
            ))}
          </ul>
        </div>

        <p className="font-mono text-xs text-on-surface-variant">
          Accepted documents are queued for processing. The pipeline that extracts, classifies and
          indexes them lands in Phase&nbsp;4.
        </p>

        <Button variant="secondary" onClick={() => { setSummary(null); setPhase("selecting"); }}>
          Upload more
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") inputRef.current?.click(); }}
        role="button"
        tabIndex={0}
        aria-label="Choose files to upload"
        className={`cursor-pointer rounded-[32px] border-2 border-dashed px-8 py-14 text-center transition-colors ${
          dragging
            ? "border-primary bg-primary/5"
            : "border-outline-variant bg-surface-container-lowest hover:border-primary hover:bg-surface-container-low"
        }`}
      >
        <UploadCloud className="mx-auto mb-4 text-outline" size={40} aria-hidden />
        <h3 className="mb-1 text-xl font-semibold text-on-background">Drag &amp; drop files here</h3>
        <p className="mb-5 text-sm text-primary">or click to browse</p>

        <div className="flex flex-col items-center gap-2">
          <span className="rounded-full bg-surface-container-high px-4 py-1 font-mono text-[11px] uppercase tracking-wider text-on-surface-variant">
            {[...limits.document_extensions, ...limits.image_extensions].join(" · ")}
          </span>
          <span className="font-mono text-[11px] text-outline">
            Max {limits.max_document_size_mb} MB documents · {limits.max_image_size_mb} MB images ·{" "}
            {limits.max_files_per_batch} files per batch
          </span>
        </div>

        <input
          ref={inputRef}
          type="file"
          multiple
          hidden
          onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
        />
      </div>

      {error && (
        <div role="alert" className="rounded-full bg-error-container px-6 py-3 text-sm text-on-error-container">
          {error}
        </div>
      )}

      {selected.length > 0 && (
        <div className="overflow-hidden rounded-[32px] border border-outline-variant bg-surface-container-lowest">
          <div className="flex items-center justify-between border-b border-outline-variant px-8 py-4">
            <h4 className="font-mono text-xs uppercase tracking-wider text-on-surface-variant">
              Selected files ({selected.length})
            </h4>
            {invalid.length > 0 && (
              <span className="font-mono text-xs text-error">
                {invalid.length} will not be sent
              </span>
            )}
          </div>

          <ul className="divide-y divide-outline-variant">
            {selected.map((file, i) => (
              <li
                key={`${file.name}-${i}`}
                className={`flex items-center gap-4 px-8 py-3 ${
                  file.status === "invalid" ? "bg-error-container/25" : ""
                }`}
              >
                <FileText
                  size={20}
                  className={file.status === "invalid" ? "shrink-0 text-outline" : "shrink-0 text-primary"}
                  aria-hidden
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-on-background">{file.name}</p>
                  <p
                    className={`font-mono text-[11px] ${
                      file.status === "invalid" ? "text-error" : "text-on-surface-variant"
                    }`}
                  >
                    {formatBytes(file.size)}
                    {file.message ? ` — ${file.message}` : ""}
                  </p>
                </div>
                <StatusBadge status={file.status === "ready" ? "READY" : "INVALID"} />
                <button
                  onClick={() => removeAt(i)}
                  aria-label={`Remove ${file.name}`}
                  className="rounded-full p-1.5 text-outline transition-colors hover:bg-surface-container-high hover:text-error"
                >
                  <X size={16} aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {selected.length > 0 && (
        <div className="flex items-center justify-end gap-3">
          <Button variant="secondary" onClick={() => setSelected([])} disabled={phase === "uploading"}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!valid.length || phase === "uploading"}>
            {phase === "uploading"
              ? "Uploading…"
              : `Upload ${valid.length} file${valid.length === 1 ? "" : "s"}`}
          </Button>
        </div>
      )}
    </div>
  );
}

function SummaryPill({ label, count, tone }: { label: string; count: number; tone: string }) {
  if (!count) return null;
  return (
    <span className="inline-flex items-center gap-2">
      <StatusBadge status={tone} />
      <span className="text-sm text-on-surface-variant">
        {count} {label}
        {count === 1 ? "" : "s"}
      </span>
    </span>
  );
}

function ResultRow({ result }: { result: FileResult }) {
  const Icon =
    result.status === "QUEUED" ? CheckCircle2 : result.status === "DUPLICATE" ? Copy : AlertCircle;
  const tone =
    result.status === "QUEUED"
      ? "text-[--color-status-completed-fg]"
      : result.status === "DUPLICATE"
        ? "text-[--color-status-duplicate-fg]"
        : "text-error";

  return (
    <li className="flex items-center gap-4 px-8 py-3">
      <Icon size={20} className={`shrink-0 ${tone}`} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-on-background">{result.file_name}</p>
        {(result.message || result.duplicate_of) && (
          <p className="truncate font-mono text-[11px] text-on-surface-variant">
            {result.duplicate_of
              ? `Duplicate of ${result.duplicate_of.file_name}`
              : result.message}
          </p>
        )}
      </div>
      <StatusBadge status={result.status} />
    </li>
  );
}
