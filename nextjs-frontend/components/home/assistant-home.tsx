"use client";

import { Mic, MessagesSquare, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { useChats } from "@/components/chat/data";
import { greeting } from "@/components/home/attention";
import { LensMark } from "@/components/ui/lens-mark";
import { useToast } from "@/components/ui/toast";
import { relative } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { canListen } from "@/lib/voice";

/** A new conversation over everything you can read (no namespace), optionally typed or spoken. */
export function assistantChatHref({ q, voice, send }: { q?: string; voice?: boolean; send?: boolean } = {}): string {
  const p = new URLSearchParams({ global: "1" });
  if (q) p.set("q", q);
  if (q && send) p.set("send", "1");
  if (voice) p.set("voice", "1");
  return `/chat?${p.toString()}`;
}

/** Asks for the microphone while the tap still counts as the person's own, so listening can start on the next page. */
async function allowMic(): Promise<boolean> {
  const md = typeof navigator !== "undefined" ? navigator.mediaDevices : undefined;
  if (!md?.getUserMedia) return true;
  try {
    const stream = await md.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}

/**
 * Assistant mode: one field and one big mic, like a search page. Touching the field (or typing) opens a chat over the
 * whole archive; the mic opens one that listens straight away. Earlier conversations are a tap below.
 */
export function AssistantHome({ switcher }: { switcher?: ReactNode }) {
  const router = useRouter();
  const toast = useToast();
  const { me } = useArchive();
  const chats = useChats();
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => setNow(new Date()), []);
  const first = (me?.user.name || me?.user.email || "").split(/[\s@]/)[0];
  const recent = (chats.data ?? []).filter((c) => c.kind !== "setup").slice(0, 4);

  const [question, setQuestion] = useState("");
  // Enter sends the question: the chat opens with it asked (an empty field opens an empty chat)
  const ask = () => router.push(assistantChatHref({ q: question.trim() || undefined, send: true }));
  const talk = async () => {
    if (!canListen()) {
      toast({
        title: "This browser can’t listen yet",
        body: "Type your question instead; voice works in Chrome, Edge and Safari.",
        tone: "gate",
      });
      return router.push(assistantChatHref());
    }
    if (!(await allowMic())) {
      toast({
        title: "The microphone is blocked",
        body: "Allow it for this site in the browser, then tap the mic again.",
        tone: "red",
      });
      return;
    }
    router.push(assistantChatHref({ voice: true }));
  };

  return (
    <div className="flex min-h-[calc(100dvh-4rem)] flex-col px-4 md:px-8">
      {switcher && <div className="flex justify-end pt-5 md:pt-6">{switcher}</div>}
      <div className="mx-auto flex w-full max-w-[640px] flex-1 flex-col items-center justify-center gap-7 pb-[10vh] pt-8">
        <div className="flex flex-col items-center gap-3">
          <LensMark size={48} />
          <h1 className="text-center text-[30px] font-extrabold leading-[1.15] tracking-[-.025em] text-fg md:text-[36px]">
            {now ? `${greeting(now)}${first ? `, ${first}` : ""}` : "Welcome back"}
          </h1>
        </div>

        <label className="flex h-14 w-full cursor-text items-center gap-3 rounded-pill border border-border bg-background px-5 shadow-1 transition-shadow duration-fast hover:shadow-2 focus-within:border-blue focus-within:shadow-[0_0_0_3px_var(--intent-surface)]">
          <Sparkles className="size-5 shrink-0 text-fg-muted" aria-hidden />
          <input
            type="text"
            autoFocus
            aria-label="Ask anything"
            placeholder="Ask anything about your archive"
            autoComplete="off"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) ask();
            }}
            className="min-w-0 flex-1 bg-transparent text-[16px] text-fg outline-none placeholder:text-fg-muted"
          />
        </label>

        <div className="flex flex-col items-center gap-2">
          <button
            type="button"
            aria-label="Talk"
            title="Start a voice conversation"
            onClick={() => void talk()}
            className="grid size-20 place-items-center rounded-full bg-blue text-white shadow-2 transition-[transform,background-color] duration-fast hover:scale-105 hover:bg-blue-dark active:scale-95"
          >
            <Mic className="size-9" />
          </button>
          <span className="text-[13px] text-fg-muted">Tap to talk</span>
        </div>

        {recent.length > 0 && (
          <nav aria-label="Recent conversations" className="flex w-full flex-col items-center gap-2">
            <ul className="flex flex-wrap justify-center gap-2">
              {recent.map((c) => (
                <li key={c.id}>
                  <Link
                    href={`/chat/${c.id}`}
                    className="inline-flex max-w-[260px] items-center gap-1.5 rounded-pill border border-border px-3 py-1.5 text-[13px] text-fg-secondary hover:bg-surface-neutral hover:text-fg"
                    title={c.created_at ? `Started ${relative(c.created_at)}` : undefined}
                  >
                    <MessagesSquare className="size-3.5 shrink-0" aria-hidden />
                    <span className="truncate">{c.title}</span>
                  </Link>
                </li>
              ))}
              <li>
                <Link
                  href="/chat"
                  className="inline-flex items-center rounded-pill px-3 py-1.5 text-[13px] font-semibold text-fg-accent hover:underline"
                >
                  All conversations
                </Link>
              </li>
            </ul>
          </nav>
        )}
      </div>
    </div>
  );
}
