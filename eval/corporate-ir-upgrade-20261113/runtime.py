"""Read-only runtime/schema readiness, safe to execute in any message/API owner."""
import json
import os
from doxagent.message_bus_v2.corporate_ir.schema import IrEndpointSpec
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.settings import DoxAgentSettings

repo = MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path, initialize=False)
source = repo.get_source('corporate_ir_press_releases')
specs = [IrEndpointSpec.model_validate(e) for e in source.default_parameters['endpoints']]
print(json.dumps(dict(build=os.environ.get('DOXAGENT_BUILD_COMMIT'), source_version=source.version,
    endpoint_count=len(specs), ionq_binding=repo.get_binding('IONQ:'+source.source_id).enabled,
    parsers={s.endpoint_id: s.parser_ref for s in specs if s.owner_ticker in {'IONQ','SNOW','ONDS'}})))
