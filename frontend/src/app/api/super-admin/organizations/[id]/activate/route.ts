import { proxy } from "@/lib/api";

export async function POST(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxy(`/super-admin/organizations/${id}/activate`, { method: "POST" });
}
