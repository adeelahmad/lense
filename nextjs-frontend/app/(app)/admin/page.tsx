import { redirect } from "next/navigation";

/** Admin opens on People. */
export default function AdminIndex() {
  redirect("/admin/people");
}
