"use client";

import {
  AlertCircle,
  Check,
  Copy,
  Loader2,
  RefreshCw,
  Trash2,
  UserPlus,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { apiFetch } from "@/lib/client-fetch";
import type { OrganizationAdmin, OrganizationDetail } from "@/types/api";

/**
 * Fetch and parse one endpoint, returning null rather than throwing.
 *
 * `response.json()` throws on any non-JSON body, and a Next.js 404 is an HTML
 * page. Callers that treat a fetch as "it either parses or the component is
 * over" render nothing at all in exactly the situation where the user most
 * needs to be told something.
 */
async function readJson(
  url: string,
): Promise<{ success: boolean; data?: unknown; message?: string } | null> {
  try {
    const response = await apiFetch(url);
    const text = await response.text();

    try {
      return JSON.parse(text);
    } catch {
      // An HTML error page, an empty body, a proxy's plain-text reply. The
      // status is the only reliable thing left, so it becomes the message.
      return {
        success: false,
        message: `The server replied with ${response.status} ${
          response.statusText || "error"
        } instead of data.`,
      };
    }
  } catch {
    return { success: false, message: "Could not reach the server." };
  }
}

/**
 * Organization detail, or the create form when `organizationId` is null.
 *
 * Everything shown here is a count or a setting. The Super Admin never sees a
 * document title or a chat message - that is the privacy guarantee, and the
 * backend enforces it by not returning them.
 */
export function OrganizationDrawer({
  organizationId,
  onClose,
  onChanged,
}: {
  organizationId: string | null;
  onClose: () => void;
  onChanged: () => void;
}) {
  const creating = organizationId === null;

  const [detail, setDetail] = useState<OrganizationDetail | null>(null);
  const [admins, setAdmins] = useState<OrganizationAdmin[]>([]);
  const [adminsError, setAdminsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmName, setConfirmName] = useState("");
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(async () => {
    if (!organizationId) return;
    setLoading(true);
    setError(null);

    // The two calls are settled INDEPENDENTLY, not awaited as a pair. They used
    // to share one `await Promise.all([...])` followed by two bare `.json()`
    // calls, and that had two failure modes that both ended in a blank panel:
    // a rejection in either fetch skipped `setDetail` entirely, and a response
    // that was not JSON - a 404 HTML page, say - made `.json()` throw with no
    // catch anywhere, leaving the drawer on "Loading…" for ever with nothing
    // to tell the user. The admins list failing is not a reason to withhold the
    // organization.
    const [org, adminBody] = await Promise.all([
      readJson(`/api/super-admin/organizations/${organizationId}`),
      readJson(`/api/super-admin/organizations/${organizationId}/admins`),
    ]);

    if (org?.success) setDetail(org.data as OrganizationDetail);
    else setError(org?.message ?? "Could not load this organization.");

    if (adminBody?.success) {
      setAdmins((adminBody.data as { items: OrganizationAdmin[] }).items);
      setAdminsError(null);
    } else {
      setAdmins([]);
      setAdminsError(adminBody?.message ?? "Could not load the administrators.");
    }

    setLoading(false);
  }, [organizationId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

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
      onChanged();
      return payload;
    } catch {
      setError("Could not reach the server.");
      return null;
    } finally {
      setBusy(false);
    }
  }

  if (creating) {
    return (
      <Shell title="New organization" onClose={onClose} error={error}>
        <CreateForm
          busy={busy}
          onSubmit={async (values) => {
            const payload = await send("/api/super-admin/organizations", "POST", values);
            if (payload) onClose();
          }}
        />
      </Shell>
    );
  }

  return (
    <Shell
      title={detail?.name ?? "Loading…"}
      subtitle={detail?.slug}
      onClose={onClose}
      error={error}
    >
      {loading && !detail && <Skeleton />}

      {!loading && !detail && (
        // Previously this state rendered nothing at all, which is how a stale
        // route table turned into "organization details is not showing".
        <div className="rounded-[24px] border border-outline-variant px-6 py-10 text-center">
          <p className="font-medium text-on-surface">Could not load this organization</p>
          <p className="mx-auto mt-1 max-w-sm text-sm text-on-surface-variant">
            {error ?? "The server did not return any details."}
          </p>
          <Button size="sm" variant="secondary" className="mt-4" onClick={() => void load()}>
            <RefreshCw size={14} aria-hidden /> Try again
          </Button>
        </div>
      )}

      {detail && (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <StatusBadge status={detail.status} />
            {detail.contact_email && (
              <span className="truncate text-sm text-on-surface-variant">
                {detail.contact_email}
              </span>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label="Admins" value={detail.counts.admins} />
            <Stat label="Documents" value={detail.counts.documents} />
            <Stat label="Published" value={detail.counts.public_documents} />
            <Stat label="Chats" value={detail.counts.conversations} />
          </div>

          <p className="rounded-[20px] bg-surface-container-high px-4 py-3 text-xs text-on-surface-variant">
            Counts only. Document titles and chat contents are never shown here — an
            organization&apos;s data stays with its own administrators.
          </p>

          <Section title="Limits">
            <LimitsForm
              detail={detail}
              busy={busy}
              onSave={async (values) => {
                const payload = await send(
                  `/api/super-admin/organizations/${detail.id}`, "PATCH", values,
                );
                if (payload) {
                  setDetail(payload.data);
                  setNotice("Saved.");
                }
              }}
            />
          </Section>

          <Section title="Public chatbot">
            <label className="flex items-center gap-3 text-sm text-on-surface">
              <input
                type="checkbox"
                checked={detail.public_chat_enabled}
                onChange={async (event) => {
                  const payload = await send(
                    `/api/super-admin/organizations/${detail.id}`, "PATCH",
                    { public_chat_enabled: event.target.checked },
                  );
                  if (payload) setDetail(payload.data);
                }}
              />
              Answer questions from this organization&apos;s published documents
            </label>
            {detail.public_chat_enabled && <EmbedDetails organizationId={detail.id} />}
          </Section>

          <Section title={`Administrators (${admins.length})`}>
            {adminsError && (
              // Its own message, not the drawer's: the organization loaded, only
              // this list did not, and conflating them hid a working panel.
              <p className="flex items-start gap-2 rounded-[20px] bg-error-container px-4 py-3 text-sm text-on-error-container">
                <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
                {adminsError}
              </p>
            )}
            <AdminList admins={admins} />
            <AddAdminForm
              busy={busy}
              onSubmit={async (values) => {
                const payload = await send(
                  `/api/super-admin/organizations/${detail.id}/admins`, "POST", values,
                );
                if (payload) {
                  await load();
                  setNotice("Administrator added. They can sign in with an OTP.");
                }
              }}
            />
          </Section>

          {notice && <p className="text-sm text-on-surface-variant">{notice}</p>}

          <Section title="Danger zone">
            <div className="flex flex-wrap items-center gap-2">
              {detail.status === "ACTIVE" ? (
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy}
                  onClick={async () => {
                    const payload = await send(
                      `/api/super-admin/organizations/${detail.id}/suspend`, "POST",
                    );
                    if (payload) setDetail(payload.data);
                  }}
                >
                  Suspend
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy}
                  onClick={async () => {
                    const payload = await send(
                      `/api/super-admin/organizations/${detail.id}/activate`, "POST",
                    );
                    if (payload) setDetail(payload.data);
                  }}
                >
                  Reactivate
                </Button>
              )}

              {!confirming ? (
                <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}>
                  <Trash2 size={14} aria-hidden /> Delete
                </Button>
              ) : (
                <div className="flex w-full flex-col gap-2 rounded-[20px] bg-error-container p-4">
                  <p className="text-sm text-on-error-container">
                    Deleting removes this organization&apos;s vectors permanently. Suspending
                    keeps everything and is reversible. Type <strong>{detail.name}</strong> to
                    confirm.
                  </p>
                  <input
                    value={confirmName}
                    onChange={(event) => setConfirmName(event.target.value)}
                    className="h-10 rounded-full border border-outline-variant bg-surface-container-lowest px-4 text-sm text-on-surface outline-none"
                  />
                  <div className="flex gap-2">
                    <Button
                      variant="danger"
                      size="sm"
                      disabled={busy || confirmName !== detail.name}
                      onClick={async () => {
                        const force = detail.counts.documents > 0;
                        const payload = await send(
                          `/api/super-admin/organizations/${detail.id}?force=${force}`,
                          "DELETE",
                        );
                        if (payload) onClose();
                      }}
                    >
                      Delete permanently
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
                      Cancel
                    </Button>
                  </div>
                </div>
              )}
            </div>
          </Section>
        </>
      )}
    </Shell>
  );
}

// --- Pieces --------------------------------------------------------------- //

function Shell({
  title,
  subtitle,
  onClose,
  error,
  children,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  error: string | null;
  children: React.ReactNode;
}) {
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
        aria-label={title}
        className="fixed right-0 top-0 z-50 flex h-full w-full max-w-[560px] flex-col border-l border-outline-variant bg-surface-container-lowest"
      >
        <header className="flex items-start justify-between gap-4 border-b border-outline-variant p-6">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold text-on-surface">{title}</h2>
            {subtitle && (
              <p className="truncate font-mono text-[11px] text-on-surface-variant">
                {subtitle}
              </p>
            )}
          </div>
          <button
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
          {children}
        </div>
      </aside>
    </>
  );
}

/**
 * The two ways to put the chatbot in front of visitors: a link, or the script
 * tag that drops it into someone else's site.
 *
 * Both key on the organization id rather than the slug, so renaming an
 * organization never breaks an embed that is already live on a customer's page.
 */
function EmbedDetails({ organizationId }: { organizationId: string }) {
  const [copied, setCopied] = useState<string | null>(null);

  // The host is only known in the browser: this drawer is a client component,
  // and the server has no reliable idea what address a visitor reaches it on.
  const [origin, setOrigin] = useState("");
  useEffect(() => setOrigin(window.location.origin), []);

  const link = `${origin}/${organizationId}/chat`;
  const snippet = `<script src="${origin}/widget.js"\n        data-organization="${organizationId}"\n        defer></script>`;

  async function copy(label: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      window.setTimeout(() => setCopied(null), 2000);
    } catch {
      setCopied(null);
    }
  }

  return (
    <div className="mt-3 space-y-3">
      <div>
        <div className="flex items-center justify-between gap-2">
          <span className="px-2 text-xs font-medium text-on-surface-variant">
            Direct link
          </span>
          <button
            type="button"
            onClick={() => copy("link", link)}
            className="flex items-center gap-1 rounded-full px-3 py-1 text-xs text-on-surface-variant transition-colors hover:bg-surface-container-low"
          >
            {copied === "link" ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
            {copied === "link" ? "Copied" : "Copy"}
          </button>
        </div>
        <a
          href={link}
          target="_blank"
          rel="noreferrer"
          className="mt-1 block break-all rounded-[20px] bg-surface-container-high px-4 py-3 font-mono text-[11px] text-primary"
        >
          {link || `/${organizationId}/chat`}
        </a>
      </div>

      <div>
        <div className="flex items-center justify-between gap-2">
          <span className="px-2 text-xs font-medium text-on-surface-variant">
            Embed on a website
          </span>
          <button
            type="button"
            onClick={() => copy("snippet", snippet)}
            className="flex items-center gap-1 rounded-full px-3 py-1 text-xs text-on-surface-variant transition-colors hover:bg-surface-container-low"
          >
            {copied === "snippet" ? (
              <Check size={12} aria-hidden />
            ) : (
              <Copy size={12} aria-hidden />
            )}
            {copied === "snippet" ? "Copied" : "Copy"}
          </button>
        </div>
        <pre className="mt-1 overflow-x-auto rounded-[20px] bg-surface-container-high px-4 py-3 font-mono text-[11px] text-on-surface-variant">
          {snippet}
        </pre>
      </div>

      <p className="px-2 text-xs text-on-surface-variant">
        Visitors need no account. The assistant answers only from documents this
        organization has published — everything else stays invisible to it.
      </p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-[18px] border border-outline-variant px-4 py-3">
      <div className="text-xl font-semibold leading-none text-on-surface">{value}</div>
      <div className="mt-1.5 text-[11px] uppercase tracking-wider text-on-surface-variant">
        {label}
      </div>
    </div>
  );
}

/** Shows the shape of what is coming, so the panel is never simply empty. */
function Skeleton() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading organization">
      <div className="h-7 w-28 animate-pulse rounded-full bg-surface-container-high" />
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[0, 1, 2, 3].map((index) => (
          <div
            key={index}
            className="h-[68px] animate-pulse rounded-[18px] bg-surface-container-high"
          />
        ))}
      </div>
      <div className="h-16 animate-pulse rounded-[20px] bg-surface-container-high" />
      <div className="h-32 animate-pulse rounded-[20px] bg-surface-container-high" />
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3 border-t border-outline-variant pt-5 first:border-0 first:pt-0">
      <h3 className="text-sm font-semibold text-on-surface">{title}</h3>
      {children}
    </section>
  );
}

function CreateForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (values: Record<string, unknown>) => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [contact, setContact] = useState("");

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ name, slug, contact_email: contact || null });
      }}
      className="space-y-4"
    >
      <Field label="Name">
        <input
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            // Suggest a slug, but leave it editable - it names the Qdrant
            // collection and cannot change afterwards.
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
      <p className="px-2 text-xs text-on-surface-variant">
        Lowercase letters, digits and hyphens. Fixed after creation — it names the
        organization&apos;s vector collection.
      </p>

      <Field label="Contact email (optional)">
        <input
          type="email"
          value={contact}
          onChange={(event) => setContact(event.target.value)}
          className={inputClass}
        />
      </Field>

      <Button type="submit" className="w-full" disabled={busy || !name || !slug}>
        {busy && <Loader2 size={15} className="animate-spin" aria-hidden />}
        Create organization
      </Button>
    </form>
  );
}

function LimitsForm({
  detail,
  busy,
  onSave,
}: {
  detail: OrganizationDetail;
  busy: boolean;
  onSave: (values: Record<string, unknown>) => void;
}) {
  const [maxDocuments, setMaxDocuments] = useState(
    detail.limits.max_documents?.toString() ?? "",
  );
  const [chatLimit, setChatLimit] = useState(detail.limits.rate_limit_chat ?? "");
  const [publicLimit, setPublicLimit] = useState(
    detail.limits.rate_limit_public_chat ?? "",
  );

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSave({
          max_documents: maxDocuments ? Number(maxDocuments) : null,
          rate_limit_chat: chatLimit || null,
          rate_limit_public_chat: publicLimit || null,
        });
      }}
      className="space-y-3"
    >
      <Field label="Maximum documents">
        <input
          type="number"
          min={1}
          value={maxDocuments}
          onChange={(event) => setMaxDocuments(event.target.value)}
          placeholder="Server default"
          className={inputClass}
        />
      </Field>

      <Field label="Chat rate limit">
        <input
          value={chatLimit}
          onChange={(event) => setChatLimit(event.target.value)}
          placeholder="30/minute"
          className={`${inputClass} font-mono`}
        />
      </Field>

      <Field label="Public chat rate limit">
        <input
          value={publicLimit}
          onChange={(event) => setPublicLimit(event.target.value)}
          placeholder="20/minute"
          className={`${inputClass} font-mono`}
        />
      </Field>

      <p className="px-2 text-xs text-on-surface-variant">
        Empty means the server default, never unlimited. Rate limits look like
        <span className="font-mono"> 30/minute</span> — a bare number is rejected.
      </p>

      <Button type="submit" size="sm" disabled={busy}>
        Save limits
      </Button>
    </form>
  );
}

function AdminList({ admins }: { admins: OrganizationAdmin[] }) {
  if (admins.length === 0) {
    return (
      <p className="text-sm text-on-surface-variant">
        No administrators yet. Without one, nobody can sign in to this organization.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {admins.map((admin) => (
        <li
          key={admin.id}
          className="flex items-center justify-between gap-3 rounded-[20px] border border-outline-variant px-4 py-3"
        >
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-on-surface">
              {admin.full_name}
            </p>
            <p className="truncate font-mono text-[11px] text-on-surface-variant">
              {admin.email}
              {admin.mobile && ` · ${admin.mobile}`}
            </p>
          </div>
          <span className="shrink-0 font-mono text-[11px] text-on-surface-variant">
            {admin.is_active ? (admin.last_login_at ? "active" : "never signed in") : "disabled"}
          </span>
        </li>
      ))}
    </ul>
  );
}

function AddAdminForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (values: Record<string, unknown>) => void;
}) {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [mobile, setMobile] = useState("");

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ full_name: fullName, email, mobile: mobile || null });
        setFullName("");
        setEmail("");
        setMobile("");
      }}
      className="space-y-3 rounded-[20px] border border-outline-variant p-4"
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Full name">
          <input
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            className={inputClass}
            required
            minLength={2}
          />
        </Field>
        <Field label="Email">
          <input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className={inputClass}
            required
          />
        </Field>
      </div>

      <Field label="Mobile (optional)">
        <input
          value={mobile}
          onChange={(event) => setMobile(event.target.value)}
          placeholder="+91…"
          className={inputClass}
        />
      </Field>

      <p className="px-2 text-xs text-on-surface-variant">
        They sign in with a one-time code — no password is created, and none can be
        reset from here.
      </p>

      <Button type="submit" size="sm" disabled={busy || !fullName || !email}>
        <UserPlus size={14} aria-hidden /> Add administrator
      </Button>
    </form>
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
