import { suggestedName } from "@/components/routines/routine-model";
import { nameFromEmail, nsSlug } from "@/lib/format";

describe("less typing", () => {
  it("turns a typed namespace name into a valid one", () => {
    expect(nsSlug("Customer Calls")).toBe("customer-calls");
    expect(nsSlug("  Équipe_2026!")).toBe("equipe_2026");
    expect(nsSlug("-x")).toBe("x");
  });

  it("names a person from their email", () => {
    expect(nameFromEmail("ana.lopez@x.io")).toBe("Ana Lopez");
    expect(nameFromEmail("bob")).toBe("Bob");
    expect(nameFromEmail("")).toBe("");
  });

  it("names a routine nobody named", () => {
    expect(suggestedName("daily", [{ type: "sync" }, { type: "pipeline" }])).toBe(
      "Nightly: sync sources, run a pipeline",
    );
    expect(suggestedName("manual", [])).toBe("On demand routine");
  });
});
