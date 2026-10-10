/** @jest-environment node */
import { duration } from "@/components/admin/health";

jest.mock("@/app/openapi-client", () => ({}));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: null }) }));

describe("duration", () => {
  it("says how long something has been up", () => {
    expect(duration(420)).toBe("7 min");
    expect(duration(5 * 3600 + 12 * 60)).toBe("5 h 12 min");
    expect(duration(3 * 86400 + 4 * 3600)).toBe("3 d 4 h");
    expect(duration(null)).toBe("–");
  });
});
