import { proxy } from "@/lib/api";

type Params = { params: Promise<{ id: string }> };

/**
 * Separate from POST /llm-providers on purpose. Registering a provider and
 * pointing every tenant's traffic at it must not be the same gesture.
 */
export async function POST(_request: Request, { params }: Params) {
  const { id } = await params;
  return proxy(`/super-admin/llm-providers/${id}/activate`, { method: "POST" });
}
