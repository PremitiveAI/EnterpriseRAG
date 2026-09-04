/** Create and list conversations. */
import { proxy } from "@/lib/api";

export async function GET(request: Request) {
  const { search } = new URL(request.url);
  return proxy(`/chat/conversations${search}`, { method: "GET" });
}

export async function POST(request: Request) {
  return proxy("/chat/conversations", { method: "POST", body: await request.text() });
}
