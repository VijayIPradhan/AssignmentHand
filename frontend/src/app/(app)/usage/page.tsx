import type { Metadata } from "next";
import { UsageView } from "@/components/UsageView";

export const metadata: Metadata = { title: "AI usage" };

export default function UsagePage() {
  return <UsageView />;
}
