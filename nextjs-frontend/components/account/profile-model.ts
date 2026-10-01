/**
 * Changing your own password (docs/api.md): the current password, then the new one twice. Pure, so it can be tested
 * without a browser.
 */
import { PASSWORD_MIN_LENGTH } from "@/lib/definitions";

export type PasswordDraft = { current: string; next: string; confirm: string };

/** Why the form can't be sent yet (for the button's reason), or null when it can. */
export function passwordBlocked(d: PasswordDraft): string | null {
  if (!d.current) return "Type your current password";
  if ([...d.next].length < PASSWORD_MIN_LENGTH)
    return `The new password needs at least ${PASSWORD_MIN_LENGTH} characters`;
  if (d.confirm !== d.next) return "Type the new password again, the same";
  if (d.next === d.current) return "The new password is the same as the current one";
  return null;
}

/** The message under the confirmation field, once there is something to compare. */
export function confirmMismatch(d: PasswordDraft): string | null {
  return d.confirm && d.confirm !== d.next ? "Passwords don’t match" : null;
}
