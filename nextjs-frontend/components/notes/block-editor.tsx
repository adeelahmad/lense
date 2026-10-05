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
  /** "page" (a document) or "edgeless" (the endless canvas: shapes, connectors, mind maps, drawing). */
  view?: "page" | "edgeless";
  /** The Markdown changed without the editor (the assistant wrote it): bring the text in line, keep the drawings. */
  stale?: boolean;
  onChange: (change: EditorChange) => void;
  /** What @ (resources, people, pages) and # (topics) offer for what was typed after them. */
  search: (sign: "@" | "#", query: string) => Promise<LinkTarget[]>;
  /** A click on a link to something in Lens ("entity:5", "page:3"). */
  onOpenLink: (target: string) => void;
  /** Where the page's images and attachments are kept: the editor names each by a hash of its bytes. */
  blobs?: EditorBlobs;
  className?: string;
};

export type EditorBlobs = {
  get: (key: string) => Promise<Blob | null>;
  set: (key: string, blob: Blob) => Promise<void>;
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
export default function BlockEditor({
  markdown,
  doc,
  readOnly,
  view = "page",
  stale,
  onChange,
  search,
  onOpenLink,
  blobs,
  className,
}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef({ onChange, search, onOpenLink, blobs });
  latest.current = { onChange, search, onOpenLink, blobs };
  // The page is read once per mount: the parent remounts the editor (key) for another page.
  const initial = useRef({ markdown, doc, readOnly, view, stale });
  const editorRef = useRef<{ mode: string } | null>(null);
  useEffect(() => {
    if (editorRef.current && editorRef.current.mode !== view) editorRef.current.mode = view;
  }, [view]);

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
      const { markdown, doc: state, readOnly, view, stale } = initial.current;
      const schema = new store.Schema().register(blocks.AffineSchemas);
      // Images and attachments: held here while the page is open, kept on the server (and read from it) through `blobs`
      const held = new Map<string, Blob>();
      const lens = {
        name: "lens",
        readonly: Boolean(readOnly),
        get: async (key: string) => held.get(key) ?? (await latest.current.blobs?.get(key).catch(() => null)) ?? null,
        set: async (key: string, value: Blob) => {
          held.set(key, value);
          if (!readOnly) await latest.current.blobs?.set(key, value);
          return key;
        },
        delete: async (key: string) => void held.delete(key), // the server keeps it: undo may bring it back
        list: async () => [...held.keys()],
      };
      const collection = new store.DocCollection({ schema, blobSources: { main: lens } });
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
      if (state && stale && !readOnly) {
        // The text follows the Markdown; the canvas's shapes and drawings (outside the notes) stay as they were.
        const notes = page.getBlocksByFlavour("affine:note").map((b) => b.model);
        for (const note of notes) for (const child of [...note.children]) page.deleteBlock(child);
        const target = notes[0]?.id;
        if (target && markdown.trim()) {
          const job = new store.Job({ collection });
          const slice = await new blocks.MarkdownAdapter(job).toSliceSnapshot({
            file: markdown,
            assets: job.assetsManager,
            workspaceId: collection.id,
            pageId: page.id,
          });
          for (const b of slice?.content.flatMap((x) => x.children) ?? []) await job.snapshotToBlock(b, page, target);
        }
        if (disposed || !host.current) return;
      }

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
      const config = std.ConfigExtension("affine:page", { linkedWidget });
      editor.pageSpecs = [...blocks.PageEditorBlockSpecs, config];
      editor.edgelessSpecs = [...blocks.EdgelessEditorBlockSpecs, config];
      editor.doc = page;
      editor.mode = view;
      editorRef.current = editor;
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
      // Any change to the document: text, and the canvas's shapes and drawings, which aren't blocks.
      // Opening a page changes its document too (the editor sets itself up): only what someone does is saved.
      let touched = false;
      const touch = () => {
        touched = true;
      };
      host.current.addEventListener("keydown", touch, true);
      host.current.addEventListener("pointerdown", touch, true);
      host.current.addEventListener("paste", touch, true);
      host.current.addEventListener("drop", touch, true);
      const onUpdate = () => {
        if (!touched) return;
        clearTimeout(timer);
        timer = setTimeout(() => {
          timer = undefined;
          void save();
        }, 600);
      };
      page.spaceDoc.on("update", onUpdate);
      const el = host.current;
      if (state && stale && !readOnly) void save(); // the brought-in-line text, so the page isn't stale any more
      cleanup = () => {
        editorRef.current = null;
        if (timer) void save(true); // what was typed in the last moment before leaving the page
        clearTimeout(timer);
        page.spaceDoc.off("update", onUpdate);
        el.removeEventListener("click", onClick, true);
        el.removeEventListener("auxclick", onClick, true);
        for (const t of ["keydown", "pointerdown", "paste", "drop"]) el.removeEventListener(t, touch, true);
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
  return (
    <div
      ref={host}
      className={cn(
        "lens-block-editor min-h-[240px] [&_doc-title]:hidden",
        view === "edgeless" && "h-[70vh] overflow-hidden rounded-md border border-border",
        className,
      )}
    />
  );
}
