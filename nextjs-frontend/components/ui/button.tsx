"use client";

import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** Aladdin pill buttons. One primary per view; hover darkens, press scales to .98. */
export const buttonVariants = cva(
  "inline-flex shrink-0 select-none items-center justify-center gap-2 whitespace-nowrap rounded-pill border font-bold transition-colors duration-fast ease-standard active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "border-transparent bg-blue text-white hover:bg-blue-dark",
        secondary: "border-border bg-background text-blue hover:bg-blue-surface",
        ghost: "border-transparent bg-transparent text-fg-strong hover:bg-surface-neutral",
        approve: "border-transparent bg-green text-white hover:bg-green-dark",
        danger: "border-transparent bg-red text-white hover:bg-red-dark",
        "danger-ghost": "border-transparent bg-transparent text-red-dark hover:bg-red-surface",
        link: "h-auto border-transparent px-0 text-fg-accent hover:underline active:scale-100",
      },
      size: {
        xs: "h-7 px-3 text-[12.5px]",
        sm: "h-8 px-3.5 text-[13px]",
        md: "h-10 px-5 text-[14px]",
        lg: "h-12 px-6 text-[15px]",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  icon?: ReactNode;
  /** Shown on hover and focus when the button is disabled: say why, and who can do it. */
  disabledReason?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, asChild, icon, children, disabledReason, disabled, onClick, type, ...props },
  ref,
) {
  const Comp = asChild ? Slot : "button";
  // A disabled button can't take focus, so one that explains itself stays focusable and is marked aria-disabled.
  const explained = Boolean(disabled && disabledReason);
  const inner = (
    <Comp
      ref={ref}
      type={asChild ? undefined : (type ?? "button")}
      className={cn(
        buttonVariants({ variant, size }),
        explained && "cursor-not-allowed opacity-50 active:scale-100",
        className,
      )}
      disabled={disabled && !explained ? true : undefined}
      aria-disabled={disabled || undefined}
      data-variant={variant ?? "secondary"}
      onClick={disabled ? (e: React.MouseEvent<HTMLButtonElement>) => e.preventDefault() : onClick}
      {...props}
    >
      {asChild ? (
        children
      ) : (
        <>
          {icon}
          {children}
        </>
      )}
    </Comp>
  );
  return explained ? <Tooltip content={disabledReason}>{inner}</Tooltip> : inner;
});

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  active?: boolean;
  size?: number;
}

/** A round icon-only button; `label` is its accessible name and tooltip. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, active, size = 36, className, children, style, ...props },
  ref,
) {
  return (
    <Tooltip content={label}>
      <button
        ref={ref}
        type="button"
        aria-label={label}
        aria-pressed={active}
        style={{ width: size, height: size, ...style }}
        className={cn(
          "inline-grid shrink-0 place-items-center rounded-full text-fg-secondary transition-colors duration-fast hover:bg-surface-neutral disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:size-[18px]",
          active && "bg-blue-surface text-blue",
          className,
        )}
        {...props}
      >
        {children}
      </button>
    </Tooltip>
  );
});
