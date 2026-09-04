import type { Metadata } from "next";
import { HumanBuilder } from "@/components/human-builder";

export const metadata: Metadata = {
  title: "Human Mode",
  alternates: { canonical: "/human" },
};

export default function HumanModePage() {
  return <HumanBuilder />;
}
