import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium transition-colors before:content-['']",
  {
    variants: {
      variant: {
        default: "border-zinc-100 bg-zinc-100 text-zinc-950",
        secondary: "border-zinc-700 bg-zinc-800 text-zinc-200",
        destructive: "border-zinc-100 bg-zinc-100 text-zinc-950 before:h-1.5 before:w-1.5 before:bg-zinc-950",
        outline: "border-zinc-600 bg-zinc-900 text-zinc-300",
        success: "border-zinc-500 bg-zinc-950 text-zinc-200 before:h-1.5 before:w-1.5 before:rounded-full before:bg-zinc-200",
        warning: "border-zinc-300 bg-zinc-800 text-white before:h-1.5 before:w-1.5 before:bg-white",
        standby: "border-zinc-700 bg-zinc-900 text-zinc-500 before:h-1.5 before:w-1.5 before:rounded-full before:border before:border-zinc-500",
      },
    },
    defaultVariants: { variant: "default" },
  },
)

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
