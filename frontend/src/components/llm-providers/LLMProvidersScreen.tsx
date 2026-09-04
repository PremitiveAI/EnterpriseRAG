"use client";

import {
  AlertCircle,
  Check,
  CircleDot,
  KeyRound,
  Plus,
  Trash2,
  Zap,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { apiFetch } from "@/lib/client-fetch";
import type { LLMProvider, LLMProviderName } from "@/types/api";

const PROVIDERS: { value: LLMProviderName; label: string; example: string }[] = [
  { value: "gemini", label: "Google Gemini", example: "gemini-2.0-flash" },
  { value: "openai", label: "OpenAI", example: "gpt-4o" },
  { value: "anthropic", label: "Anthropic", example: "claude-sonnet-4" },
  { value: "azure", label: "Azure OpenAI", example: "my-deployment" },
];

/**
 * Which model answers, for every organization.
 *
 * Three things on this screen are not ordinary CRUD, and the UI says so rather
 * than letting someone find out from an error:
 *
 * - **Registering is not activating.** A saved provider is verified but idle.
 *   One button that did both would make "try this" and "point every tenant at
 *   this" the same gesture.
 * - **The key is write-only.** What comes back is a fingerprint. There is no
 *   field on the wire that could hold the key itself, so nothing here can
 *   reveal one — and an empty key box means "leave it alone", never "clear it".
 * - **Activation is immediate and global.** No restart, no deploy, and it
 *   reaches the background worker too. The confirmation names the model.
 */
export function LLMProvidersScreen({ providers }: { providers: LLMProvider[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  async function send(url: string, method: string, body?: unknown, key = url) {
    setBusy(key);
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
      setNotice(payload.message ?? null);
      router.refresh();
      return payload;
    } catch {
      setError("Could not reach the server.");
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function activate(provider: LLMProvider) {
    const confirmed = window.confirm(
      `Answer every organization's questions with ${provider.provider_name}/` +
        `${provider.model_name}?\n\n` +
        "This takes effect on the next request, including in the background " +
        "worker. No restart is needed.",
    );
    if (!confirmed) return;
    await send(`/api/super-admin/llm-providers/${provider.id}/activate`, "POST",
      undefined, provider.id);
  }

  const ordered = [...providers].sort(
    (a, b) =>
      Number(b.is_active) - Number(a.is_active) ||
      a.provider_name.localeCompare(b.provider_name),
  );
  const active = ordered.find((p) => p.is_active);

  return (
    <div className="space-y-6">
      {error && (
        <p className="flex items-start gap-2 rounded-[20px] bg-error-container px-4 py-3 text-sm text-on-error-container">
          <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
          {error}
        </p>
      )}
      {notice && (
        <p className="flex items-start gap-2 rounded-[20px] bg-surface-container-high px-4 py-3 text-sm text-on-surface-variant">
          <Check size={16} className="mt-0.5 shrink-0" aria-hidden />
          {notice}
        </p>
      )}

      {!active && (
        <p className="flex items-start gap-2 rounded-[20px] bg-error-container px-4 py-3 text-sm text-on-error-container">
          <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
          <span>
            <strong>No provider is active.</strong> Chat and document enrichment
            are returning their fallbacks until one is activated.
          </span>
        </p>
      )}

      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-on-surface-variant">
          {ordered.length} registered
          {active
            ? ` · answering with ${active.provider_name}/${active.model_name}`
            : ""}
        </p>
        <Button size="sm" onClick={() => setAdding((open) => !open)}>
          <Plus size={14} aria-hidden /> Add provider
        </Button>
      </div>

      {adding && (
        <AddProviderForm
          busy={busy === "create"}
          onCancel={() => setAdding(false)}
          onSubmit={async (body) => {
            const ok = await send("/api/super-admin/llm-providers", "POST", body,
              "create");
            if (ok) setAdding(false);
          }}
        />
      )}

      <ul className="space-y-3">
        {ordered.map((provider) => (
          <ProviderRow
            key={provider.id}
            provider={provider}
            busy={busy === provider.id}
            onActivate={() => activate(provider)}
            onTest={() =>
              send(`/api/super-admin/llm-providers/${provider.id}/test`, "POST",
                undefined, provider.id)
            }
            onReplaceKey={(apiKey) =>
              send(`/api/super-admin/llm-providers/${provider.id}`, "PATCH",
                { api_key: apiKey }, provider.id)
            }
            onDelete={() =>
              send(`/api/super-admin/llm-providers/${provider.id}`, "DELETE",
                undefined, provider.id)
            }
          />
        ))}
      </ul>

      {ordered.length === 0 && !adding && (
        <div className="rounded-[32px] border border-outline-variant bg-surface-container-lowest px-8 py-16 text-center">
          <p className="font-medium text-on-surface">No providers registered</p>
          <p className="mt-1 text-sm text-on-surface-variant">
            Add one to give the agents a model to answer with.
          </p>
        </div>
      )}
    </div>
  );
}

function ProviderRow({
  provider,
  busy,
  onActivate,
  onTest,
  onReplaceKey,
  onDelete,
}: {
  provider: LLMProvider;
  busy: boolean;
  onActivate: () => void;
  onTest: () => void;
  onReplaceKey: (apiKey: string) => void;
  onDelete: () => void;
}) {
  const [replacing, setReplacing] = useState(false);
  const [draftKey, setDraftKey] = useState("");

  return (
    <li
      className={`rounded-[20px] border px-5 py-4 ${
        provider.is_active
          ? "border-primary bg-secondary-container"
          : "border-outline-variant bg-surface-container-lowest"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="flex items-center gap-2 font-medium text-on-surface">
            {provider.is_active ? (
              <CircleDot size={15} className="shrink-0 text-primary" aria-hidden />
            ) : (
              <CircleDot size={15} className="shrink-0 text-on-surface-variant/40" aria-hidden />
            )}
            {provider.provider_name}
            <span className="font-mono text-[13px] text-on-surface-variant">
              {provider.model_name}
            </span>
          </p>

          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-on-surface-variant">
            <span className="flex items-center gap-1">
              <KeyRound size={11} aria-hidden />
              {provider.key_fingerprint}
            </span>
            <span>v{provider.config_version}</span>
            {provider.base_url && <span className="truncate">{provider.base_url}</span>}
            <span>
              {provider.last_tested_at
                ? `tested ${new Date(provider.last_tested_at).toLocaleString()}`
                : "never tested"}
            </span>
          </p>

          <p className="mt-1 text-xs text-on-surface-variant">
            {provider.is_active
              ? "Answering now, for every organization."
              : "Registered and idle. Nothing is routed here."}
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Button size="sm" variant="ghost" disabled={busy} onClick={onTest}>
            Test
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => setReplacing((open) => !open)}
          >
            Replace key
          </Button>
          {!provider.is_active && (
            <>
              <Button size="sm" disabled={busy} onClick={onActivate}>
                <Zap size={14} aria-hidden /> Activate
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={onDelete}>
                <Trash2 size={14} aria-hidden />
                <span className="sr-only">Delete {provider.model_name}</span>
              </Button>
            </>
          )}
        </div>
      </div>

      {replacing && (
        <div className="mt-4 border-t border-outline-variant pt-4">
          <label
            htmlFor={`key-${provider.id}`}
            className="text-xs font-medium text-on-surface"
          >
            New API key
          </label>
          <div className="mt-1 flex flex-wrap gap-2">
            <input
              id={`key-${provider.id}`}
              type="password"
              value={draftKey}
              autoComplete="off"
              onChange={(event) => setDraftKey(event.target.value)}
              placeholder="Paste the replacement key"
              className="min-w-[240px] flex-1 rounded-full border border-outline-variant bg-surface-container-lowest px-5 py-2 font-mono text-[13px] text-on-surface outline-none focus:border-primary"
            />
            <Button
              size="sm"
              disabled={busy || draftKey.trim().length < 8}
              onClick={() => {
                onReplaceKey(draftKey.trim());
                setDraftKey("");
                setReplacing(false);
              }}
            >
              Verify and save
            </Button>
          </div>
          <p className="mt-2 text-xs text-on-surface-variant">
            The key is checked against the provider before it is stored. The one in
            use now stays in place if that check fails.
          </p>
        </div>
      )}
    </li>
  );
}

function AddProviderForm({
  busy,
  onCancel,
  onSubmit,
}: {
  busy: boolean;
  onCancel: () => void;
  onSubmit: (body: Record<string, unknown>) => void;
}) {
  const [providerName, setProviderName] = useState<LLMProviderName>("gemini");
  const [modelName, setModelName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");

  const chosen = PROVIDERS.find((p) => p.value === providerName)!;
  const ready = modelName.trim().length > 0 && apiKey.trim().length >= 8;

  return (
    <form
      className="space-y-4 rounded-[20px] border border-outline-variant bg-surface-container-lowest px-5 py-5"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({
          provider_name: providerName,
          model_name: modelName.trim(),
          api_key: apiKey.trim(),
          base_url: baseUrl.trim() || null,
        });
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm">
          <span className="font-medium text-on-surface">Provider</span>
          <select
            value={providerName}
            onChange={(event) =>
              setProviderName(event.target.value as LLMProviderName)
            }
            className="mt-1 w-full rounded-full border border-outline-variant bg-surface-container-lowest px-5 py-2 text-sm text-on-surface outline-none focus:border-primary"
          >
            {PROVIDERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="block text-sm">
          <span className="font-medium text-on-surface">Model</span>
          <input
            value={modelName}
            onChange={(event) => setModelName(event.target.value)}
            placeholder={chosen.example}
            className="mt-1 w-full rounded-full border border-outline-variant bg-surface-container-lowest px-5 py-2 font-mono text-[13px] text-on-surface outline-none focus:border-primary"
          />
        </label>
      </div>

      <label className="block text-sm">
        <span className="font-medium text-on-surface">API key</span>
        <input
          type="password"
          value={apiKey}
          autoComplete="off"
          onChange={(event) => setApiKey(event.target.value)}
          placeholder="Paste the key"
          className="mt-1 w-full rounded-full border border-outline-variant bg-surface-container-lowest px-5 py-2 font-mono text-[13px] text-on-surface outline-none focus:border-primary"
        />
      </label>

      <label className="block text-sm">
        <span className="font-medium text-on-surface">
          Endpoint <span className="font-normal text-on-surface-variant">(optional)</span>
        </span>
        <input
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          placeholder="Azure and self-hosted endpoints only"
          className="mt-1 w-full rounded-full border border-outline-variant bg-surface-container-lowest px-5 py-2 font-mono text-[13px] text-on-surface outline-none focus:border-primary"
        />
      </label>

      <p className="text-xs text-on-surface-variant">
        The key is verified with one real call before anything is stored, and the
        provider is saved <strong>idle</strong> — activate it separately when you
        want it answering. Enter the bare model id; the provider prefix is added
        for you.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" type="submit" disabled={busy || !ready}>
          Verify and register
        </Button>
        <Button size="sm" variant="ghost" type="button" disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
