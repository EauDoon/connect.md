import type { Metadata } from "next";
import { MarkdownEditor } from "@/components/markdown-editor";

export const metadata: Metadata = {
  title: "MD Mode",
  alternates: { canonical: "/md" },
};

export default function MarkdownModePage() {
  return <MarkdownEditor />;
}
