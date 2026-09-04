import { proxy } from "@/lib/api";

type Params = { params: Promise<{ id: string }> };

export async function GET(_request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/super-admin/organizations/${id}`, { method: "GET" });
}

export async function PATCH(request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/super-admin/organizations/${id}`, {
    method: "PATCH",
    body: await request.text(),
  });
}

export async function DELETE(request: Request, { params }: Params) {
  const { id } = await params;
  const force = new URL(request.url).searchParams.get("force") === "true";
  return proxy(`/super-admin/organizations/${id}?force=${force}`, { method: "DELETE" });
}
