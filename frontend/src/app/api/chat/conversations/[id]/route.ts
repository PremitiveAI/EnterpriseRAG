/** Detail, rename and soft delete for one conversation. */
import { proxy } from "@/lib/api";

type Params = { params: Promise<{ id: string }> };

export async function GET(_request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/chat/conversations/${id}`, { method: "GET" });
}

export async function PATCH(request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/chat/conversations/${id}`, { method: "PATCH", body: await request.text() });
}

export async function DELETE(_request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/chat/conversations/${id}`, { method: "DELETE" });
}
