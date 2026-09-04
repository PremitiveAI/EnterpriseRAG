import { proxy } from "@/lib/api";

export async function GET() {
  return proxy("/super-admin/llm-providers", { method: "GET" });
}

export async function POST(request: Request) {
  // The credential travels through here on its way in and never on its way
  // back: the backend's response model has no field for it.
  return proxy("/super-admin/llm-providers", {
    method: "POST",
    body: await request.text(),
  });
}
