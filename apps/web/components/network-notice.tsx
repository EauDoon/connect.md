"use client";

import { WifiOff } from "lucide-react";
import { useEffect, useState } from "react";

export function NetworkNotice({ label }: { label: string }) {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => { window.removeEventListener("online", update); window.removeEventListener("offline", update); };
  }, []);
  if (online) return null;
  return <p role="status" className="mt-4 flex items-start gap-2 rounded-xl border border-amber-300/25 bg-amber-300/[.08] p-3 text-sm leading-6 text-amber-100"><WifiOff className="mt-0.5 size-4 shrink-0" aria-hidden />You are offline. {label} cannot refresh until the connection returns.</p>;
}

