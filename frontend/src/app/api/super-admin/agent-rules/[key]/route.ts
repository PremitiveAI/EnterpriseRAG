import { proxy } from "@/lib/api";

type Params = { params: Promise<{ key: string }> };

export async function GET(_request: Request, { params }: Params) {
  const { key } = await params;
  return proxy(`/super-admin/agent-rules/${key}`, { method: "GET" });
}

export async function PUT(request: Request, { params }: Params) {
  const { key } = await params;
  return proxy(`/super-admin/agent-rules/${key}`, {
    method: "PUT",
    body: await request.text(),
  });
}

// No DELETE, mirroring the backend. Saving empty content restores the built-in
// prompt, which is the only thing a delete would have done — so a delete route
// would be a second path to the same state and a second thing to authorize.
