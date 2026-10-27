import type { Metadata } from "next";
import { Suspense } from "react";
import { LoadingBlock } from "@/components/ui";
import { Workspace } from "@/components/workspace/Workspace";

export const metadata: Metadata = { title: "Assignment" };

async function AssignmentWorkspace({ params }: { params: PageProps<"/assignments/[id]">["params"] }) {
  const { id } = await params;
  return <Workspace id={id} />;
}

export default function AssignmentPage({ params }: PageProps<"/assignments/[id]">) {
  return (
    <Suspense fallback={<LoadingBlock label="Loading assignment" />}>
      <AssignmentWorkspace params={params} />
    </Suspense>
  );
}
