"use client";

import "@toeverything/theme/style.css";

import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

export type LinkTarget = { target: string; label: string; kind: string };
export type EditorChange = { markdown: string; doc: string };

type Props = {
  /** The page's Markdown: read when there is no `doc` (a new page, or one the assistant last wrote). */
  markdown: string;
  /** The editor's own state (a Yjs update, base64) from the last save, when there is one. */
  doc: string | null;
  readOnly?: boolean;
  onChange: (change: EditorChange) => void;
  /** What @ (resources, people, pages) and # (topics) offer for what was typed after them. */
  search: (sign: "@" | "#", query: string) => Promise<LinkTarget[]>;
  /** A click on a link to something in Lens ("entity:5", "page:3"). */
  onOpenLink: (target: string) => void;
  className?: string;
};

const LENS_LINK = /^(recording|entity|collection|speaker|page):\d+$/;
const WEB_LINK = /^(https?:|mailto:)/i;

function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

function fromBase64(text: string): Uint8Array {
  const s = atob(text);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

/** AFFiNE's BlockSuite editor on one page. Its documents are Yjs CRDTs (what OctoBase stores and syncs). It keeps the
 * Markdown and its own state in step: @ and # open a menu of what to link, written as @[label](kind:id) in the
 * Markdown. Loaded in the browser only. */
export default function BlockEditor({ markdown, doc, readOnly, onChange, search, onOpenLink, className }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef({ onChange, search, onOpenLink });
  latest.current = { onChange, search, onOpenLink };
  // The page is read once per mount: the parent remounts the editor (key) for another page.
  const initial = useRef({ markdown, doc, readOnly });

  useEffect(() => {
    let disposed = false;
    let cleanup = () => {};
    (async () => {
      const [blockEffects, presetEffects, presets, blocks, store, std, Y] = await Promise.all([
        import("@blocksuite/blocks/effects"),
        import("@blocksuite/presets/effects"),
        import("@blocksuite/presets"),
        import("@blocksuite/blocks"),
        import("@blocksuite/store"),
        import("@blocksuite/block-std"),
        import("yjs"),
      ]);
      if (!customElements.get("affine-editor-container")) {
        blockEffects.effects();
        presetEffects.effects();
      }
      if (disposed || !host.current) return;
      const { markdown, doc: state, readOnly } = initial.current;
      const schema = new store.Schema().register(blocks.AffineSchemas);
      const collection = new store.DocCollection({ schema });
      collection.meta.initialize();

      let id = "page";
      if (state) {
        const d = collection.createDoc({ id });
        d.load();
        Y.applyUpdate(d.spaceDoc, fromBase64(state));
      } else {
        const job = new store.Job({ collection });
        const made = markdown.trim()
          ? await new blocks.MarkdownAdapter(job).toDoc({ file: markdown, assets: job.assetsManager })
          : undefined;
        if (made) id = made.id;
        else {
          const d = collection.createDoc({ id });
          d.load(() => {
            const root = d.addBlock("affine:page", {});
            d.addBlock("affine:surface", {}, root);
            d.addBlock("affine:paragraph", {}, d.addBlock("affine:note", {}, root));
          });
        }
      }
      const page = collection.getDoc(id, { readonly: Boolean(readOnly) });
      if (!page || disposed || !host.current) return;
      page.load();

      const linkedWidget = {
        triggerKeys: ["@", "#"],
        convertTriggerKey: false,
        ignoreBlockTypes: ["affine:code"],
        getMenus: async (
          query: string,
          abort: () => void,
          _host: unknown,
          inline: {
            yTextString: string;
            getInlineRange: () => { index: number; length: number } | null;
            deleteText: (r: { index: number; length: number }) => void;
            insertText: (r: { index: number; length: number }, text: string, attrs?: Record<string, unknown>) => void;
            setInlineRange: (r: { index: number; length: number }) => void;
          },
        ) => {
          const range = inline.getInlineRange();
          const at = range ? range.index - query.length - 1 : -1;
          const sign = inline.yTextString[at] === "#" ? "#" : "@";
          const hits = await latest.current.search(sign, query).catch(() => [] as LinkTarget[]);
          return [
            {
              name: sign === "#" ? "Topics" : "Link to",
              maxDisplay: 8,
              overflowText: "more",
              items: hits.map((h) => ({
                key: h.target,
                name: h.label,
                icon: sign === "#" ? blocks.TagsIcon : blocks.LinkedDocIcon,
                suffix: h.kind,
                action: () => {
                  abort(); // takes away what was typed: the sign and the query
                  inline.insertText({ index: at, length: 0 }, sign);
                  inline.insertText({ index: at + 1, length: 0 }, h.label, { link: h.target });
                  inline.insertText({ index: at + 1 + h.label.length, length: 0 }, " ");
                  inline.setInlineRange({ index: at + 2 + h.label.length, length: 0 });
                },
              })),
            },
          ];
        },
      };

      const editor = new presets.AffineEditorContainer();
      editor.pageSpecs = [...blocks.PageEditorBlockSpecs, std.ConfigExtension("affine:page", { linkedWidget })];
      editor.doc = page;
      editor.mode = "page";
      host.current.replaceChildren(editor);

      // Links to things in Lens open in the app, not as web addresses. Other links open only as web or mail addresses:
      // BlockSuite renders any href it is given, and the Markdown may come from the assistant (`javascript:` and such).
      const onClick = (e: MouseEvent) => {
        const a = (e.composedPath().find((n) => n instanceof HTMLAnchorElement) ?? null) as HTMLAnchorElement | null;
        if (!a) return;
        const href = (a.getAttribute("href") ?? "").trim();
        if (WEB_LINK.test(href)) return;
        e.preventDefault();
        e.stopPropagation();
        if (e.type === "click" && LENS_LINK.test(href)) latest.current.onOpenLink(href);
      };
      host.current.addEventListener("click", onClick, true);
      host.current.addEventListener("auxclick", onClick, true);

      let timer: ReturnType<typeof setTimeout> | undefined;
      const save = async (flush = false) => {
        const job = new store.Job({ collection });
        const snapshot = job.docToSnapshot(page);
        if (!snapshot) return;
        const out = await new blocks.MarkdownAdapter(job).fromDocSnapshot({ snapshot, assets: job.assetsManager });
        if (disposed && !flush) return;
        const title = String((page.root as { title?: { toString(): string } } | null)?.title ?? "");
        let md = out.file.trim();
        if (title && md.startsWith(`# ${title}`)) md = md.slice(title.length + 2).trimStart();
        latest.current.onChange({
          markdown: md,
          doc: toBase64(Y.encodeStateAsUpdate(page.spaceDoc)),
        });
      };
      const changed = page.slots.blockUpdated.on(() => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          timer = undefined;
          void save();
        }, 600);
      });
      const el = host.current;
      cleanup = () => {
        if (timer) void save(true); // what was typed in the last moment before leaving the page
        clearTimeout(timer);
        changed.dispose();
        el.removeEventListener("click", onClick, true);
        el.removeEventListener("auxclick", onClick, true);
        el.replaceChildren();
        collection.dispose();
      };
    })().catch((err) => console.error("The editor didn't load", err));
    return () => {
      disposed = true;
      cleanup();
    };
  }, []);

  // The page's title is Lens's own field above the editor, so the editor's title line is hidden.
  return <div ref={host} className={cn("lens-block-editor min-h-[240px] [&_doc-title]:hidden", className)} />;
}
