/** Polling target. Small by design — it is fetched every two seconds (§44). */
import { proxy } from "@/lib/api";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxy(`/admin/documents/${id}/status`, { method: "GET" });
}
