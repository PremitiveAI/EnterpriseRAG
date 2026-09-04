"use client";

import { AlertCircle, Bot, Check, Lock, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { apiFetch } from "@/lib/client-fetch";
import type { AgentRuleDetail, AgentRuleSummary } from "@/types/api";

/**
 * The Super Admin's prompt editor.
 *
 * Two things on this screen are deliberate and easy to "improve" into being
 * wrong:
 *
 * - **The locked text is shown, not hidden.** It is what the backend appends to
 *   every prompt regardless of what is typed here. A Super Admin who cannot see
 *   the part they may not change will write their own output contract and then
 *   wonder why nothing takes effect.
 * - **There is no delete.** Clearing the box restores the built-in prompt,
 *   which is the only thing a delete would have done.
 */
export function AgentRulesScreen({ agents }: { agents: AgentRuleSummary[] }) {
  const [selected, setSelected] = useState(agents[0]?.agent_key ?? "");
  const [detail, setDetail] = useState<AgentRuleDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!selected) return;
    setLoading(true);
    setError(null);
    setNotice(null);

    const body = await readJson(`/api/super-admin/agent-rules/${selected}`);
    if (body?.success) {
      const data = body.data as AgentRuleDetail;
      setDetail(data);
      setDraft(data.content);
    } else {
      setDetail(null);
      setError(body?.message ?? "Could not load this agent's rule.");
    }
    setLoading(false);
  }, [selected]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(content: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await apiFetch(`/api/super-admin/agent-rules/${selected}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.success) {
        setError(payload.message ?? "Could not save the rule.");
        return;
      }
      setDetail(payload.data);
      setDraft(payload.data.content);
      setNotice(payload.message);
    } catch {
      setError("Could not reach the server.");
    } finally {
      setBusy(false);
    }
  }

  const dirty = detail !== null && draft !== detail.content;

  return (
    <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
      <nav aria-label="Agents" className="space-y-2">
        {agents.map((agent) => {
          const active = agent.agent_key === selected;
          return (
            <button
              key={agent.agent_key}
              onClick={() => setSelected(agent.agent_key)}
              aria-current={active ? "true" : undefined}
              className={`w-full rounded-[20px] border px-4 py-3 text-left transition-colors ${
                active
                  ? "border-primary bg-secondary-container"
                  : "border-outline-variant hover:bg-surface-container-low"
              }`}
            >
              <span className="flex items-center gap-2">
                <Bot size={15} className="shrink-0 text-on-surface-variant" aria-hidden />
                <span className="font-medium text-on-surface">{agent.name}</span>
              </span>
              <span className="mt-1 block text-xs leading-snug text-on-surface-variant">
                {agent.role}
              </span>
              <span className="mt-2 block font-mono text-[10px] uppercase tracking-wider text-on-surface-variant">
                {agent.is_custom ? "custom rule" : "built-in prompt"}
              </span>
            </button>
          );
        })}
      </nav>

      <div className="space-y-4">
        {error && (
          <p className="flex items-start gap-2 rounded-[14px] bg-error-container px-4 py-3 text-sm text-on-error-container">
            <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
            {error}
          </p>
        )}
        {notice && (
          <p className="flex items-start gap-2 rounded-[14px] bg-surface-container-high px-4 py-3 text-sm text-on-surface-variant">
            <Check size={16} className="mt-0.5 shrink-0" aria-hidden />
            {notice}
          </p>
        )}

        {loading && !detail && (
          <div className="h-64 animate-pulse rounded-[20px] bg-surface-container-high" />
        )}

        {detail && (
          <>
            <section className="space-y-2">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <label
                  htmlFor="rule"
                  className="text-sm font-semibold text-on-surface"
                >
                  Instructions for {detail.name}
                </label>
                <span className="font-mono text-[11px] text-on-surface-variant">
                  {draft.length.toLocaleString()} characters
                </span>
              </div>

              <textarea
                id="rule"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                rows={14}
                spellCheck={false}
                disabled={busy}
                placeholder={detail.default_content}
                className="w-full resize-y rounded-[20px] border border-outline-variant bg-surface-container-lowest px-5 py-4 font-mono text-[13px] leading-relaxed text-on-surface outline-none placeholder:text-on-surface-variant/60 focus:border-primary disabled:opacity-60"
              />

              <p className="px-2 text-xs text-on-surface-variant">
                {detail.is_custom
                  ? "A custom rule is in effect. Clear the box and save to go back to the built-in prompt."
                  : "Empty, so the built-in prompt shown in grey is running. Type to replace it."}{" "}
                Changes apply to the next chat message — no restart.
              </p>
            </section>

            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" disabled={busy || !dirty} onClick={() => save(draft)}>
                Save rule
              </Button>
              {dirty && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => setDraft(detail.content)}
                >
                  Discard changes
                </Button>
              )}
              {detail.is_custom && (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy}
                  onClick={() => save("")}
                  className="ml-auto"
                >
                  <RotateCcw size={14} aria-hidden /> Reset to built-in
                </Button>
              )}
            </div>

            <LockedText text={detail.locked_text} />
          </>
        )}
      </div>
    </div>
  );
}

/**
 * The part no rule can reach.
 *
 * Not decoration. The grounding rules are what keep an answer tied to the
 * documents, and the JSON contract is what every caller parses. Editing either
 * away would fail silently — a 200, a plausible answer, and nothing in the log
 * — so they live in code and are shown here only so the boundary is visible.
 */
function LockedText({ text }: { text: string }) {
  if (!text) return null;

  return (
    <section className="space-y-2 border-t border-outline-variant pt-4">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-on-surface">
        <Lock size={14} className="shrink-0" aria-hidden />
        Always appended — not editable
      </h2>
      <p className="text-xs text-on-surface-variant">
        The backend adds this after your instructions on every call. It keeps answers
        grounded in the retrieved passages and keeps the response machine-readable.
        Writing your own version above will not replace it.
      </p>
      <pre className="overflow-x-auto rounded-[20px] bg-surface-container-high px-5 py-4 font-mono text-[11px] leading-relaxed text-on-surface-variant">
        {text}
      </pre>
    </section>
  );
}

/** Parses defensively: an HTML error page must not take the screen down. */
async function readJson(
  url: string,
): Promise<{ success: boolean; data?: unknown; message?: string } | null> {
  try {
    const response = await apiFetch(url);
    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch {
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
