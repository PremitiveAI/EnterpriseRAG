"use client";

import { AlertCircle, ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { apiFetch } from "@/lib/client-fetch";
import type { Category } from "@/types/api";

/**
 * The global taxonomy. One list, shared by every organization.
 *
 * Two things on this screen are not ordinary CRUD, and the UI has to say so
 * rather than let someone discover it from an error:
 *
 * - The slug is fixed after creation. Documents and Qdrant payloads store it,
 *   so a rename would orphan every existing classification.
 * - Deleting a category that documents use quietly becomes a deactivation.
 *   Better to say that in the confirmation than to surprise someone with a
 *   success message that did something else.
 */
export function CategoriesScreen({ categories }: { categories: Category[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  async function send(url: string, method: string, body?: unknown) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await apiFetch(url, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const payload = await response.json();
      if (!response.ok || !payload.success) {
        setError(payload.message ?? "That did not work.");
        return null;
      }
      router.refresh();
      return payload;
    } catch {
      setError("Could not reach the server.");
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function move(category: Category, direction: -1 | 1) {
    const ordered = [...categories].sort((a, b) => a.sort_order - b.sort_order);
    const index = ordered.findIndex((c) => c.id === category.id);
    const neighbour = ordered[index + direction];
    if (!neighbour) return;

    // Swap the two sort_order values rather than renumbering the list: one
    // request instead of N, and two Super Admins reordering at once can only
    // disagree about one pair.
    await send(`/api/super-admin/categories/${category.id}`, "PATCH", {
      sort_order: neighbour.sort_order,
    });
    await send(`/api/super-admin/categories/${neighbour.id}`, "PATCH", {
      sort_order: category.sort_order,
    });
  }

  async function remove(category: Category) {
    const payload = await send(`/api/super-admin/categories/${category.id}`, "DELETE");
    if (payload) setNotice(payload.message);
  }

  const ordered = [...categories].sort(
    (a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name),
  );

  return (
    <div className="space-y-6">
      {error && (
        <p className="flex items-start gap-2 rounded-[20px] bg-error-container px-4 py-3 text-sm text-on-error-container">
          <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
          {error}
        </p>
      )}
      {notice && (
        <p className="rounded-[20px] bg-surface-container-high px-4 py-3 text-sm text-on-surface-variant">
          {notice}
        </p>
      )}

      <div className="flex justify-end">
        <Button onClick={() => setCreating((open) => !open)}>
          <Plus size={16} aria-hidden /> New category
        </Button>
      </div>

      {creating && (
        <CreateForm
          busy={busy}
          nextSortOrder={(ordered.at(-1)?.sort_order ?? 0) + 10}
          onCancel={() => setCreating(false)}
          onSubmit={async (values) => {
            const payload = await send("/api/super-admin/categories", "POST", values);
            if (payload) setCreating(false);
          }}
        />
      )}

      {ordered.length === 0 ? (
        <div className="rounded-[32px] border border-outline-variant bg-surface-container-lowest px-8 py-16 text-center">
          <p className="font-medium text-on-surface">No categories</p>
          <p className="mt-1 text-sm text-on-surface-variant">
            With none defined, documents are indexed uncategorised — searchable, but
            not filterable by type.
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {ordered.map((category, index) => (
            <li
              key={category.id}
              className={`rounded-[24px] border border-outline-variant bg-surface-container-lowest p-5 ${
                category.is_active ? "" : "opacity-60"
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-on-surface">{category.name}</span>
                    <span className="font-mono text-[11px] text-on-surface-variant">
                      {category.slug}
                    </span>
                    {!category.is_active && (
                      <span className="rounded-full bg-surface-container-high px-3 py-0.5 font-mono text-[10px] uppercase tracking-wider text-on-surface-variant">
                        inactive
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-on-surface-variant">
                    {category.description || "No description — the classifier gets only the name."}
                  </p>
                  <p className="mt-1 text-xs text-on-surface-variant">
                    {category.document_count} document
                    {category.document_count === 1 ? "" : "s"} across all organizations
                  </p>
                </div>

                <div className="flex shrink-0 items-center gap-1">
                  <IconButton
                    label="Move up"
                    disabled={busy || index === 0}
                    onClick={() => move(category, -1)}
                  >
                    <ArrowUp size={15} aria-hidden />
                  </IconButton>
                  <IconButton
                    label="Move down"
                    disabled={busy || index === ordered.length - 1}
                    onClick={() => move(category, 1)}
                  >
                    <ArrowDown size={15} aria-hidden />
                  </IconButton>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setEditingId((current) =>
                        current === category.id ? null : category.id,
                      )
                    }
                  >
                    {editingId === category.id ? "Close" : "Edit"}
                  </Button>
                </div>
              </div>

              {editingId === category.id && (
                <EditForm
                  category={category}
                  busy={busy}
                  onSave={async (values) => {
                    const payload = await send(
                      `/api/super-admin/categories/${category.id}`, "PATCH", values,
                    );
                    if (payload) setEditingId(null);
                  }}
                  onToggleActive={() =>
                    send(`/api/super-admin/categories/${category.id}`, "PATCH", {
                      is_active: !category.is_active,
                    })
                  }
                  onDelete={() => remove(category)}
                />
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CreateForm({
  busy,
  nextSortOrder,
  onCancel,
  onSubmit,
}: {
  busy: boolean;
  nextSortOrder: number;
  onCancel: () => void;
  onSubmit: (values: Record<string, unknown>) => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({
          name,
          slug,
          description: description || null,
          sort_order: nextSortOrder,
        });
      }}
      className="space-y-4 rounded-[24px] border border-outline-variant bg-surface-container-lowest p-6"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name">
          <input
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              setSlug(
                event.target.value
                  .toLowerCase()
                  .replace(/[^a-z0-9]+/g, "-")
                  .replace(/^-|-$/g, ""),
              );
            }}
            className={inputClass}
            required
            minLength={2}
          />
        </Field>
        <Field label="Slug">
          <input
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
            className={`${inputClass} font-mono`}
            required
            pattern="[a-z0-9]+(-[a-z0-9]+)*"
          />
        </Field>
      </div>

      <p className="px-2 text-xs text-on-surface-variant">
        The slug cannot be changed later — documents and their vectors store it, so a
        rename would leave them pointing at a category that no longer exists.
      </p>

      <Field label="Description">
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={2}
          maxLength={500}
          className={`${textareaClass}`}
          placeholder="Supplier agreements, SOWs and renewal terms."
        />
      </Field>
      <p className="px-2 text-xs text-on-surface-variant">
        This text is sent to the classifier. It is an instruction, not a label — the
        clearer it is, the better documents are sorted.
      </p>

      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || !name || !slug}>
          Create category
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function EditForm({
  category,
  busy,
  onSave,
  onToggleActive,
  onDelete,
}: {
  category: Category;
  busy: boolean;
  onSave: (values: Record<string, unknown>) => void;
  onToggleActive: () => void;
  onDelete: () => void;
}) {
  const [name, setName] = useState(category.name);
  const [description, setDescription] = useState(category.description ?? "");
  const [confirming, setConfirming] = useState(false);

  const inUse = category.document_count > 0;

  return (
    <div className="mt-4 space-y-4 border-t border-outline-variant pt-4">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSave({ name, description: description || null });
        }}
        className="space-y-3"
      >
        <Field label="Name">
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            className={inputClass}
            required
            minLength={2}
          />
        </Field>
        <Field label="Description (sent to the classifier)">
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={2}
            maxLength={500}
            className={textareaClass}
          />
        </Field>
        <Button type="submit" size="sm" disabled={busy}>
          Save
        </Button>
      </form>

      <div className="flex flex-wrap items-center gap-2 border-t border-outline-variant pt-4">
        <Button variant="secondary" size="sm" disabled={busy} onClick={onToggleActive}>
          {category.is_active ? "Deactivate" : "Reactivate"}
        </Button>

        {!confirming ? (
          <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}>
            <Trash2 size={14} aria-hidden /> Delete
          </Button>
        ) : (
          <div className="w-full space-y-2 rounded-[20px] bg-error-container p-4">
            <p className="text-sm text-on-error-container">
              {inUse
                ? `${category.document_count} document(s) use this category, so it will be
                   deactivated rather than deleted — they keep the label they were
                   classified with.`
                : "Nothing uses this category, so it will be removed permanently."}
            </p>
            <div className="flex gap-2">
              <Button variant="danger" size="sm" disabled={busy} onClick={onDelete}>
                {inUse ? "Deactivate it" : "Delete permanently"}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function IconButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="rounded-full p-2 text-on-surface-variant transition-colors hover:bg-surface-container-low disabled:cursor-not-allowed disabled:opacity-30"
    >
      {children}
    </button>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="px-2 text-xs font-medium text-on-surface-variant">{label}</span>
      {children}
    </label>
  );
}

const inputClass =
  "h-11 w-full rounded-full border border-outline-variant bg-surface-container-lowest px-5 text-sm text-on-surface outline-none placeholder:text-on-surface-variant focus:border-primary disabled:opacity-50";

const textareaClass =
  "w-full resize-y rounded-[20px] border border-outline-variant bg-surface-container-lowest px-5 py-3 text-sm text-on-surface outline-none placeholder:text-on-surface-variant focus:border-primary";
