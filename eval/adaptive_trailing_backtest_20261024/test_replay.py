"""Causality and stop-state regression checks for this offline experiment."""
import unittest
from collections import deque
from unittest.mock import patch
import replay as r

class Path:
    def __init__(self,points,invalid=()):self.points=points;self.invalid=set(invalid)
    def get(self,s,t):
        if t in self.invalid:return None
        pnl=self.points[min((t-r.START)//5,len(self.points)-1)]
        price=100-pnl/100 if s=='RKLB' else 100
        return dict(timestamp=r.iso(t-1),bid_price=price,ask_price=price,bid_size=10,ask_size=10)

class Checks(unittest.TestCase):
    def simulate(self,points,invalid=(),atr=10):
        pos=dict(start=r.START,stock_entry=100,etf_entry=100,qs=-100,qe=0,hedged=False,entry_commission=1)
        with patch.object(r,'warmup_atr',return_value=(deque([atr]*20,maxlen=20),0,21)):
            return r.replay_position(Path(points,invalid),pos,r.START+(len(points)-1)*5,.4,2.25)
    def test_causal_prefix_and_no_preentry_peak(self):
        a=self.simulate([0]*8+[1000]*4+[0]*10)
        b=self.simulate([0]*8+[2000]*4+[0]*10)
        self.assertEqual(a['trace'][:8],b['trace'][:8]);self.assertIsNone(a['trace'][0]['stop_line_net_usd'])
    def test_confirmation_delays_and_real_quote_fill(self):
        out=self.simulate([1000]*5+[400]*8)
        self.assertIsNotNone(out['trigger']);t=out['trigger']
        self.assertGreaterEqual(t['time']-r.ts(out['activation_utc']),30)
        self.assertGreaterEqual(t['confirmed_breaches'],5)
        self.assertLess(t['net_pnl'],t['stop_line_net_usd'])
    def test_invalid_latest_cannot_trigger(self):
        points=[1000]*5+[400]*8
        out=self.simulate(points,[r.START+i*5 for i in range(5,len(points))])
        self.assertIsNone(out['trigger']);self.assertEqual(out['status'],'INCOMPLETE_DATA')
    def test_atr_expansion_cannot_loosen(self):
        out=self.simulate([1000]*5+[800,1100]*35,atr=1)
        lines=[x['stop_line_net_usd'] for x in out['trace'] if x['stop_line_net_usd'] is not None]
        self.assertEqual(lines,sorted(lines))
    def test_spike_median_filter(self):
        out=self.simulate([0,0,2000,0,0,0,0])
        self.assertIsNone(out['activation_utc'])
    def test_age_and_spread_filter(self):
        e=Path([0]).get('RKLB',r.START)
        self.assertTrue(r.valid(e,r.START)[0]);self.assertFalse(r.valid(e,r.START+10)[0])
        e['ask_price']=120;self.assertFalse(r.valid(e,r.START)[0])

if __name__=='__main__':unittest.main()
