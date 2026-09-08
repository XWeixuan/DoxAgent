import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App, ErrorBoundary } from "./app";
import { createAuth } from "./core/auth";
import { createRuntime, RuntimeProvider } from "./core/runtime";
import "./styles.css";
import "./business.css";
import "./refinement.css";
const root = createRoot(document.getElementById("root")!);
root.render(
  <div className="boot-loading" role="status">
    正在打开 DoxAgent…
  </div>,
);
async function boot() {
  const auth = await createAuth();
  const runtime = createRuntime(auth);
  root.render(
    <ErrorBoundary>
      <RuntimeProvider runtime={runtime}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </RuntimeProvider>
    </ErrorBoundary>,
  );
}
void boot().catch((error) =>
  root.render(
    <div className="boot-error">
      <h1>暂时无法打开工作空间</h1>
      <p>{error instanceof Error ? error.message : "连接失败"}</p>
      <button onClick={() => location.reload()}>重试连接</button>
    </div>,
  ),
);
