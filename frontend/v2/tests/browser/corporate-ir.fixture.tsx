import { createRoot } from "react-dom/client";
import { IrEndpoints } from "../../src/components/ir-endpoints";
import "../../src/styles.css";
import "../../src/business.css";
import "../../src/refinement.css";

createRoot(document.getElementById("root")!).render(
  <main style={{ maxWidth: 1200, margin: "24px auto", padding: "0 16px" }}>
    <div className="binding-workspace">
      <nav className="binding-channels">
        <button>
          <strong>企业 IR / Press Releases</strong>
          <span>API · 30s / 次</span>
        </button>
      </nav>
      <section className="binding-config-panel">
        <h2>企业 IR / Press Releases</h2>
        <IrEndpoints
          endpoints={[
            {
              endpoint_id: "cohr.corporate",
              name: "Coherent corporate releases",
              url: "https://ir.coherent.com/news-events/corporate-releases",
              method: "html",
              status: "failed",
              last_success_at: null,
              last_attempt_at: "2026-10-20T11:00:00Z",
              next_target_at: null,
              latency_ms: 12000,
              http_status: 503,
              error: "HTTP_503：当前入口失败，其他入口继续轮询",
            },
            {
              endpoint_id: "cohr.financial",
              name: "Coherent financial releases",
              url: "https://ir.coherent.com/news-events/financial-releases",
              method: "html",
              status: "succeeded",
              last_success_at: "2026-10-20T11:00:01Z",
              last_attempt_at: "2026-10-20T11:00:00Z",
              next_target_at: "2026-10-20T11:00:30Z",
              latency_ms: 350,
              http_status: 304,
              error: null,
            },
          ]}
        />
      </section>
    </div>
  </main>,
);
