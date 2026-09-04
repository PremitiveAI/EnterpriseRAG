import { proxy } from "@/lib/api";

type Params = { params: Promise<{ id: string }> };

export async function PATCH(request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/super-admin/llm-providers/${id}`, {
    method: "PATCH",
    body: await request.text(),
  });
}

export async function DELETE(_request: Request, { params }: Params) {
  const { id } = await params;
  // The backend refuses to delete the active provider with a 409 rather than
  // silently deactivating it — there is nothing for the caller to override.
  return proxy(`/super-admin/llm-providers/${id}`, { method: "DELETE" });
}
