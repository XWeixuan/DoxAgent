import type { ComponentProps } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";

export function LoadMore({
  children,
  disabled,
  ...props
}: ComponentProps<typeof Button>) {
  return (
    <div className="message-load-more">
      <Button
        {...props}
        variant="ghost"
        disabled={disabled}
        aria-busy={disabled || undefined}
      >
        <ChevronDown aria-hidden="true" />
        {disabled ? "正在加载…" : children}
      </Button>
    </div>
  );
}
