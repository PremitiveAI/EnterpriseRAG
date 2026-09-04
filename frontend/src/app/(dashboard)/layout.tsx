import { redirect } from "next/navigation";
import { Shell } from "@/components/layout/Shell";
import { getSubjectType, isAuthenticated } from "@/lib/session";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  // Server-side guard. Route groups carry no behaviour on their own.
  if (!(await isAuthenticated())) redirect("/login");
  return <Shell subjectType={await getSubjectType()}>{children}</Shell>;
}
