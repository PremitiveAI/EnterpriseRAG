/**
 * Streams the original file through the BFF.
 *
 * The body is piped rather than buffered: a 20 MB document would otherwise be
 * held in the Node process in full. The browser never sees the backend token,
 * and never constructs a storage path — it only knows the document id.
 */
import { API, authHeaders } from "@/lib/session";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let headers: Record<string, string>;
  try {
    headers = await authHeaders();
  } catch {
    return Response.json(
      { success: false, error_code: "UNAUTHORIZED", message: "Not signed in." },
      { status: 401 },
    );
  }

  const upstream = await fetch(`${API}/admin/documents/${id}/download`, {
    headers,
    cache: "no-store",
  });

  if (!upstream.ok) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "application/octet-stream",
      // Carried through verbatim: the backend already sanitised the filename
      // and chose `attachment`, so an uploaded HTML file cannot execute here.
      "Content-Disposition":
        upstream.headers.get("content-disposition") ?? `attachment; filename="document"`,
      "X-Content-Type-Options": "nosniff",
    },
  });
}
