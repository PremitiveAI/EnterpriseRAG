import { proxy } from "@/lib/api";

type Params = { params: Promise<{ id: string }> };

/** One real call against a stored provider. Changes nothing but last_tested_at. */
export async function POST(_request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/super-admin/llm-providers/${id}/test`, { method: "POST" });
}
