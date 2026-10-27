import type { Metadata } from "next";
import { ProfilesView } from "@/components/profiles/ProfilesView";

export const metadata: Metadata = { title: "Handwriting profiles" };

export default function ProfilesPage() {
  return <ProfilesView />;
}
