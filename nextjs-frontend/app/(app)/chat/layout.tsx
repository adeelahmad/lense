import type { Metadata } from "next";
import { Suspense, type ReactNode } from "react";

import { ChatApp } from "@/components/chat/chat-app";

export const metadata: Metadata = { title: "Chat" };

/** Chat (CH1–CH3, AI1–AI2). The app lives in the layout so a streaming answer survives /chat → /chat/{id}. */
export default function ChatLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense>
      <ChatApp />
      {children}
    </Suspense>
  );
}
