import type { Metadata } from "next";
import { NewAssignmentView } from "@/components/NewAssignmentView";

export const metadata: Metadata = { title: "New assignment" };

export default function NewAssignmentPage() {
  return <NewAssignmentView />;
}
