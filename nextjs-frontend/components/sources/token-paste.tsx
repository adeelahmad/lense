"use client";

import { useId } from "react";

import { parseRcloneToken } from "@/components/sources/source-model";
import { Textarea } from "@/components/ui/field";
import { CodeBlock } from "@/components/ui/states";
import { cn } from "@/lib/utils";

const fmt = (d: Date) => d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });

/**
 * Sign in with rclone on your own computer and paste the result (SO2): the server can't open a browser.
 * `value` is what was pasted; the parent sends parseRcloneToken(value).token to the backend.
 */
export function TokenPaste({
  command,
  service,
  value,
  onChange,
  replacing,
}: {
  command: string;
  service: string;
  value: string;
  onChange: (v: string) => void;
  replacing?: boolean;
}) {
  const id = useId();
  const check = parseRcloneToken(value);
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[13px] leading-normal text-fg-secondary">
        The server can’t open a browser, so you sign in on your own computer with rclone and paste the result here.
        {replacing ? " The token you paste replaces the saved one." : ""}
      </p>
      <div className="flex flex-col gap-1.5">
        <span className="text-[12.5px] font-bold text-fg">1 · Run this on a computer with a browser</span>
        <CodeBlock text={command} label={`the ${command} command`} className="[&_pre]:py-2.5" />
      </div>
      <span className="text-[12.5px] font-bold text-fg">2 · Sign in to {service} in the browser that opens</span>
      <div className="flex flex-col gap-1.5">
        <label htmlFor={id} className="text-[12.5px] font-bold text-fg">
          3 · Paste everything rclone prints between the arrows
        </label>
        <Textarea
          id={id}
          mono
          rows={3}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          invalid={check.ok === false}
          aria-describedby={`${id}-msg`}
          placeholder='{"access_token":"…","token_type":"bearer","refresh_token":"…","expiry":"…"}'
          className="min-h-[70px] break-all text-[12px]"
          spellCheck={false}
          autoComplete="off"
        />
        <span id={`${id}-msg`} role={check.ok === false ? "alert" : undefined} className={cn("text-[12px] leading-snug", check.ok === false ? "text-red-dark" : check.ok ? "text-green-dark" : "text-fg-muted")}>
          {check.ok === false
            ? check.error
            : check.ok
              ? `✓ Looks like a ${service} token${check.expiry ? ` · expires ${fmt(check.expiry)}` : ""}${check.refreshes ? ", refreshes automatically" : ""}`
              : "Stored encrypted; never shown again after saving."}
        </span>
      </div>
    </div>
  );
}
