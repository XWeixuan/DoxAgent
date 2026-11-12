import { useState } from "react";
import { createRoot } from "react-dom/client";
import type { Response, ReadContext } from "@contract";
import { PageTitle } from "../../src/components/page-kit";
import { context } from "../fixtures/wire";
import "../../src/styles.css";
import "../../src/business.css";
function Fixture() {
 const [caught,setCaught]=useState(false);
 const response:Response<ReadContext>={data:{...context("CURRENT_TRADING_DAY"),projection:[{source:"runtime",head:20,checkpoint:caught?20:10,checked_at:"2026-11-12T00:01:00Z",source_event_at:"2026-11-12T00:00:00Z",oldest_pending_at:caught?null:"2026-11-12T00:00:01Z",historical_gaps:17204,error:null}]},meta:{contract_version:"2.0.0-draft.2",workflow_generation:"V2",request_id:"fixture",view_id:"view",scope_key:"fixture",as_of:"2026-11-12T00:01:00Z",representation_revision:"1",freshness:"STALE",refresh_error:null}};
 return <main><PageTitle title="MU 运行状态" readContext={response} refresh={async()=>setCaught(true)}/></main>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
