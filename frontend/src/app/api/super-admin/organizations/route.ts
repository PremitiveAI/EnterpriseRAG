/** Organization list and create. Super Admin only - the backend re-checks. */
import { proxy } from "@/lib/api";

export async function GET(request: Request) {
  const { search } = new URL(request.url);
  return proxy(`/super-admin/organizations${search}`, { method: "GET" });
}

export async function POST(request: Request) {
  return proxy("/super-admin/organizations", {
    method: "POST",
    body: await request.text(),
  });
}
