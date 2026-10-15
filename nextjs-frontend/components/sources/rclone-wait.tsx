"use client";

import { useEffect, useId, useRef, useState } from "react";

import { Sources } from "@/app/openapi-client";
import { isRcloneReturn, type RcloneWaiting } from "@/components/sources/rclone-signin";
import { Input } from "@/components/ui/field";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

const POLL_MS = 2000;

/**
 * Waiting for a sign-in through rclone on the server (startRclone). The address the service sent the browser to is
 * pasted here and handed on as soon as it looks right (Enter sends it too). Meanwhile it asks every two seconds whether
 * rclone got there itself, as it does when the browser runs on the server: then there's nothing to paste.
 * `onConnected` gets the connection, made and tested; `onOver` why the sign-in ended (cancelled at the service,
 * refused, ran out), to start again.
 */
export function RcloneWait({
  waiting,
  service,
  onConnected,
  onOver,
}: {
  waiting: RcloneWaiting;
  service: string;
  onConnected: (id: number) => void;
  onOver: (why: string) => void;
}) {
  const client = useApiClient();
  const id = useId();
  const [address, setAddress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const busy = useRef(false);
  const done = useRef(false);

  // one request at a time: a look and a pasted address don't race to make the connection
  const finish = async (addr?: string) => {
    if (busy.current || done.current) return;
    busy.current = true;
    if (addr) setSending(true);
    try {
      const r = await data(
        Sources.finishRcloneSignin({ client, path: { fid: waiting.id }, body: addr ? { address: addr } : {} }),
      );
      if (r.connected != null) {
        done.current = true;
        onConnected(r.connected);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 400) setError(e.message);
      else {
        done.current = true;
        onOver(e instanceof Error ? e.message : String(e));
      }
    } finally {
      busy.current = false;
      setSending(false);
    }
  };

  useEffect(() => {
    const t = window.setInterval(() => void finish(), POLL_MS);
    return () => window.clearInterval(t);
  }, [waiting.id]);

  const send = (text: string) => {
    setError(null);
    void finish(text.trim());
  };

  return (
    <div className="flex flex-col gap-3 text-[13px] leading-normal text-fg-secondary">
      <p>
        <span className="font-bold text-fg">1 · Sign in to {service}</span>{" "}
        {waiting.opened ? "in the new tab" : "on its page"} and allow rclone, the tool Lens connects with.{" "}
        <a
          href={waiting.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-fg underline underline-offset-2 hover:text-fg-accent"
        >
          {waiting.opened ? "Open it again" : `Open ${service}’s sign-in page`}
        </a>
      </p>
      <div className="flex flex-col gap-1.5">
        <label htmlFor={id} className="text-[13px]">
          <span className="font-bold text-fg">2 · Paste the address it ends on.</span> {service} sends the tab to a page
          that doesn’t load, at http://127.0.0.1:{waiting.port}/…. Copy that address from the address bar.
        </label>
        <Input
          id={id}
          autoFocus
          mono
          value={address}
          placeholder={`http://127.0.0.1:${waiting.port}/?state=…&code=…`}
          invalid={Boolean(error)}
          aria-describedby={`${id}-msg`}
          disabled={sending}
          onChange={(e) => {
            setAddress(e.target.value);
            if (isRcloneReturn(e.target.value, waiting.port)) send(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && address.trim()) {
              e.preventDefault();
              send(address);
            }
          }}
          spellCheck={false}
          autoComplete="off"
        />
        <span
          id={`${id}-msg`}
          role={error ? "alert" : undefined}
          className={error ? "text-[12.5px] text-red-dark" : "text-[12px] text-fg-muted"}
        >
          {error ?? (sending ? "Connecting…" : "On the machine Lens runs on, this finishes by itself.")}
        </span>
      </div>
    </div>
  );
}
