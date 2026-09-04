import { notFound } from "next/navigation";
import { PublicChat } from "@/components/public/PublicChat";
import { API } from "@/lib/session";
import type { ApiResponse, PublicConfig } from "@/types/api";

export const dynamic = "force-dynamic";

/**
 * The organization's public chatbot. No sign-in, no session cookie, no token.
 *
 * The organization id comes from the URL, which is the one place in the whole
 * application where that is allowed. The backend pays for it by answering only
 * from documents the organization has published.
 */
async function loadConfig(organizationId: string): Promise<PublicConfig | null> {
  try {
    const response = await fetch(`${API}/public/${organizationId}/config`, {
      cache: "no-store",
    });
    const body = (await response.json()) as ApiResponse<PublicConfig>;
    return body.success ? body.data : null;
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ organizationId: string }>;
}) {
  const config = await loadConfig((await params).organizationId);
  return { title: config ? `Ask ${config.organization_name}` : "Chat" };
}

export default async function PublicChatPage({
  params,
  searchParams,
}: {
  params: Promise<{ organizationId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { organizationId } = await params;
  // Set by widget.js. The panel supplies its own frame and close button, so the
  // page drops the chrome that would otherwise be drawn twice.
  const embed = (await searchParams).embed === "1";
  const config = await loadConfig(organizationId);

  // The backend answers an identical 404 for "no such organization", "it is
  // suspended" and "its chatbot is switched off" - so that a stranger cannot
  // use this page to discover which organizations exist. Rendering anything
  // more specific here would undo that.
  if (!config) notFound();

  return <PublicChat organizationId={organizationId} config={config} embed={embed} />;
}
