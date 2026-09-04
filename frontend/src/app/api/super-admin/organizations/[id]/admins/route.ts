import { proxy } from "@/lib/api";

type Params = { params: Promise<{ id: string }> };

export async function GET(_request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/super-admin/organizations/${id}/admins`, { method: "GET" });
}

export async function POST(request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/super-admin/organizations/${id}/admins`, {
    method: "POST",
    body: await request.text(),
  });
}
