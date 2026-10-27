import type { Metadata } from "next";
import { Suspense } from "react";
import { ProfileView } from "@/components/profiles/ProfileView";
import { LoadingBlock } from "@/components/ui";

export const metadata: Metadata = { title: "Handwriting profile" };

async function Profile({ params }: { params: PageProps<"/profiles/[id]">["params"] }) {
  const { id } = await params;
  return <ProfileView id={id} />;
}

export default function ProfilePage({ params }: PageProps<"/profiles/[id]">) {
  return (
    <Suspense fallback={<LoadingBlock label="Loading profile" />}>
      <Profile params={params} />
    </Suspense>
  );
}
