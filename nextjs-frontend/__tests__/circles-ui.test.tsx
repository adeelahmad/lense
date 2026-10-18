import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import { CirclesPanel } from "@/components/account/circles";
import {
  addable,
  firstPick,
  isHandle,
  personLabel,
  reach,
  remoteLabel,
  shareable,
  whom,
} from "@/components/circles/model";
import { ShareDialog } from "@/components/circles/share-dialog";
import { familyOf } from "@/components/timeline/model";
import { TooltipProvider } from "@/components/ui/tooltip";

const ok = (data: unknown) => Promise.resolve({ data, response: { ok: true, status: 200 } });
const sam = { id: 2, name: "Sam", email: "sam@x.io" };
const sara = { id: 3, name: null, email: "sara@x.io" };
const family = { id: 1, name: "Immediate family", members: [sam] };
const friends = { id: 4, name: "Friends", members: [sam, sara] };

const mockApi = {
  listCircles: jest.fn(),
  listCirclePeople: jest.fn(() => ok([sam, sara])),
  createCircle: jest.fn(() => ok({ id: 9, name: "Friends", members: [] })),
  updateCircle: jest.fn(() => ok(family)),
  deleteCircle: jest.fn(() => ok({ ok: true })),
  shareWithCircles: jest.fn(() =>
    ok({ id: 7, at: "", object: {}, circles: [], people: [], with_item: false, granted: 0 }),
  ),
  undoCircleShare: jest.fn(() => ok({ undone: 1, taken_back: 0 })),
  getElsewhere: jest.fn(),
  setElsewhere: jest.fn(() => ok({ on: true, findable: true, handle: "adeel@lens.example" })),
};
jest.mock("@/app/openapi-client", () => ({
  get Circles() {
    return mockApi;
  },
}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));
jest.mock("@/lib/hooks/session", () => ({
  useArchive: () => ({ me: { user: { email: "adeel@x.io" }, roles: { calls: "owner", pods: "owner" } }, admin: false }),
}));
const toast = jest.fn();
jest.mock("@/components/ui/toast", () => ({ useToast: () => toast }));

beforeEach(() => {
  jest.clearAllMocks();
  mockApi.listCircles.mockImplementation(() => ok({ circles: [family], suggested: ["Close family", "Friends"] }));
  mockApi.getElsewhere.mockImplementation(() => ok({ on: false, findable: false }));
});

function show(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("the circles model", () => {
  it("names people, and offers only who isn't in the circle yet", () => {
    expect(personLabel(sara)).toBe("sara@x.io");
    expect(personLabel({ id: 5 })).toBe("Account 5");
    expect(addable([sam, sara], family).map((p) => p.id)).toEqual([3]);
  });

  it("says who a share goes to and counts each person once", () => {
    const all = [family, friends];
    expect(whom(all, [1])).toBe("Immediate family");
    expect(whom(all, [1, 4])).toBe("Immediate family and Friends");
    expect(whom([...all, { id: 6, name: "Work" }], [1, 4, 6])).toBe("3 circles");
    expect(reach(all, [1, 4])).toBe(2);
    const kim = { id: 1, handle: "kim@far.example", name: null };
    expect(
      reach(
        [
          { ...family, remote: [kim] },
          { ...friends, remote: [kim] },
        ],
        [1, 4],
      ),
    ).toBe(3);
    expect(remoteLabel(kim)).toBe("kim@far.example");
    expect(isHandle("@kim@far.example")).toBe(true);
    expect(isHandle("kim")).toBe(false);
    expect(firstPick([family])).toEqual([1]);
    expect(firstPick(all)).toEqual([]);
  });

  it("doesn't pass shares on, and files them under Shared", () => {
    expect(shareable("share.received")).toBe(false);
    expect(shareable("email.received")).toBe(true);
    expect(familyOf("share.received")).toBe("Shared");
  });
});

describe("circles under Profile", () => {
  it("makes a suggested circle in one click", async () => {
    show(<CirclesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Close family" }));
    await waitFor(() =>
      expect(mockApi.createCircle).toHaveBeenCalledWith(expect.objectContaining({ body: { name: "Close family" } })),
    );
  });

  it("adds and takes out people", async () => {
    show(<CirclesPanel />);
    const add = await screen.findByRole("combobox", { name: "Add someone to Immediate family" });
    await screen.findByRole("option", { name: "sara@x.io" });
    fireEvent.change(add, { target: { value: "3" } });
    await waitFor(() =>
      expect(mockApi.updateCircle).toHaveBeenCalledWith(
        expect.objectContaining({ path: { cid: 1 }, body: { add: [3] } }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Take Sam out of Immediate family" }));
    await waitFor(() =>
      expect(mockApi.updateCircle).toHaveBeenCalledWith(expect.objectContaining({ body: { remove: [2] } })),
    );
  });

  it("lets a circle see when you're free in one click", async () => {
    show(<CirclesPanel />);
    fireEvent.click((await screen.findAllByRole("switch"))[0]);
    await waitFor(() =>
      expect(mockApi.updateCircle).toHaveBeenCalledWith(
        expect.objectContaining({ path: { cid: 1 }, body: { lets: ["free_busy"] } }),
      ),
    );
  });

  it("lets a circle ask your agent about the namespaces you pick", async () => {
    mockApi.listCircles.mockImplementation(() =>
      ok({ circles: [{ ...family, lets: ["free_busy", "questions"], about: ["calls"] }], suggested: [] }),
    );
    show(<CirclesPanel />);
    fireEvent.click(await screen.findByRole("checkbox", { name: "pods" }));
    await waitFor(() =>
      expect(mockApi.updateCircle).toHaveBeenCalledWith(
        expect.objectContaining({ body: { about: ["calls", "pods"] } }),
      ),
    );
    fireEvent.click(screen.getAllByRole("switch")[1]);
    await waitFor(() =>
      expect(mockApi.updateCircle).toHaveBeenCalledWith(expect.objectContaining({ body: { lets: ["free_busy"] } })),
    );
  });

  it("deletes a circle with Undo instead of a confirm", async () => {
    show(<CirclesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete Immediate family" }));
    await waitFor(() => expect(mockApi.deleteCircle).toHaveBeenCalled());
    const t = toast.mock.calls.at(-1)[0];
    expect(t.title).toBe("Deleted Immediate family");
    t.action.onClick();
    await waitFor(() =>
      expect(mockApi.createCircle).toHaveBeenCalledWith(
        expect.objectContaining({ body: { name: "Immediate family", members: [2] } }),
      ),
    );
  });
});

describe("sharing", () => {
  it("picks the only circle, shares with a note, and undoes from the toast", async () => {
    show(<ShareDialog open onOpenChange={() => {}} activity="email-5" what="“School trip”" />);
    const box = await screen.findByRole("checkbox");
    await waitFor(() => expect(box).toBeChecked());
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Packed lunch on Friday" } });
    fireEvent.click(screen.getByRole("button", { name: "Share with 1" }));
    await waitFor(() =>
      expect(mockApi.shareWithCircles).toHaveBeenCalledWith(
        expect.objectContaining({
          body: {
            activity: "email-5",
            recording: null,
            circles: [1],
            note: "Packed lunch on Friday",
            with_item: false,
          },
        }),
      ),
    );
    const t = toast.mock.calls.at(-1)[0];
    expect(t.title).toBe("Shared with Immediate family");
    t.action.onClick();
    await waitFor(() =>
      expect(mockApi.undoCircleShare).toHaveBeenCalledWith(expect.objectContaining({ path: { sid: 7 } })),
    );
  });

  it("offers the item itself only for a resource", async () => {
    show(<ShareDialog open onOpenChange={() => {}} recording={12} what="“Report”" />);
    fireEvent.click(await screen.findByRole("switch"));
    await waitFor(() => expect(screen.getByRole("checkbox")).toBeChecked());
    fireEvent.click(screen.getByRole("button", { name: "Share with 1" }));
    await waitFor(() =>
      expect(mockApi.shareWithCircles).toHaveBeenCalledWith(
        expect.objectContaining({ body: expect.objectContaining({ recording: 12, with_item: true }) }),
      ),
    );
  });

  it("points to Profile when there are no circles yet", async () => {
    mockApi.listCircles.mockImplementation(() => ok({ circles: [], suggested: [] }));
    show(<ShareDialog open onOpenChange={() => {}} activity="email-5" what="“School trip”" />);
    expect(await screen.findByRole("link", { name: "Make one under Profile" })).toHaveAttribute(
      "href",
      "/account#circles",
    );
  });
});

describe("circles with people on other servers", () => {
  it("shows nothing of it until an admin turns it on", async () => {
    show(<CirclesPanel />);
    await screen.findByRole("button", { name: "Delete Immediate family" });
    expect(screen.queryByRole("textbox", { name: /another server/ })).not.toBeInTheDocument();
  });

  it("makes you findable, and adds someone elsewhere by handle with Enter", async () => {
    mockApi.getElsewhere.mockImplementation(() => ok({ on: true, findable: false }));
    show(<CirclesPanel />);
    fireEvent.click(await screen.findByRole("switch", { name: /People on other servers can find you/ }));
    await waitFor(() =>
      expect(mockApi.setElsewhere).toHaveBeenCalledWith(expect.objectContaining({ body: { findable: true } })),
    );
    expect(await screen.findByText("adeel@lens.example")).toBeInTheDocument();
    const box = screen.getByRole("textbox", { name: "Add someone on another server to Immediate family" });
    fireEvent.change(box, { target: { value: "kim@far.example" } });
    fireEvent.submit(box);
    await waitFor(() =>
      expect(mockApi.updateCircle).toHaveBeenCalledWith(
        expect.objectContaining({ path: { cid: 1 }, body: { add_remote: ["kim@far.example"] } }),
      ),
    );
  });

  it("takes someone elsewhere out by their handle", async () => {
    mockApi.getElsewhere.mockImplementation(() => ok({ on: true, findable: true, handle: "adeel@lens.example" }));
    mockApi.listCircles.mockImplementation(() =>
      ok({ circles: [{ ...family, remote: [{ id: 3, handle: "kim@far.example", name: "Kim" }] }], suggested: [] }),
    );
    show(<CirclesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Take Kim out of Immediate family" }));
    await waitFor(() =>
      expect(mockApi.updateCircle).toHaveBeenCalledWith(
        expect.objectContaining({ body: { remove_remote: ["kim@far.example"] } }),
      ),
    );
  });
});
