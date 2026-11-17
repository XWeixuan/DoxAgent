import asyncio,json
from pathlib import Path
from doxagent.message_bus_v2.energy_sources import EnergyHttp
from doxagent.message_bus_v2.energy_catalog import SOURCES,NRC_EVENTS
async def main():
 http=EnergyHttp()
 try:
  async with asyncio.timeout(20):r=await http.fetch(NRC_EVENTS,SOURCES['nrc_event_notifications'])
  Path('/tmp/nrc-report-repair-20261111.html').write_text(r.text);print(json.dumps({'status':r.status,'bytes':len(r.text)}))
 finally:await http.close()
asyncio.run(main())
