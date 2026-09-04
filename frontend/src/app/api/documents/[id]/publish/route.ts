import { proxy } from "@/lib/api";

/**
 * Publish a document to the organization's public chatbot, or withdraw it.
 *
 * Its own route rather than a field on the PATCH handler, mirroring the
 * backend: publishing is the one edit that changes who can read a document, so
 * it is never something a caller can do as a side effect of saving a title.
 */
export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  return proxy(`/admin/documents/${id}/publish`, {
    method: "PATCH",
    body: await request.text(),
  });
}
