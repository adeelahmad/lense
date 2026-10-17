import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom";

import { Chats } from "@/app/openapi-client";
import type { ChatTools } from "@/app/openapi-client/types.gen";
import { ScopeBar } from "@/components/chat/composer";
import { choiceLabel, settingLink, ToolsChip, useSaveTools, type ToolChoice } from "@/components/chat/tools-picker";
import { ToastProvider } from "@/components/ui/toast";
import { data } from "@/lib/api/browser";

jest.mock("@/app/openapi-client", () => ({
  Chats: { chatTools: jest.fn(), getChat: jest.fn(), updateChat: jest.fn() },
  Collections: {},
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({
    namespaces: [
      { name: "pods", recordings: 3, minutes: 40 },
      { name: "calls", recordings: 2, minutes: 10 },
    ],
  }),
}));
jest.mock("@/components/import/pending", () => ({ chooseFiles: jest.fn(() => Promise.resolve([])) }));

beforeAll(() => {
  // Radix's popper measures what it places
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

const group = (name: string, label: string, more: Partial<ChatTools["groups"][number]> = {}) => ({
  name,
  label,
  tools: [{ name: `${name}_tool`, effect: "read" as const, out: false, available: more.available ?? true }],
  asks: false,
  out: false,
  available: true,
  why: null,
  setting: null,
  ...more,
});

const CATALOG: ChatTools = {
  groups: [
    group("archive", "Archive"),
    group("notes", "Notes", { asks: true }),
    group("browser", "Your browser", {
      out: true,
      available: false,
      why: "switched off by an admin",
      setting: "ai.browser",
    }),
    group("server", "Server", { available: false, why: "for admins" }),
  ],
  max_tool_calls: 12,
  remembered: { tools: null, max_tool_calls: null },
  namespaces_note:
    "Web and MCP tools reach beyond your namespaces: the namespaces you pick narrow the archive's tools only.",
};

function chip(value: ToolChoice, admin = false) {
  const onChange = jest.fn();
  render(<ToolsChip value={value} onChange={onChange} catalog={CATALOG} admin={admin} />);
  const button = screen.getByRole("button", { name: /Choose the tools for this conversation/ });
  return { onChange, button };
}

describe("the Tools chip", () => {
  it("is a button, so Enter opens it, and says what is picked", () => {
    const { button } = chip({ tools: null, max_tool_calls: null });
    expect(button.tagName).toBe("BUTTON");
    expect(button).toHaveTextContent("Tools: Auto");
    expect(choiceLabel({ tools: [], max_tool_calls: null })).toBe("Tools: no tools");
    expect(choiceLabel({ tools: ["archive", "notes"], max_tool_calls: 5 })).toBe("Tools: 2 picked · 5 calls");
  });

  it("opens on Auto, with every group you have on and the others saying why", () => {
    const { button } = chip({ tools: null, max_tool_calls: null });
    fireEvent.click(button);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("switch", { name: /Auto/ })).toBeChecked();
    expect(within(dialog).getByRole("switch", { name: "Archive" })).toBeChecked();
    expect(within(dialog).getByRole("switch", { name: "Server" })).toBeDisabled();
    expect(within(dialog).getByText("For admins.")).toBeInTheDocument();
    expect(within(dialog).getByText("needs approval")).toBeInTheDocument();
    expect(within(dialog).getByText("sends data out")).toBeInTheDocument();
    expect(within(dialog).getByText(/Web and MCP tools reach beyond your namespaces/)).toBeInTheDocument();
    // only admins are sent to the setting
    expect(within(dialog).queryByRole("link", { name: "Open settings" })).not.toBeInTheDocument();
  });

  it("links an admin to the setting that switches a group back on", () => {
    fireEvent.click(chip({ tools: null, max_tool_calls: null }, true).button);
    expect(screen.getByRole("link", { name: "Open settings" })).toHaveAttribute("href", settingLink("ai.browser"));
    expect(settingLink("ai.browser")).toBe("/settings/ai?field=ai.browser");
  });

  it("turning a group off leaves Auto with the rest of what you have", () => {
    const { button, onChange } = chip({ tools: null, max_tool_calls: null });
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("switch", { name: "Notes" }));
    expect(onChange).toHaveBeenCalledWith({ tools: ["archive"], max_tool_calls: null });
  });

  it("picks groups, keeps single tools, and goes back to Auto", () => {
    const { button, onChange } = chip({ tools: ["archive", "find_entities"], max_tool_calls: null });
    fireEvent.click(button);
    expect(screen.getByRole("switch", { name: "Notes" })).not.toBeChecked();
    expect(screen.getByText("Also picked: find_entities")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Notes" }));
    expect(onChange).toHaveBeenLastCalledWith({ tools: ["archive", "find_entities", "notes"], max_tool_calls: null });
    fireEvent.click(screen.getByRole("switch", { name: /Auto/ }));
    expect(onChange).toHaveBeenLastCalledWith({ tools: null, max_tool_calls: null });
  });

  it("limits the tool calls, from 1 to the server's most", () => {
    let r = chip({ tools: null, max_tool_calls: null });
    fireEvent.click(r.button);
    fireEvent.click(screen.getByRole("switch", { name: "Limit tool calls per answer" }));
    expect(r.onChange).toHaveBeenCalledWith({ tools: null, max_tool_calls: 10 });
    cleanup();

    r = chip({ tools: null, max_tool_calls: 12 });
    fireEvent.click(r.button);
    expect(screen.getByRole("button", { name: "More tool calls" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Fewer tool calls" }));
    expect(r.onChange).toHaveBeenLastCalledWith({ tools: null, max_tool_calls: 11 });
    fireEvent.change(screen.getByRole("spinbutton", { name: "Tool calls per answer" }), { target: { value: "40" } });
    expect(r.onChange).toHaveBeenCalledTimes(1); // past the most: not sent
    fireEvent.change(screen.getByRole("spinbutton", { name: "Tool calls per answer" }), { target: { value: "3" } });
    expect(r.onChange).toHaveBeenLastCalledWith({ tools: null, max_tool_calls: 3 });
  });
});

describe("saving a conversation's tools", () => {
  const ok = (data: unknown) => ({ data, response: { ok: true, status: 200 } });

  it("builds a change on the one before while that one is still being saved, and saves them in order", async () => {
    let server: ToolChoice = { tools: null, max_tool_calls: null };
    const saving: (() => void)[] = []; // each save, finished when the test says
    (Chats.updateChat as jest.Mock).mockImplementation(
      ({ body }: { body: ToolChoice }) =>
        new Promise((done) =>
          saving.push(() => {
            server = { ...server, ...body };
            done(ok({ ok: true }));
          }),
        ),
    );
    (Chats.getChat as jest.Mock).mockImplementation(() => Promise.resolve(ok({ id: 4, ...server })));
    function Conversation() {
      const chat = useQuery({ queryKey: ["chat", 4], queryFn: () => data(Chats.getChat({ path: { cid: 4 } })) });
      const save = useSaveTools(4);
      if (!chat.data) return null;
      const value = { tools: chat.data.tools ?? null, max_tool_calls: chat.data.max_tool_calls ?? null };
      return <ToolsChip value={value} onChange={(v) => void save(v)} catalog={CATALOG} admin={false} />;
    }
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <Conversation />
        </ToastProvider>
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /^Tools: Auto\./ }));
    fireEvent.click(screen.getByRole("switch", { name: "Notes" }));
    await waitFor(() => expect(screen.getByRole("switch", { name: "Notes" })).not.toBeChecked()); // shown at once
    // the limit, before Notes is saved: it keeps Notes off, and waits for that save
    fireEvent.click(screen.getByRole("switch", { name: "Limit tool calls per answer" }));
    await waitFor(() => expect(screen.getByRole("switch", { name: "Limit tool calls per answer" })).toBeChecked());
    expect(Chats.updateChat).toHaveBeenCalledTimes(1);
    act(() => saving[0]());
    await waitFor(() => expect(Chats.updateChat).toHaveBeenCalledTimes(2));
    expect((Chats.updateChat as jest.Mock).mock.calls[1][0].body).toEqual({ tools: ["archive"], max_tool_calls: 10 });
    expect(Chats.getChat).toHaveBeenCalledTimes(1); // not read back between the two
    act(() => saving[1]());
    await waitFor(() => expect(Chats.getChat).toHaveBeenCalledTimes(2));
    expect(server).toEqual({ tools: ["archive"], max_tool_calls: 10 });
    expect(screen.getByRole("button", { name: /^Tools: 1 picked · 10 calls\./ })).toBeInTheDocument();
  });
});

describe("the scope's namespaces", () => {
  it("go back to all of them in one tap", () => {
    const onChange = jest.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <ScopeBar scope={{ namespaces: ["pods"] }} onChange={onChange} model={null} tools={null} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Scope" }));
    const all = screen.getByRole("checkbox", { name: "All my namespaces" });
    expect(all).not.toBeChecked();
    fireEvent.click(all);
    expect(onChange).toHaveBeenCalledWith({ namespaces: undefined });
  });
});
