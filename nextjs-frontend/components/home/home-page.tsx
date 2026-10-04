"use client";

import { useQuery } from "@tanstack/react-query";
import { LayoutDashboard, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";

import { Resources } from "@/app/openapi-client";
import { AssistantHome } from "@/components/home/assistant-home";
import { HomeScreen } from "@/components/home/home-screen";
import { Segmented } from "@/components/ui/tabs";
import { data, useApiClient } from "@/lib/api/browser";

export type HomeMode = "assistant" | "overview";
const KEY = "lens.home.mode";

function savedMode(): HomeMode | null {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "assistant" || v === "overview" ? v : null;
  } catch {
    return null;
  }
}

function saveMode(m: HomeMode) {
  try {
    window.localStorage.setItem(KEY, m);
  } catch {
    // private window: the choice lasts this visit
  }
}

/** The mode a visit opens in: the one you last picked, else the assistant once the archive has something in it. */
export function homeMode(saved: HomeMode | null, hasContent: boolean): HomeMode {
  return saved ?? (hasContent ? "assistant" : "overview");
}

/** Home: assistant mode (one field, one mic) or the overview (what needs you, what arrived, what's running). */
export function HomePage() {
  const client = useApiClient();
  const [saved, setSaved] = useState<HomeMode | null | undefined>(undefined);
  useEffect(() => setSaved(savedMode()), []);
  const any = useQuery({
    queryKey: ["recordings", "any"],
    queryFn: () => data(Resources.listRecordings({ client, query: { limit: 1 } })),
    staleTime: 60_000,
    enabled: saved === null,
  });

  // Nothing shows until we know which mode, so the page doesn't flash the other one first.
  if (saved === undefined || (saved === null && any.isPending)) return null;
  const mode = homeMode(saved, Boolean(any.data?.length));
  const pick = (m: string) => {
    saveMode(m as HomeMode);
    setSaved(m as HomeMode);
  };
  const switcher = (
    <Segmented
      label="Home view"
      value={mode}
      onChange={pick}
      items={[
        { value: "assistant", label: "Assistant", icon: <Sparkles /> },
        { value: "overview", label: "Overview", icon: <LayoutDashboard /> },
      ]}
    />
  );
  return mode === "assistant" ? <AssistantHome switcher={switcher} /> : <HomeScreen switcher={switcher} />;
}
