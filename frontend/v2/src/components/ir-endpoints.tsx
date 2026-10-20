import type { IrEndpointStatus } from "@contract";
import { formatInstant } from "@/core/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export function IrEndpoints({ endpoints }: { endpoints?: IrEndpointStatus[] }) {
  if (!endpoints?.length) return null;
  return (
    <details open>
      <summary>内部入口（{endpoints.length}）</summary>
      <Table className="binding-endpoints">
        <TableHeader>
          <TableRow>
            <TableHead>入口</TableHead>
            <TableHead>方式</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>最近成功</TableHead>
            <TableHead>延迟</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {endpoints.map((endpoint) => (
            <TableRow key={endpoint.endpoint_id}>
              <TableCell>
                <a href={endpoint.url} target="_blank" rel="noreferrer">
                  {endpoint.name}
                </a>
                {endpoint.error && (
                  <small role="status">{endpoint.error}</small>
                )}
              </TableCell>
              <TableCell>{endpoint.method.toUpperCase()}</TableCell>
              <TableCell>
                {
                  {
                    succeeded: "正常",
                    failed: "失败",
                    deferred: "延期",
                    never_polled: "尚未轮询",
                  }[endpoint.status]
                }
              </TableCell>
              <TableCell>
                {endpoint.last_success_at
                  ? formatInstant(endpoint.last_success_at)
                  : "—"}
              </TableCell>
              <TableCell>{(endpoint.latency_ms / 1000).toFixed(2)}s</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </details>
  );
}
