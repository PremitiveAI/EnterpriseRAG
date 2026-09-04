import { proxy } from "@/lib/api";

export async function GET() {
  return proxy("/super-admin/agent-rules", { method: "GET" });
}
