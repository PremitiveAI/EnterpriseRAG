"use client";

import { AlertCircle, FileText, Loader2, SendHorizontal, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Markdown } from "@/components/chat/Markdown";
import { apiFetch } from "@/lib/client-fetch";
import type { AnswerResponse, ChatMessage, SourceRef } from "@/types/api";

const MAX_CHARS = 4000;

type Pending = { question: string } | null;

export function ChatPanel({
  conversationId,
  initialMessages,
}: {
  conversationId: string;
  initialMessages: ChatMessage[];
}) {
  const router = useRouter();
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(null);
  /**
   * The last answer was produced on a degraded path — an agent fell back, or
   * the provider configuration could not be re-confirmed (ADR-010 §5). Not an
   * error: the answer is real and grounded. Worth saying, because "quietly
   * worse" is exactly how a broken agent used to look like a working one.
   */
  const [degraded, setDegraded] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setMessages(initialMessages);
  }, [initialMessages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);

  // Auto-grow, capped so a pasted essay does not swallow the transcript.
  useEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 200)}px`;
  }, [draft]);

  async function send() {
    const question = draft.trim();
    if (!question || pending) return;

    setDraft("");
    setError(null);
    // Optimistic: the question appears immediately, the answer replaces the
    // thinking state when it lands.
    setPending({ question });

    try {
      const response = await apiFetch(`/api/chat/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: question }),
      });
      const body = await response.json();

      if (!body.success) {
        setError(body.message ?? "Something went wrong.");
        setDraft(question);
        setPending(null);
        return;
      }

      const answer = body.data as AnswerResponse;
      setPending(null);
      setDegraded(answer.degraded);
      // Refresh rather than splice: the server holds the canonical transcript,
      // including the persisted user message and its id.
      router.refresh();
      setMessages((current) => [
        ...current,
        {
          id: `local-${answer.message_id}-q`,
          role: "user",
          content: question,
          is_grounded: null,
          error_code: null,
          latency_ms: null,
          created_at: new Date().toISOString(),
          sources: [],
        },
        {
          id: answer.message_id,
          role: "assistant",
          content: answer.answer,
          is_grounded: answer.is_grounded,
          error_code: answer.error_code,
          latency_ms: answer.latency_ms,
          created_at: new Date().toISOString(),
          sources: answer.sources,
        },
      ]);
    } catch {
      setError("Could not reach the server.");
      setDraft(question);
      setPending(null);
    }
  }

  const empty = messages.length === 0 && !pending;

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto px-4 py-8">
        <div className="mx-auto flex w-full max-w-[800px] flex-col gap-6">
          {empty && (
            <div className="mt-24 text-center">
              <Sparkles size={30} className="mx-auto text-primary" aria-hidden />
              <h2 className="mt-4 text-2xl font-semibold text-on-background">
                How can I help you today?
              </h2>
              <p className="mt-2 text-on-surface-variant">
                Ask anything about the documents in the knowledge base.
              </p>
            </div>
          )}

          {messages.map((message) => (
            <Bubble key={message.id} message={message} />
          ))}

          {pending && (
            <>
              <UserBubble content={pending.question} />
              <div className="flex items-center gap-2 text-sm text-on-surface-variant">
                <Loader2 size={15} className="animate-spin" aria-hidden />
                Searching the documents…
              </div>
            </>
          )}

          {degraded && !error && (
            <p className="flex items-start gap-2 rounded-[20px] bg-surface-container-high px-4 py-3 text-sm text-on-surface-variant">
              <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
              Answered on a reduced path — the reply is grounded in your documents,
              but part of the AI pipeline fell back. Recent answers may be shorter
              or less well phrased than usual.
            </p>
          )}

          {error && (
            <p className="flex items-start gap-2 rounded-[20px] bg-error-container px-4 py-3 text-sm text-on-error-container">
              <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
              {error}
            </p>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      <div className="border-t border-outline-variant bg-surface-container-lowest px-4 py-4">
        <div className="mx-auto flex w-full max-w-[800px] items-end gap-2">
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value.slice(0, MAX_CHARS))}
            onKeyDown={(event) => {
              // Enter sends; Shift+Enter is a newline.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void send();
              }
            }}
            rows={1}
            placeholder="Ask a question about your documents…"
            aria-label="Message"
            className="max-h-[200px] flex-1 resize-none rounded-[24px] border border-outline-variant bg-surface-container-lowest px-6 py-3 text-sm text-on-surface outline-none placeholder:text-on-surface-variant focus:border-primary"
          />
          <button
            onClick={() => void send()}
            disabled={!draft.trim() || pending !== null}
            aria-label="Send"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-primary text-on-primary transition-colors hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-40"
          >
            {pending ? (
              <Loader2 size={17} className="animate-spin" aria-hidden />
            ) : (
              <SendHorizontal size={17} aria-hidden />
            )}
          </button>
        </div>
        <p className="mx-auto mt-2 w-full max-w-[800px] text-center text-[11px] text-on-surface-variant">
          Answers come only from your indexed documents.
        </p>
      </div>
    </div>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  if (message.role === "user") return <UserBubble content={message.content} />;

  // A refusal is a correct answer, so it is styled as an answer — muted, not
  // as an error. Confusing the two teaches the admin to distrust both.
  const refused = message.is_grounded === false;

  return (
    <div className="space-y-3">
      <div
        className={`rounded-[24px] px-6 py-4 ${
          refused
            ? "bg-surface-container-high text-on-surface-variant"
            : "bg-surface-container-lowest text-on-surface border border-outline-variant"
        }`}
      >
        {/* The composer picks the shape — a sentence, prose, a list, a table or
            a code block — so the answer is rendered as Markdown rather than as
            preformatted text. A refusal is one plain sentence either way. */}
        <Markdown>{message.content}</Markdown>
      </div>

      {message.sources.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {message.sources.map((source) => (
            <SourceChip key={`${source.document_id}-${source.rank}`} source={source} />
          ))}
        </div>
      )}
    </div>
  );
}

function UserBubble({ content }: { content: string }) {
  return (
    <div className="self-end whitespace-pre-wrap rounded-[24px] bg-primary px-6 py-3 text-sm text-on-primary">
      {content}
    </div>
  );
}

function SourceChip({ source }: { source: SourceRef }) {
  const label = (
    <>
      <FileText size={13} aria-hidden />
      <span className="max-w-[220px] truncate">{source.document_name}</span>
      {source.page != null && (
        <span className="font-mono text-[10px] opacity-70">Page {source.page}</span>
      )}
    </>
  );

  const className =
    "inline-flex items-center gap-1.5 rounded-full border border-outline-variant px-4 py-1.5 text-xs transition-colors";

  // A citation on a deleted document still resolves; the chip is greyed rather
  // than linking somewhere that would 404.
  if (source.document_deleted) {
    return (
      <span
        className={`${className} text-on-surface-variant opacity-60`}
        title="This document has been deleted"
      >
        {label}
      </span>
    );
  }

  return (
    <a
      href={`/documents?search=${encodeURIComponent(source.document_name)}`}
      className={`${className} text-on-surface hover:bg-surface-container-low`}
    >
      {label}
    </a>
  );
}
