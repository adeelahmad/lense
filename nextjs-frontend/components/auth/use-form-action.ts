"use client";

import { startTransition, useActionState, type FormEvent } from "react";

import type { FormState } from "@/lib/definitions";

/**
 * A server action behind a form, submitted from onSubmit so React doesn't reset the form afterwards: after "Wrong email
 * or password" the email you typed is still there. Pass `action` to the form too: before the page has hydrated the
 * browser then posts to the server action instead of submitting a GET with the password in the address.
 */
export function useFormAction(action: (prev: FormState, data: FormData) => Promise<FormState>) {
  const [state, dispatch, pending] = useActionState(action, undefined);
  const onSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    startTransition(() => dispatch(data));
  };
  return { state, pending, onSubmit, action: dispatch };
}
