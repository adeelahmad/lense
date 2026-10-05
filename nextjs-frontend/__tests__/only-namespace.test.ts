import { onlyNamespace } from "@/lib/hooks/session";

jest.mock("next-auth/react", () => ({ useSession: () => ({ data: null }) }));

describe("the current namespace when none is picked", () => {
  it("is the only namespace there is", () => {
    expect(onlyNamespace([{ name: "archive" }])).toBe("archive");
  });

  it("stays all namespaces when there are several, or the one is only partly theirs", () => {
    expect(onlyNamespace([{ name: "archive" }, { name: "family" }])).toBeNull();
    expect(onlyNamespace([{ name: "family", partial: true }])).toBeNull();
    expect(onlyNamespace([])).toBeNull();
  });
});
