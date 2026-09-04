import { proxy } from "@/lib/api";

export async function GET() {
  return proxy("/super-admin/categories", { method: "GET" });
}

export async function POST(request: Request) {
  return proxy("/super-admin/categories", {
    method: "POST",
    body: await request.text(),
  });
}
