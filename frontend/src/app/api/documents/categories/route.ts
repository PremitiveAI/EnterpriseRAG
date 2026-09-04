/** Filter options: categories, statuses, document types, languages. */
import { proxy } from "@/lib/api";

export async function GET() {
  return proxy("/admin/documents/categories", { method: "GET" });
}
