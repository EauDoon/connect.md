"use client";

import React, { useState } from "react";
import { useDraft } from "@/components/draft-provider";
import { Button } from "@/components/ui/button";
import { downloadMarkdown } from "@/components/publish-panel";
import { encodeRecoveryBundle } from "@/lib/session-recovery";

export function SessionRecovery({ onBeforeAction }: { onBeforeAction?: () => void }) {
  const { checkpoints, getDraftSnapshot, masked } = useDraft();
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  return <details className="border-b border-white/10 px-4 py-4 sm:px-6">
    <summary className="min-h-11 cursor-pointer py-3 text-sm font-semibold text-white">Session recovery</summary>
    <p className="mt-2 text-xs leading-5 text-mist">Keep a local recovery file with the current draft and named checkpoints, including unfinished or invalid Markdown. It contains document text: store it privately. Nothing is saved automatically or uploaded.</p>
    <Button className="mt-3" variant="secondary" disabled={masked} onClick={() => {
      onBeforeAction?.();
      try {
        const snapshot = getDraftSnapshot();
        if (!snapshot) throw new Error("The current draft is unavailable.");
        const encoded = encodeRecoveryBundle({ kind: snapshot.kind, markdown: snapshot.markdown }, checkpoints);
        downloadMarkdown(encoded, "connectmd-session.recovery.json");
        setFailed(false);
        setMessage("Recovery file download requested. Keep the file before closing this tab. This does not mark the draft as validated or downloaded Markdown.");
      } catch (error) { setFailed(true); setMessage(error instanceof Error ? error.message : "Recovery download could not start. Keep this tab open and copy your source."); }
    }}>Download session recovery</Button>
    {message && <p className="mt-3 text-xs leading-5 text-mist" role={failed ? "alert" : "status"}>{message}</p>}
  </details>;
}
