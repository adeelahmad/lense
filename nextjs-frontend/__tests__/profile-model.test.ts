import { confirmMismatch, passwordBlocked } from "@/components/account/profile-model";

describe("changing your password", () => {
  const good = { current: "old password 1", next: "a brand new password", confirm: "a brand new password" };

  it("says what's missing before it can be sent", () => {
    expect(passwordBlocked({ ...good, current: "" })).toBe("Type your current password");
    expect(passwordBlocked({ ...good, next: "short", confirm: "short" })).toBe(
      "The new password needs at least 10 characters",
    );
    expect(passwordBlocked({ ...good, confirm: "a brand new passwore" })).toBe("Type the new password again, the same");
    expect(passwordBlocked({ current: "same password", next: "same password", confirm: "same password" })).toBe(
      "The new password is the same as the current one",
    );
    expect(passwordBlocked(good)).toBeNull();
  });

  it("flags a confirmation that doesn't match, once there is one", () => {
    expect(confirmMismatch({ ...good, confirm: "" })).toBeNull();
    expect(confirmMismatch({ ...good, confirm: "a brand" })).toBe("Passwords don’t match");
    expect(confirmMismatch(good)).toBeNull();
  });
});
