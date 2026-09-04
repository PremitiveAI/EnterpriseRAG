/** List proxy. Query parameters pass straight through — the backend owns the
 * filter and sort whitelists, so duplicating them here could only drift. */
import { proxy } from "@/lib/api";

export async function GET(request: Request) {
  const { search } = new URL(request.url);
  return proxy(`/admin/documents${search}`, { method: "GET" });
}
