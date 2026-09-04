/** Detail, metadata edit and soft delete for one document. */
import { proxy } from "@/lib/api";

type Params = { params: Promise<{ id: string }> };

export async function GET(_request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/admin/documents/${id}`, { method: "GET" });
}

export async function PATCH(request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/admin/documents/${id}`, {
    method: "PATCH",
    body: await request.text(),
  });
}

export async function DELETE(_request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/admin/documents/${id}`, { method: "DELETE" });
}
