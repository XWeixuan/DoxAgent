import json
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

data = json.loads((Path(__file__).parent/'sample.json').read_text(encoding='utf-8-sig'))
ticker = sys.argv[1]
for n, case in enumerate((x for x in data['sample'] if x['ticker']==ticker), 1):
    print(f"\n{'='*22} {ticker}-{n} {case['case_id']} {'='*22}")
    print('created',case['created_at'],'source_id',case['source_message_id'],'route',case['route'])
    print('title:',case['source'].get('title'))
    print('W1:',case['w1_final'])
    print('W2:',case['w2_final'])
    w3=case['w3_result'] or {}
    print('W3 novelty:',w3.get('novelty'),'W3 policy:',w3.get('policy'))
    print('R3:',case['w1_extraction'])
    print('PROVISIONS:',[(x['event_id'],x['payload'].get('candidate'),x['payload'].get('originating_node')) for x in case['candidates']])
    print('BODY:',case['source'].get('body'))
