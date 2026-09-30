"use client";

import { useFormStatus } from "react-dom";

import { Button } from "@/components/ui/button";

export function SubmitButton({
  text,
  pendingText = "Working…",
}: {
  text: string;
  pendingText?: string;
}) {
  const { pending } = useFormStatus();
  return (
    <Button
      className="w-full"
      type="submit"
      disabled={pending}
      aria-disabled={pending}
    >
      {pending ? pendingText : text}
    </Button>
  );
}
