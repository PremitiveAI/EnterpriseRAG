import { proxy } from "@/lib/api";

type Params = { params: Promise<{ id: string }> };

export async function PATCH(request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/super-admin/categories/${id}`, {
    method: "PATCH",
    body: await request.text(),
  });
}

export async function DELETE(_request: Request, { params }: Params) {
  const { id } = await params;
  // No `force` here, unlike organizations: the backend already downgrades a
  // delete to a deactivation when documents still carry the category, so there
  // is nothing for the caller to override.
  return proxy(`/super-admin/categories/${id}`, { method: "DELETE" });
}
