"use client";

import { useMutation } from "@tanstack/react-query";
import { Copy, Download, FileText, Type } from "lucide-react";

import { Chats } from "@/app/openapi-client";
import { answerMarkdown, copyText, plainText, type Cited } from "@/components/chat/copy";
import { saveBlob, slug } from "@/components/recording/hooks";
import { IconButton } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";

/** A conversation, or one message of it, as a file (GET /chats/{cid}/export.{fmt}; docs/assistant.md, "Copy and
 * export"). */
export type ExportFormat = "md" | "pdf" | "json";

export const FORMATS: { fmt: ExportFormat; label: string }[] = [
  { fmt: "md", label: "Markdown" },
  { fmt: "pdf", label: "PDF" },
  { fmt: "json", label: "JSON" },
];

/** The download's name, as the server names it: the title's slug, and the message's id for one message. */
export function exportName(title: string | null | undefined, fmt: ExportFormat, message?: number): string {
  const base = title ? slug(title) : "conversation";
  return `${base === "recording" ? "conversation" : base}${message != null ? `-message-${message}` : ""}.${fmt}`;
}

/** Download an export. A PDF the server can't make here (409: no Chromium, anytopdf or LibreOffice) is printed by
 * the browser instead: `onPrint` gets the message, if it was one, and opens the print dialog. */
export function useChatExport(
  chatId: number | null,
  title: string | null | undefined,
  onPrint: (message?: number) => void,
) {
  const client = useApiClient();
  const toast = useToast();
  return useMutation({
    mutationFn: async ({ fmt, message }: { fmt: ExportFormat; message?: number }) => {
      const blob = (await data(
        Chats.exportChat({
          client,
          path: { cid: chatId as number, fmt },
          query: message != null ? { message } : undefined,
          parseAs: "blob",
        }),
      )) as unknown as Blob;
      saveBlob(blob, exportName(title, fmt, message));
    },
    onError: (e, { fmt, message }) => {
      if (fmt === "pdf" && e instanceof ApiError && e.status === 409) {
        toast({
          title: "Printing it instead",
          body: "This server can’t make PDFs, so your browser’s print dialog opens: pick Save as PDF.",
          tone: "gate",
        });
        onPrint(message);
        return;
      }
      toast({
        title: "Couldn’t export it",
        body: e instanceof ApiError ? e.message : "Please try again.",
        tone: "red",
      });
    },
  });
}

/** Export: a download icon with Markdown, PDF and JSON. */
export function ExportMenu({
  onExport,
  label = "Export",
  disabled,
}: {
  onExport: (fmt: ExportFormat) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <Menu>
      <MenuTrigger asChild disabled={disabled}>
        <IconButton label={label} disabled={disabled} className="print:hidden">
          <Download />
        </IconButton>
      </MenuTrigger>
      <MenuContent className="min-w-[200px]">
        <MenuLabel>{label} as</MenuLabel>
        {FORMATS.map(({ fmt, label: name }) => (
          <MenuItem key={fmt} onSelect={() => onExport(fmt)}>
            {name}
          </MenuItem>
        ))}
      </MenuContent>
    </Menu>
  );
}

const ITEM = "w-full text-left";

/** An answer's copy menu: Copy as Markdown, Copy text and, once it's saved, Export it as Markdown, PDF or JSON. */
export function AnswerMenu({
  text,
  passages,
  onExport,
}: {
  text: string;
  passages: Cited[] | null | undefined;
  onExport?: (fmt: ExportFormat) => void;
}) {
  const toast = useToast();
  return (
    <Menu>
      <MenuTrigger asChild>
        <IconButton label="Copy" size={32} className="print:hidden [&_svg]:size-4">
          <Copy />
        </IconButton>
      </MenuTrigger>
      <MenuContent align="start" className="min-w-[210px]">
        {/* in the click itself, not after the menu closes: some browsers only let a click write to the clipboard */}
        <MenuItem asChild>
          <button
            type="button"
            className={ITEM}
            onClick={() =>
              void copyText(answerMarkdown(text, passages, window.location.origin), toast, "Copied as Markdown")
            }
          >
            <FileText />
            <span className="flex-1">Copy as Markdown</span>
          </button>
        </MenuItem>
        <MenuItem asChild>
          <button type="button" className={ITEM} onClick={() => void copyText(plainText(text), toast, "Copied")}>
            <Type />
            <span className="flex-1">Copy text</span>
          </button>
        </MenuItem>
        {onExport && (
          <>
            <MenuSeparator />
            <MenuLabel>Export as</MenuLabel>
            {FORMATS.map(({ fmt, label }) => (
              <MenuItem key={fmt} onSelect={() => onExport(fmt)}>
                {label}
              </MenuItem>
            ))}
          </>
        )}
      </MenuContent>
    </Menu>
  );
}
