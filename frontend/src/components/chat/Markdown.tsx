"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Renders an assistant answer.
 *
 * The answer is Markdown chosen by the composer to fit the question — a
 * sentence, a paragraph, a list, a table or a code block. This renders all of
 * them without the page having to know which arrived.
 *
 * **This is untrusted input.** The text is written by a model that has just
 * read admin-uploaded documents, so a document could try to smuggle markup
 * through it. Two deliberate omissions guard that:
 *
 * - `rehype-raw` is NOT used, so raw HTML in the answer is escaped and shown as
 *   text rather than parsed. Adding it would make a document able to inject
 *   markup into this origin.
 * - Link hrefs are filtered to http/https/mailto, so `javascript:` cannot
 *   survive even if a future dependency stops filtering it.
 */

const SAFE_PROTOCOLS = ["http:", "https:", "mailto:"];

function safeHref(url: string): string {
  try {
    const parsed = new URL(url, "https://example.invalid");
    return SAFE_PROTOCOLS.includes(parsed.protocol) ? url : "";
  } catch {
    return "";
  }
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeHref}
        components={{
          p: ({ children }) => <p className="my-3">{children}</p>,

          ul: ({ children }) => (
            <ul className="my-3 list-disc space-y-1.5 pl-5">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="my-3 list-decimal space-y-1.5 pl-5">{children}</ol>
          ),
          li: ({ children }) => <li className="pl-1">{children}</li>,

          h1: ({ children }) => (
            <h3 className="mb-2 mt-4 text-base font-semibold text-on-surface">{children}</h3>
          ),
          h2: ({ children }) => (
            <h3 className="mb-2 mt-4 text-base font-semibold text-on-surface">{children}</h3>
          ),
          h3: ({ children }) => (
            <h4 className="mb-2 mt-4 text-sm font-semibold text-on-surface">{children}</h4>
          ),

          // A wide table must scroll inside its own box rather than stretching
          // the 800px message column.
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto rounded-[16px] border border-outline-variant">
              <table className="w-full border-collapse text-left text-[13px]">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="border-b border-outline-variant bg-surface-container-high">
              {children}
            </thead>
          ),
          th: ({ children }) => (
            <th className="px-4 py-2.5 font-semibold text-on-surface">{children}</th>
          ),
          td: ({ children }) => (
            <td className="border-t border-outline-variant/60 px-4 py-2.5 align-top">
              {children}
            </td>
          ),

          code: ({ className, children, ...rest }) => {
            // react-markdown gives inline code no language class; a fenced
            // block gets `language-*`. That is the only reliable signal.
            const fenced = /language-(\w+)/.test(className ?? "");
            if (!fenced) {
              return (
                <code
                  className="rounded bg-surface-container-high px-1.5 py-0.5 font-mono text-[12px]"
                  {...rest}
                >
                  {children}
                </code>
              );
            }
            return (
              <code className="font-mono text-[12px] leading-relaxed" {...rest}>
                {children}
              </code>
            );
          },
          pre: ({ children }) => (
            <pre className="my-3 overflow-x-auto rounded-[16px] border border-outline-variant bg-surface-container-high p-4">
              {children}
            </pre>
          ),

          blockquote: ({ children }) => (
            <blockquote className="my-3 border-l-2 border-outline-variant pl-4 text-on-surface-variant">
              {children}
            </blockquote>
          ),

          a: ({ href, children }) =>
            href ? (
              <a
                href={href}
                target="_blank"
                // noopener stops the opened page reaching back via window.opener.
                rel="noopener noreferrer"
                className="text-primary underline underline-offset-2"
              >
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),

          hr: () => <hr className="my-4 border-outline-variant" />,
          strong: ({ children }) => (
            <strong className="font-semibold text-on-surface">{children}</strong>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
