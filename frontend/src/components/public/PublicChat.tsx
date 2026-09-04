"use client";

import { ArrowUp, FileText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Markdown } from "@/components/chat/Markdown";
import type { ApiResponse, PublicAnswer, PublicConfig } from "@/types/api";

interface Turn {
  role: "visitor" | "assistant";
  text: string;
  sources?: { document_name: string; page: number | null }[];
  grounded?: boolean;
}

/**
 * A visitor has no account, so `session_id` is the only thing tying their turns
 * together. It lives in sessionStorage rather than localStorage: closing the
 * tab should end the conversation, and nothing about a stranger's questions
 * needs to outlive it.
 */
function useSessionId(): string {
  const [id, setId] = useState("");

  useEffect(() => {
    const KEY = "erag_public_session";
    let existing: string | null = null;
    try {
      existing = sessionStorage.getItem(KEY);
    } catch {
      // Some embedding contexts deny storage entirely. A per-render id still
      // works; it just means the backend sees each reload as a new visitor.
    }
    const next = existing ?? crypto.randomUUID();
    if (!existing) {
      try {
        sessionStorage.setItem(KEY, next);
      } catch {
        /* not fatal - see above */
      }
    }
    setId(next);
  }, []);

  return id;
}

/**
 * The organization's public chatbot.
 *
 * Renders in two places with the same code: a browser tab, and a 400px panel
 * inside somebody else's website. `embed` is what separates them - the panel
 * already has a frame, a shadow and a close button around it, so repeating the
 * page's own chrome inside it wastes a third of the height a conversation has
 * to live in.
 */
export function PublicChat({
  organizationId,
  config,
  embed = false,
}: {
  organizationId: string;
  config: PublicConfig;
  embed?: boolean;
}) {
  const sessionId = useSessionId();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, sending]);

  async function send(message: string) {
    if (!message || sending || !sessionId) return;

    setTurns((current) => [...current, { role: "visitor", text: message }]);
    setDraft("");
    setSending(true);
    setError(null);

    try {
      const response = await fetch(`/api/public/${organizationId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: sessionId }),
      });
      const body = (await response.json()) as ApiResponse<PublicAnswer>;

      if (!body.success) {
        setError(body.message);
        return;
      }

      setTurns((current) => [
        ...current,
        {
          role: "assistant",
          text: body.data.answer,
          sources: body.data.sources,
          grounded: body.data.is_grounded,
        },
      ]);
    } catch {
      setError("Could not reach the assistant. Please try again.");
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }

  return (
    <main
      className={`flex h-dvh flex-col bg-surface ${
        embed ? "" : "mx-auto max-w-3xl border-x border-outline-variant"
      }`}
    >
      <Header config={config} compact={embed} />

      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-5 sm:px-5">
        <Bubble role="assistant">
          <Markdown>{config.greeting}</Markdown>
        </Bubble>

        {turns.length === 0 && !sending && (
          <p className="px-1 pt-1 text-xs text-on-surface-variant">
            Answers are drawn only from documents {config.organization_name} has
            published. If something is not in them, the assistant will say so rather
            than guess.
          </p>
        )}

        {turns.map((turn, index) => (
          <Bubble key={index} role={turn.role}>
            {turn.role === "assistant" ? (
              <>
                <Markdown>{stripCitationMarkers(turn.text)}</Markdown>
                {turn.sources && turn.sources.length > 0 && <Sources items={turn.sources} />}
              </>
            ) : (
              <p className="whitespace-pre-wrap text-sm leading-relaxed">{turn.text}</p>
            )}
          </Bubble>
        ))}

        {sending && <Thinking />}

        {error && (
          <p className="rounded-[14px] bg-error-container px-4 py-3 text-sm text-on-error-container">
            {error}
          </p>
        )}

        <div ref={endRef} />
      </div>

      <Composer
        ref={inputRef}
        value={draft}
        onChange={setDraft}
        onSend={() => send(draft.trim())}
        disabled={sending}
      />
    </main>
  );
}

// --- Pieces ---------------------------------------------------------------- //

function Header({ config, compact }: { config: PublicConfig; compact: boolean }) {
  return (
    <header className="flex shrink-0 items-center gap-3 border-b border-outline-variant bg-surface-container-lowest px-4 py-3 sm:px-5">
      <span
        className={`flex shrink-0 items-center justify-center rounded-full bg-primary font-semibold text-on-primary ${
          compact ? "h-9 w-9 text-sm" : "h-10 w-10"
        }`}
        aria-hidden
      >
        {config.organization_name.slice(0, 1).toUpperCase()}
      </span>
      <div className="min-w-0">
        <h1 className="truncate text-[15px] font-semibold leading-tight text-on-surface">
          {config.organization_name}
        </h1>
        <p className="flex items-center gap-1.5 truncate text-xs leading-tight text-on-surface-variant">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[--color-status-completed-fg]" aria-hidden />
          Answers from published documents
        </p>
      </div>
    </header>
  );
}

/**
 * Radii are literal pixels here, not `rounded-2xl`.
 *
 * ADR-008 sets EVERY radius token to 9999px ("fully rounded everywhere"), which
 * is right for buttons and chips and wrong for a paragraph of text: a
 * three-line answer came out as a lozenge with its first and last words curving
 * away from the edge. The tokens are deliberately not changed - the rest of the
 * app depends on them - so this one surface opts out.
 *
 * The corner nearest each speaker's own side is flattened. That is what makes a
 * column of bubbles read as a conversation with two sides to it.
 */
function Bubble({ role, children }: { role: Turn["role"]; children: React.ReactNode }) {
  const visitor = role === "visitor";
  return (
    <div className={`flex ${visitor ? "justify-end" : "justify-start"}`}>
      <div
        className={
          visitor
            ? "max-w-[85%] rounded-[18px] rounded-br-[6px] bg-primary px-4 py-2.5 text-on-primary shadow-sm"
            : "max-w-[92%] rounded-[18px] rounded-bl-[6px] border border-outline-variant bg-surface-container-lowest px-4 py-3 text-on-surface"
        }
      >
        {children}
      </div>
    </div>
  );
}

/**
 * Citation markers are stripped from the visitor's copy of the answer.
 *
 * Not cosmetic. The composer numbers its markers by PASSAGE - `[1]`..`[K]` over
 * everything retrieved - while the sources list underneath is rebuilt from only
 * the passages actually cited and re-ranked from 1. So an answer that cites the
 * second passage renders "[2]" above a list containing exactly one file. The
 * number points at nothing the reader can see.
 *
 * A signed-in admin has a sources panel where the numbering can be reconciled;
 * a website visitor has a file list and no way to interpret a stray "[2]", so
 * for this surface the marker is removed and the file list carries the
 * attribution on its own. The stored message keeps its markers - this changes
 * what is displayed, never what was recorded.
 *
 * Only bare numeric brackets go: `[1]`, `[1, 2]`, `[1-3]`. A Markdown link's
 * `[label](url)` is protected by the lookahead, and any other bracketed text is
 * left alone.
 */
const CITATION_MARKER = /\s?\[\d+(?:\s*[,–-]\s*\d+)*\](?!\()/g;

export function stripCitationMarkers(text: string): string {
  return text.replace(CITATION_MARKER, "");
}

interface SourceFile {
  name: string;
  pages: number[];
}

/**
 * One row per FILE, not per passage.
 *
 * Retrieval returns chunks, and three chunks out of the same PDF arrived here
 * as three identical-looking chips that differed only in a page number. That
 * reads as three documents. Grouping by filename and collecting the pages says
 * the true thing - one document, consulted in three places - in a third of the
 * space, which matters in a 400px panel.
 */
export function groupByFile(
  items: { document_name: string; page: number | null }[],
): SourceFile[] {
  const files = new Map<string, SourceFile>();

  for (const item of items) {
    const existing = files.get(item.document_name);
    const file = existing ?? { name: item.document_name, pages: [] };
    if (item.page !== null && !file.pages.includes(item.page)) file.pages.push(item.page);
    if (!existing) files.set(item.document_name, file);
  }

  // Insertion order is retrieval order, i.e. best match first — worth keeping.
  return [...files.values()].map((file) => ({
    ...file,
    pages: [...file.pages].sort((a, b) => a - b),
  }));
}

/** "p3", "p3, 7", "p3, 7 +2 more" — a page list must not wrap the row. */
export function describePages(pages: number[]): string | null {
  if (pages.length === 0) return null;
  const shown = pages.slice(0, 2).join(", ");
  const rest = pages.length - 2;
  return rest > 0 ? `p${shown} +${rest} more` : `p${shown}`;
}

/**
 * The filename, kept readable when it is too long for the panel.
 *
 * Plain truncation eats the extension, and ".jpg" vs ".pdf" is often the fastest
 * way to recognise a file, so the tail is always kept.
 */
export function shortenName(name: string, limit = 34): string {
  if (name.length <= limit) return name;
  const dot = name.lastIndexOf(".");
  const extension = dot > 0 && name.length - dot <= 6 ? name.slice(dot) : "";
  const stem = extension ? name.slice(0, dot) : name;
  const keep = Math.max(8, limit - extension.length - 1);
  return `${stem.slice(0, keep)}…${extension}`;
}

function Sources({ items }: { items: { document_name: string; page: number | null }[] }) {
  const files = groupByFile(items);

  return (
    <div className="mt-3 border-t border-outline-variant pt-2.5">
      <p className="mb-2 text-[11px] font-medium uppercase tracking-wider text-on-surface-variant">
        {files.length === 1 ? "Source" : `Sources · ${files.length} files`}
      </p>
      <ul className="space-y-1">
        {files.map((file) => {
          const pages = describePages(file.pages);
          return (
            <li
              key={file.name}
              // Not a pill: a filename is left-aligned text, and the token
              // radius would curve its first character away from the icon.
              className="flex items-center gap-2 rounded-[10px] bg-surface-container-high px-2.5 py-1.5"
            >
              <FileText size={12} className="shrink-0 text-on-surface-variant" aria-hidden />
              <span
                className="min-w-0 flex-1 truncate text-[11px] text-on-surface"
                title={file.name}
              >
                {shortenName(file.name)}
              </span>
              {pages && (
                <span className="shrink-0 font-mono text-[10px] text-on-surface-variant">
                  {pages}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Thinking() {
  return (
    <div className="flex justify-start">
      <div className="flex items-center gap-1.5 rounded-[18px] rounded-bl-[6px] border border-outline-variant bg-surface-container-lowest px-4 py-3.5">
        <span className="sr-only">Searching the documents…</span>
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="h-1.5 w-1.5 animate-bounce rounded-full bg-on-surface-variant"
            style={{ animationDelay: `${index * 0.15}s` }}
            aria-hidden
          />
        ))}
      </div>
    </div>
  );
}

function Composer({
  ref,
  value,
  onChange,
  onSend,
  disabled,
}: {
  ref: React.RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (next: string) => void;
  onSend: () => void;
  disabled: boolean;
}) {
  // Grows with the question instead of scrolling a one-line box. Reset to auto
  // first, or the height only ever ratchets upward as text is deleted.
  useEffect(() => {
    const field = ref.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, 120)}px`;
  }, [value, ref]);

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSend();
      }}
      className="shrink-0 border-t border-outline-variant bg-surface-container-lowest px-4 py-3 sm:px-5"
    >
      <div className="flex items-end gap-2 rounded-3xl border border-outline-variant bg-surface-container-low px-4 py-2 focus-within:border-primary">
        <textarea
          ref={ref}
          rows={1}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter breaks the line - what every chat does,
            // and the reason the field is a textarea rather than an input.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSend();
            }
          }}
          placeholder="Ask a question…"
          aria-label="Your question"
          maxLength={1000}
          disabled={disabled}
          className="max-h-[120px] flex-1 resize-none bg-transparent py-1.5 text-sm leading-relaxed text-on-surface outline-none placeholder:text-on-surface-variant disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={disabled || !value.trim()}
          aria-label="Send"
          className="mb-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-on-primary transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ArrowUp size={16} aria-hidden />
        </button>
      </div>
    </form>
  );
}
