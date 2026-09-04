import { redirect } from "next/navigation";
import { getSubjectType } from "@/lib/session";

export default async function Home() {
  // Three destinations, not two: a Super Admin has no organization, so the
  // dashboard has nothing to show them.
  const subject = await getSubjectType();
  if (subject === "SUPER_ADMIN") redirect("/super-admin/organizations");
  redirect(subject ? "/dashboard" : "/login");
}
