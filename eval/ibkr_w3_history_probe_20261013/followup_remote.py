# The runner prepends the read-only imports and helpers from probe_remote.py.
tool_settings=settings.model_copy(update={"ibkr_tws_client_id":381018,"ibkr_tws_timeout_seconds":12})
client=IbkrMarketHistoryClient(tool_settings)
def tool_call(label, period='1d'):
    began=time.monotonic()
    req=ToolRequest(tool_name='ibkr.market_history',ticker='MU',agent_name=list(AgentName)[0],input={'symbol':'MU','period':period,'bar':'5min','outside_rth':True})
    r=client.call(req)
    bars=r.output.get('bars',[])
    value={'kind':'configured_mode_tool','label':label,'configured_market_data_type':tool_settings.ibkr_tws_market_data_type,'latency_ms':round((time.monotonic()-began)*1000,3),'status':str(r.status),'error':r.error.model_dump() if r.error else None,'period':period,'bar_count':len(bars),'first':bars[0] if bars else None,'last':bars[-1] if bars else None,'bars':bars}
    emit(value)
    return value
tool_call('default3_single')
began=time.monotonic()
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    [f.result() for f in [pool.submit(tool_call,'default3_parallel_a'),pool.submit(tool_call,'default3_parallel_b')]]
emit({'kind':'semantic_concurrency','batch_ms':round((time.monotonic()-began)*1000,3),'requests':2,'session_lock':True})
tool_call('existing_1w_can_filter_24h',period='1w')
with IbkrTwsSession(IbkrTwsConfig(host=cfg.host,port=cfg.port,client_id=381019,timeout_seconds=12,market_data_type=3)) as api:
    contract=api.resolve_stock('MU')[0]
    try:
        snapshot=api.market_snapshot(contract,generic_ticks='233')
        emit({'kind':'configured_mode_snapshot','configured_market_data_type':3,'actual_market_data_type':snapshot['market_data_type'],'snapshot':snapshot})
    except Exception as exc:
        emit({'kind':'configured_mode_snapshot','error':str(exc)})
    hist(api,contract,'MU_later_2D',duration='2 D')
    hist(api,contract,'MU_later_86400S')
    emit({'kind':'followup_errors','errors':[asdict(e) for e in api._require_app().api_errors]})
