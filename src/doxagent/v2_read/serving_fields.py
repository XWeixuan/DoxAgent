"""Projector/API state fields. This is not a mirror of all captured source rows.

Source business repositories and Worker/W3 frozen inputs are never modified.
"""

# Small control and execution records are used by stateful projection and PNL.
CONTROL={
    'source_definitions','ticker_source_bindings','ticker_monitoring_states',
    'poll_states','ir_endpoint_states','v2_ticker_control','v2_control_ack',
    'initialization_repair_incidents','initialization_repair_rounds',
    'te_executions','te_jobs','te_attempts','te_fills','te_events','te_allocations',
    'te_lots','te_lot_adjustments',
    'article_body_results','poll_runs','poll_captures',
}
CASE_FIELDS={
    'case_id','runtime_mode','version_pin','trading_date','w1_final','w1_extraction','w2_alignment',
    'w2_alignment_status','w2_alignment_error','w2_contract_version','first_round_tags',
    'orchestration_revision','w2_final','w2_round1','w2_skipped','w3_result',
    'w3_contract_version','w3_trade_decision','w3_operations_result','w3_operations_status','w3_operations_error',
    'error_code','error_message','updated_at','status',
}
VALUE_NAMES={
    'policy_authorization_head','policy_watch_applied','policy_watch_validation',
    'policy_authorization_applied','policy_consumptions','policy_consumption_migration',
    'w3_operations_input','w3_global_input','w3_operations_applied',
}


def select(kind,identity,value):
    if value is None or not kind.startswith('native:'):return value
    table=kind[7:]
    if table in CONTROL:return value
    if table=='runtime_v2_cases':
        result={k:v for k,v in value.items() if k in CASE_FIELDS}
        result['source']={'snapshot':{'ticker':value.get('source',{}).get('snapshot',{}).get('ticker')}}
        result['frozen_inputs']={k:v for k,v in value.get('frozen_inputs',{}).items()
                                 if k in {'policy_watch_followup','w3_execution_state'}}
        return result
    if table=='runtime_v2_turns':
        return {k:v for k,v in value.items() if k in {'turn_id','case_id','output'}}
    if table=='runtime_v2_w3_cases':
        return {k:v for k,v in value.items() if k in {'case_id','mode','status','ticker'}}
    if table=='runtime_v2_candidates':
        return {k:v for k,v in value.items() if k in {'case_id','candidate','provisional_event_id','trading_date','snapshot_version'}}
    if table=='runtime_tasks':
        return {k:v for k,v in value.items() if k in {'id','ticker','kind','status'}}
    if table=='runtime_values':
        namespace=identity.partition(':')[0]
        if namespace not in VALUE_NAMES:return None
        if namespace in {'w3_global_input','w3_operations_input'}:
            return {k:v for k,v in value.items() if k in {'watch','pins'}}
        return value
    if table=='activation_revisions':
        return {k:v for k,v in value.items() if k in {'revision_id','ticker','base_revision'}}
    if table=='initialization_runs':
        return {k:v for k,v in value.items() if k in {'initialization_id','ticker','status','state_seq','manual_resume_required','error','control_operation_id'}}
    if table=='initialization_nodes':
        return {**{k:v for k,v in value.items() if k in {'key','block','status'}},
                'result':{'quality_annotations':(value.get('result') or {}).get('quality_annotations',[])}}
    if table=='initialization_events':
        if value.get('kind') not in {'node.started','node.settled','native.reconciled','manual.adopt'}:return None
        return {'kind':value['kind'],'created_at':value['created_at'],
                'payload':{'node':value.get('payload',{}).get('node')}}
    if table=='standard_messages':
        return {k:v for k,v in value.items() if k in {
            'standard_message_id','revision','url','title','source_id','binding_id',
            'published_at','collected_at','normalized_at','body_ref','metadata',
            'source_name','source','publisher_name','resolved_domain',
        }}
    if table=='stream_items':
        return {k:v for k,v in value.items() if k in {'stream_item_id','ticker','published_at','stream_offset'}}
    return None
