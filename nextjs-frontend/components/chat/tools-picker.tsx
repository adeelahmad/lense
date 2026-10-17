"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Minus, Plus, Wrench } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Chats } from "@/app/openapi-client";
import type { ChatTools, ToolGroup } from "@/app/openapi-client/types.gen";
import { settingHref } from "@/components/settings/find";
import { FIELDS, fieldId, SECTIONS } from "@/components/settings/model";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/field";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { data, useApiClient } from "@/lib/api/browser";
import { cn } from "@/lib/utils";

/** What a conversation's answers may use: group or tool names (null: Auto, every tool you have) and a cap on their
 * tool calls (null: none beyond the rounds). */
export type ToolChoice = { tools: string[] | null; max_tool_calls: number | null };

/** The limit a new one starts at, when the person turns a limit on. */
const FIRST_LIMIT = 10;

/** The tool groups and tools you have (GET /chats/tools), for a conversation's scope when there is one. */
export function useToolCatalog(chatId: number | null, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["chat-tools", chatId],
    queryFn: () => data(Chats.chatTools({ client, query: chatId != null ? { chat_id: chatId } : undefined })),
    enabled,
    staleTime: 60_000,
  });
}

/** Where an admin switches a setting back on (`ai.browser`): its field in Settings, else its section. */
export function settingLink(key: string): string | null {
  const field = FIELDS.find((f) => fieldId(f) === key);
  const section = SECTIONS.find((s) => s.backend.includes(key.split(".")[0]));
  return section ? settingHref(section.id, field) : null;
}

/** The chip's words: Auto, no tools, or how many groups and tools, with the limit. */
export function choiceLabel(choice: ToolChoice): string {
  const what = choice.tools == null ? "Auto" : choice.tools.length === 0 ? "no tools" : `${choice.tools.length} picked`;
  return choice.max_tool_calls != null ? `Tools: ${what} · ${choice.max_tool_calls} calls` : `Tools: ${what}`;
}

/** The "Tools" chip beside the scope: Enter or a tap opens what this conversation's answers may use. */
export function ToolsChip({
  value,
  onChange,
  catalog,
  admin,
}: {
  value: ToolChoice;
  onChange: (v: ToolChoice) => void;
  catalog: ChatTools | undefined;
  admin: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`${choiceLabel(value)}. Choose the tools for this conversation`}
          className={cn(
            "inline-flex h-7 items-center gap-1 rounded-pill border px-2.5 text-[12px] font-semibold [&_svg]:size-[13px]",
            value.tools == null && value.max_tool_calls == null
              ? "border-border bg-background text-fg-secondary hover:border-fg-muted hover:text-fg"
              : "border-blue-border bg-blue-surface text-fg-accent",
          )}
        >
          <Wrench aria-hidden />
          {choiceLabel(value)}
          <ChevronDown aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start">
        <ToolsPicker value={value} onChange={onChange} catalog={catalog} admin={admin} onClose={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  );
}

/** Auto, a switch for each group (saying which ask first or send data out, and why one is missing), and the limit. */
export function ToolsPicker({
  value,
  onChange,
  catalog,
  admin,
  onClose,
}: {
  value: ToolChoice;
  onChange: (v: ToolChoice) => void;
  catalog: ChatTools | undefined;
  admin: boolean;
  onClose: () => void;
}) {
  const groups = catalog?.groups ?? [];
  const has = groups.filter((g) => g.available).map((g) => g.name);
  const names = new Set(groups.map((g) => g.name));
  const auto = value.tools == null;
  const on = (g: ToolGroup) => g.available && (auto || Boolean(value.tools?.includes(g.name)));
  const flip = (g: ToolGroup, v: boolean) => {
    const now = auto ? has : (value.tools ?? []);
    const others = now.filter((n) => n !== g.name);
    onChange({ ...value, tools: v ? [...others, g.name] : others });
  };
  const most = catalog?.max_tool_calls ?? 50;
  // single tools picked over the API stay picked; the switches are for groups
  const single = (value.tools ?? []).filter((n) => !names.has(n));
  return (
    <div className="flex w-[380px] max-w-[calc(100vw-32px)] flex-col gap-3 p-3">
      <Switch
        checked={auto}
        onCheckedChange={(v) => onChange({ ...value, tools: v ? null : has })}
        label={
          <span>
            <span className="font-semibold">Auto</span> <span className="text-fg-muted">every tool you have</span>
          </span>
        }
      />
      {!catalog && <p className="m-0 text-[13px] text-fg-muted">Loading tools…</p>}
      <ul className="m-0 flex max-h-[280px] list-none flex-col gap-2 overflow-y-auto p-0" aria-label="Tool groups">
        {groups.map((g) => {
          const link = admin && g.setting ? settingLink(g.setting) : null;
          return (
            <li key={g.name} className="flex flex-col gap-0.5">
              <div className="flex items-center gap-2">
                <Switch checked={on(g)} disabled={!g.available} onCheckedChange={(v) => flip(g, v)} label={g.label} />
                <span className="ml-auto flex shrink-0 gap-1">
                  {g.asks && <Badge tone="gate">needs approval</Badge>}
                  {g.out && <Badge tone="red">sends data out</Badge>}
                </span>
              </div>
              {!g.available && g.why && (
                <p className="m-0 pl-[46px] text-[12px] text-fg-muted">
                  {g.why.charAt(0).toUpperCase() + g.why.slice(1)}.{" "}
                  {link && (
                    <Link href={link} className="font-semibold text-fg-accent hover:underline">
                      Open settings
                    </Link>
                  )}
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {single.length > 0 && <p className="m-0 text-[12px] text-fg-muted">Also picked: {single.join(", ")}</p>}
      <Limit value={value.max_tool_calls} most={most} onChange={(n) => onChange({ ...value, max_tool_calls: n })} />
      <p className="m-0 text-[12px] text-fg-muted">{catalog?.namespaces_note}</p>
      <div className="flex justify-end">
        <Button size="sm" variant="primary" onClick={onClose}>
          Done
        </Button>
      </div>
    </div>
  );
}

/** The tool calls one answer may make: no limit, or a number from 1 to the server's most, with − and +. */
function Limit({
  value,
  most,
  onChange,
}: {
  value: number | null;
  most: number;
  onChange: (n: number | null) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null); // what's typed, while it's typed
  const clamp = (n: number) => Math.max(1, Math.min(most, n));
  return (
    <div className="flex flex-col gap-1.5 border-t border-border pt-3">
      <Switch
        checked={value != null}
        onCheckedChange={(v) => onChange(v ? clamp(FIRST_LIMIT) : null)}
        label={<span className="font-semibold">Limit tool calls per answer</span>}
      />
      {value != null && (
        <div className="flex items-center gap-1.5 pl-[46px]">
          <button
            type="button"
            aria-label="Fewer tool calls"
            disabled={value <= 1}
            onClick={() => onChange(clamp(value - 1))}
            className="grid size-7 place-items-center rounded-full border border-border text-fg-secondary hover:text-fg disabled:opacity-50"
          >
            <Minus className="size-3.5" />
          </button>
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={most}
            value={editing ?? String(value)}
            aria-label="Tool calls per answer"
            onChange={(e) => {
              setEditing(e.target.value);
              const n = Number(e.target.value);
              if (e.target.value && Number.isInteger(n) && n >= 1 && n <= most) onChange(n);
            }}
            onBlur={() => setEditing(null)}
            className="h-7 w-14 rounded-sm border border-border bg-background text-center text-[13px] tabular"
          />
          <button
            type="button"
            aria-label="More tool calls"
            disabled={value >= most}
            onClick={() => onChange(clamp(value + 1))}
            className="grid size-7 place-items-center rounded-full border border-border text-fg-secondary hover:text-fg disabled:opacity-50"
          >
            <Plus className="size-3.5" />
          </button>
          <span className="text-[12px] text-fg-muted">of at most {most}</span>
        </div>
      )}
    </div>
  );
}
