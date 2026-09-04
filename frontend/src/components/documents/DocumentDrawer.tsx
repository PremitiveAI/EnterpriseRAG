"use client";

import {
  AlertCircle,
  Download,
  Globe,
  Loader2,
  Lock,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { apiFetch } from "@/lib/client-fetch";
import { formatBytes } from "@/lib/validation";
import type {
  CategoryRef,
  DocumentDetail,
  DocumentStatusSnapshot,
} from "@/types/api";

const ACTIVE_POLL_MS = 2000;

/** A drawer rather than a route change, so the list keeps its scroll and filters. */
export function DocumentDrawer({
  documentId,
  categories,
  onClose,
  onChanged,
}: {
  documentId: string;
  categories: CategoryRef[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    const response = await apiFetch(`/api/documents/${documentId}`);
    const body = await response.json();
    if (body.success) {
      setDetail(body.data);
      setError(null);
    } else {
      setError(body.message ?? "Could not load this document.");
    }
  }, [documentId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  // Escape closes, matching every other drawer the admin has ever used.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Poll only while the document is moving, and stop at a terminal state —
  // the endpoint reports `is_terminal` so the client never hard-codes the set.
  useEffect(() => {
    if (!detail || isTerminal(detail.status)) return;

    const timer = setInterval(async () => {
      const response = await apiFetch(`/api/documents/${documentId}/status`);
      const body = await response.json();
      if (!body.success) return;

      const snapshot = body.data as DocumentStatusSnapshot;
      setDetail((current) => (current ? { ...current, status: snapshot.status } : current));
      if (snapshot.is_terminal) {
        await load();
        onChanged();
      }
    }, ACTIVE_POLL_MS);

    return () => clearInterval(timer);
  }, [detail, documentId, load, onChanged]);

  async function save(payload: Record<string, unknown>) {
    setBusy(true);
    setNotice(null);
    const response = await apiFetch(`/api/documents/${documentId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    setBusy(false);

    if (body.success) {
      setDetail(body.data);
      setNotice("Saved.");
      onChanged();
    } else {
      setError(body.message ?? "Could not save.");
    }
  }

  async function reprocess() {
    setBusy(true);
    setError(null);
    const response = await apiFetch(`/api/documents/${documentId}/reprocess`, { method: "POST" });
    const body = await response.json();
    setBusy(false);

    if (body.success) {
      setNotice("Queued for reprocessing.");
      await load();
      onChanged();
    } else {
      setError(body.message ?? "Could not reprocess.");
    }
  }

  async function setPublic(next: boolean) {
    setBusy(true);
    setError(null);
    const response = await apiFetch(`/api/documents/${documentId}/publish`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_public: next }),
    });
    const body = await response.json();
    setBusy(false);

    if (body.success) {
      setDetail(body.data);
      setNotice(
        next
          ? "Published. The public chatbot can now answer from this document."
          : "Withdrawn. The public chatbot can no longer see this document.",
      );
      onChanged();
    } else {
      setError(body.message ?? "Could not change the publish state.");
    }
  }

  async function remove() {
    setBusy(true);
    const response = await apiFetch(`/api/documents/${documentId}`, { method: "DELETE" });
    const body = await response.json();
    setBusy(false);

    if (body.success) {
      onChanged();
      onClose();
    } else {
      setError(body.message ?? "Could not delete.");
      setConfirmingDelete(false);
    }
  }

  const editable = detail !== null && isTerminal(detail.status);

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-inverse-surface/25 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Document detail"
        className="fixed right-0 top-0 z-50 flex h-full w-full max-w-[520px] flex-col border-l border-outline-variant bg-surface-container-lowest"
      >
        <header className="flex items-start justify-between gap-4 border-b border-outline-variant p-6">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold text-on-surface">
              {detail?.title ?? detail?.file_name ?? "Loading…"}
            </h2>
            {detail && (
              <p className="truncate font-mono text-[11px] text-on-surface-variant">
                {detail.original_file_name}
              </p>
            )}
          </div>
          <button
            ref={closeRef}
            onClick={onClose}
            aria-label="Close"
            className="rounded-full p-2 text-on-surface-variant transition-colors hover:bg-surface-container-low"
          >
            <X size={18} aria-hidden />
          </button>
        </header>

        <div className="flex-1 space-y-6 overflow-y-auto p-6">
          {error && (
            <p className="flex items-start gap-2 rounded-[20px] bg-error-container px-4 py-3 text-sm text-on-error-container">
              <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
              {error}
            </p>
          )}

          {detail && (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <StatusBadge status={detail.status} />
                <span className="text-sm text-on-surface-variant">
                  {formatBytes(detail.file_size)} · {detail.file_type.toUpperCase()}
                  {detail.page_count ? ` · ${detail.page_count} pages` : ""}
                  {detail.chunk_count ? ` · ${detail.chunk_count} chunks` : ""}
                </span>
              </div>

              {detail.status === "FAILED" && detail.processing?.error_code && (
                <div className="rounded-[20px] border border-outline-variant px-4 py-3">
                  <p className="font-mono text-xs text-error">{detail.processing.error_code}</p>
                  {detail.processing.error_message && (
                    <p className="mt-1 text-sm text-on-surface-variant">
                      {detail.processing.error_message}
                    </p>
                  )}
                  {/* The fix sits next to the diagnosis rather than a click away. */}
                  <Button size="sm" className="mt-3" onClick={reprocess} disabled={busy}>
                    <RefreshCw size={14} aria-hidden /> Reprocess
                  </Button>
                </div>
              )}

              {!editable && (
                <p className="rounded-[20px] bg-surface-container-high px-4 py-3 text-sm text-on-surface-variant">
                  This document is still processing. Editing is disabled until it finishes —
                  the pipeline would overwrite the change.
                </p>
              )}

              <EditForm
                detail={detail}
                categories={categories}
                disabled={!editable || busy}
                onSave={save}
              />

              <PublishPanel
                detail={detail}
                busy={busy}
                editable={editable}
                onChange={setPublic}
              />

              <dl className="space-y-2 border-t border-outline-variant pt-4 text-sm">
                <Row label="Language" value={detail.language ?? "—"} />
                <Row label="Document type" value={detail.document_type ?? "—"} />
                <Row label="Stage" value={detail.processing?.current_stage ?? "—"} />
                <Row
                  label="Uploaded"
                  value={new Date(detail.created_at).toLocaleString()}
                />
                {detail.processing?.duration_ms != null && (
                  <Row
                    label="Processing time"
                    value={`${(detail.processing.duration_ms / 1000).toFixed(1)}s`}
                  />
                )}
              </dl>

              {notice && <p className="text-sm text-on-surface-variant">{notice}</p>}
            </>
          )}
        </div>

        <footer className="flex flex-wrap items-center gap-2 border-t border-outline-variant p-6">
          <a
            href={`/api/documents/${documentId}/download`}
            className="inline-flex h-9 items-center gap-2 rounded-full border border-outline-variant px-5 text-sm text-on-surface transition-colors hover:bg-surface-container-low"
          >
            <Download size={14} aria-hidden /> Download
          </a>
          <Button size="sm" variant="secondary" onClick={reprocess} disabled={busy || !editable}>
            {busy ? <Loader2 size={14} className="animate-spin" aria-hidden /> : <RefreshCw size={14} aria-hidden />}
            Reprocess
          </Button>

          <div className="ml-auto">
            {confirmingDelete ? (
              <div className="flex items-center gap-2">
                <span className="text-sm text-on-surface-variant">
                  Delete {detail?.original_file_name}?
                </span>
                <Button size="sm" variant="danger" onClick={remove} disabled={busy}>
                  Confirm
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirmingDelete(false)}>
                  Cancel
                </Button>
              </div>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => setConfirmingDelete(true)}>
                <Trash2 size={14} aria-hidden /> Delete
              </Button>
            )}
          </div>
        </footer>
      </aside>
    </>
  );
}

/**
 * The publish control.
 *
 * Deliberately not a toggle switch. Everything else in this drawer changes what
 * the organization's own admins see; this one decides whether anonymous
 * strangers on the internet can ask questions about the document, and a switch
 * that flips on a stray click is the wrong affordance for that. Publishing
 * therefore takes a confirmation; withdrawing - the safe direction - does not.
 */
function PublishPanel({
  detail,
  busy,
  editable,
  onChange,
}: {
  detail: DocumentDetail;
  busy: boolean;
  editable: boolean;
  onChange: (next: boolean) => void;
}) {
  const [confirming, setConfirming] = useState(false);

  // Mirrors the backend rule rather than restating it: an unprocessed document
  // has no vectors, so publishing it would advertise an empty corpus. The
  // server answers DOCUMENT_NOT_PUBLISHABLE; saying so here saves the round
  // trip and explains why the button is disabled.
  const publishable = detail.status === "COMPLETED";

  return (
    <section className="space-y-3 border-t border-outline-variant pt-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-on-surface">
            {detail.is_public ? (
              <Globe size={15} className="text-primary" aria-hidden />
            ) : (
              <Lock size={15} aria-hidden />
            )}
            Public chatbot
          </h3>
          <p className="mt-1 text-sm text-on-surface-variant">
            {detail.is_public
              ? "Anyone using this organization's chatbot can get answers from this document."
              : "Private. Only signed-in administrators can see this document."}
          </p>
        </div>

        {detail.is_public ? (
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={() => onChange(false)}
          >
            Withdraw
          </Button>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            disabled={busy || !editable || !publishable}
            onClick={() => setConfirming(true)}
          >
            Publish
          </Button>
        )}
      </div>

      {!publishable && !detail.is_public && (
        <p className="rounded-[20px] bg-surface-container-high px-4 py-3 text-xs text-on-surface-variant">
          Only a processed document can be published — until the pipeline
          finishes there are no vectors for the chatbot to search.
        </p>
      )}

      {confirming && (
        <div className="space-y-2 rounded-[20px] bg-error-container p-4">
          <p className="text-sm text-on-error-container">
            Publishing makes this document answerable by <strong>anyone</strong> who
            opens the chatbot — no sign-in, no account. Its contents will be quoted
            back to strangers.
          </p>
          <p className="text-sm text-on-error-container">
            Check it holds nothing personal or confidential before continuing.
            Identity numbers are masked in answers; names, addresses and dates are
            not.
          </p>
          <div className="flex gap-2 pt-1">
            <Button
              size="sm"
              variant="danger"
              disabled={busy}
              onClick={() => {
                setConfirming(false);
                onChange(true);
              }}
            >
              Publish to the chatbot
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

function EditForm({
  detail,
  categories,
  disabled,
  onSave,
}: {
  detail: DocumentDetail;
  categories: CategoryRef[];
  disabled: boolean;
  onSave: (payload: Record<string, unknown>) => void;
}) {
  const [title, setTitle] = useState(detail.title ?? "");
  const [description, setDescription] = useState(detail.description ?? "");
  const [categoryId, setCategoryId] = useState(detail.category?.id ?? "");
  const [tags, setTags] = useState(detail.tags.join(", "));

  useEffect(() => {
    setTitle(detail.title ?? "");
    setDescription(detail.description ?? "");
    setCategoryId(detail.category?.id ?? "");
    setTags(detail.tags.join(", "));
  }, [detail]);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSave({
          title: title.trim(),
          description: description.trim(),
          category_id: categoryId || null,
          tags: tags
            .split(",")
            .map((tag) => tag.trim())
            .filter(Boolean),
        });
      }}
      className="space-y-4"
    >
      <Field label="Title">
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          disabled={disabled}
          maxLength={512}
          className={inputClass}
        />
      </Field>

      <Field label="Description">
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          disabled={disabled}
          rows={3}
          maxLength={4000}
          className={`${inputClass} min-h-[88px] rounded-[20px] py-3`}
        />
      </Field>

      <Field label="Category">
        <select
          value={categoryId}
          onChange={(event) => setCategoryId(event.target.value)}
          disabled={disabled}
          className={inputClass}
        >
          <option value="">Uncategorised</option>
          {categories.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Tags">
        <input
          value={tags}
          onChange={(event) => setTags(event.target.value)}
          disabled={disabled}
          placeholder="comma, separated"
          className={inputClass}
        />
      </Field>

      <p className="text-xs text-on-surface-variant">
        Metadata only. The document is not re-read or re-embedded — its text has not changed.
      </p>

      <Button type="submit" size="sm" disabled={disabled}>
        Save changes
      </Button>
    </form>
  );
}

const inputClass =
  "h-11 w-full rounded-full border border-outline-variant bg-surface-container-lowest px-5 text-sm text-on-surface outline-none focus:border-primary disabled:opacity-50";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="px-2 text-xs font-medium text-on-surface-variant">{label}</span>
      {children}
    </label>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-on-surface-variant">{label}</dt>
      <dd className="truncate text-on-surface">{value}</dd>
    </div>
  );
}

function isTerminal(status: string): boolean {
  return ["COMPLETED", "FAILED", "DUPLICATE", "DELETED"].includes(status);
}
