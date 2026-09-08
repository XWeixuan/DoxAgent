import { Button } from "@/components/ui/button";
import { operationsFor, type PendingCommand } from "@/core/operations";
import { useRuntime } from "@/core/runtime";

export function OperationNotice({ entry }: { entry: PendingCommand }) {
  const operations = operationsFor(useRuntime());
  return (
    <div
      className={`operation-note ${entry.phase === "failed" || entry.phase === "conflict" ? "error-text" : ""}`}
      role="status"
    >
      {entry.message}
      {["unknown", "waiting", "conflict", "failed"].includes(entry.phase) && (
        <Button
          size="sm"
          variant="link"
          onClick={() => void operations.resume(entry)}
        >
          {entry.phase === "waiting"
            ? "查看结果"
            : entry.phase === "conflict"
              ? "重新提交"
              : "重试"}
        </Button>
      )}
    </div>
  );
}
