import json,sqlite3
ids=['unmannedairspace_counteruas','spacenews_rss','venturebeat_software','register_software_cloud_security','nbis_official_actor_rss','airspaceforces_space','siliconangle_software','wccftech_hardware','siliconangle_ai','ans_nuclear_newswire','quantum_insider','lakehouse_platform_official_rss','aws_platform_marketplace_rss','power_magazine_energy','corporate_ir_press_releases']
c=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True,timeout=3)
out=[]
for sid in ids:
 r=c.execute('select data_json from source_definitions where source_id=?',(sid,)).fetchone()
 if not r:out.append({'source_id':sid,'missing':True});continue
 s=json.loads(r[0]);s={k:s[k] for k in ['source_id','version','display_name','entry_url','adapter_ref','content_language','acquisition_mode','content_enrichment_mode','default_polling_config','scheduler_constraints','scheduler_group','default_parameters']}
 s['bindings']=[json.loads(r[0]) for r in c.execute('select data_json from ticker_source_bindings where source_id=? and enabled=1',(sid,))]
 out.append(s)
print(json.dumps(out,ensure_ascii=False))
