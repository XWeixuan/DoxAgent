import asyncio,json,os,sys
from pathlib import Path
from datetime import UTC,datetime
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters,stdio_client
from doxagent.data_runtime.policy import DataCapabilityCodec,DataToolPolicyRegistry
from doxagent.codex_runtime.schema import CodexD3Node,CodexD3AgentRole

async def main():
    base=Path(__file__).resolve().parent
    run=base/"live-data-o3-mu"; run.mkdir(exist_ok=True)
    codec=DataCapabilityCodec(os.urandom(32).hex())
    allowed=DataToolPolicyRegistry().allowed_tools(CodexD3Node.O3_BUILD,CodexD3AgentRole.O3)
    assert "yfinance.sell_side_consensus" in allowed
    token=codec.issue(run_id=run.name,node_id=CodexD3Node.O3_BUILD,node_attempt_id="live-check-1",
        agent_role=CodexD3AgentRole.O3,ticker="MU",cutoff_at=datetime.now(UTC),
        enabled_tool_ids={"yfinance.sell_side_consensus"})
    env={**os.environ,"DOXAGENT_DATA_MCP_CAPABILITY":token,
         "DOXAGENT_DATA_MCP_PUBLIC_KEY":codec.public_key,
         "DOXAGENT_OBSERVATION_CONTROL_ROOT":str(base/".control"/run.name/"live-check-1"),
         "PYTHONPATH":str(Path.cwd()/"src")}
    params=StdioServerParameters(command=sys.executable,args=["-m","doxagent.mcp.data_server"],env=env,cwd=run)
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            result=await session.call_tool("yfinance_sell_side_consensus",arguments={})
            payload=result.model_dump(mode="json")
            (base/"live_data_result.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf8")
            structured=result.structured_content or {}
            print(json.dumps({"is_error":result.is_error,"execution_status":structured.get("execution_status"),
                "data_status":structured.get("data_status"),"keys":list(structured)},ensure_ascii=False))
asyncio.run(main())
