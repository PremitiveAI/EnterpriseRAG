/**
 * Ask a question.
 *
 * Agents 2 and 3 both sit on this path, so the upstream call can take a minute:
 * a CrewAI round-trip to Gemini costs ~15s on its own, and there are two of
 * them plus embedding and retrieval. A grounded refusal comes back as HTTP 200
 * with is_grounded false — a correct answer, not an error.
 */
import { proxy } from "@/lib/api";

// Must exceed the backend's own budget (AGENT_PLAN_TIMEOUT_SECONDS +
// AGENT_COMPOSE_TIMEOUT_SECONDS plus retrieval), or this layer gives up while
// the backend is still working and the answer is lost after being paid for.
const CHAT_TIMEOUT_MS = 150_000;

export const maxDuration = 180;

export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return proxy(
    `/chat/conversations/${id}/messages`,
    { method: "POST", body: await request.text() },
    CHAT_TIMEOUT_MS,
  );
}
