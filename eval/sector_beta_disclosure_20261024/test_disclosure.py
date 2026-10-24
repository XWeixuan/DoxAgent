import json,unittest
from datetime import datetime,UTC
from pathlib import Path
import engine as E
import recalculate as R

class DisclosureChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.x=json.loads((R.ROOT/'results.json').read_text(encoding='utf8'))
    def test_coverage_and_identical_fixed_exits(self):
        a=self.x['primary']['trades']['0'];b=self.x['matched_original']['trades']['0']
        self.assertEqual(len(a),15)
        self.assertEqual({t['report_number'] for t in a},set(range(1,17))-{3})
        for new,old in zip(a,b):
            self.assertEqual(new['report_number'],old['report_number'])
            self.assertEqual(new['end'],old['end'])
    def test_overnight_has_real_historical_series_at_disclosure(self):
        rows={t['report_number']:t for t in self.x['primary']['trades']['0']}
        for n in [9,10,16]:
            t=rows[n]
            self.assertEqual(t['stock_entry_venue'],'OVERNIGHT')
            self.assertEqual(t['etf_entry_venue'],'OVERNIGHT')
            self.assertIsNotNone(E.bar(t['ticker'],t['start']))
            self.assertIsNotNone(E.bar(t['etf'],t['start']))
    def test_known_seconds_are_never_rounded_backward(self):
        t=next(t for t in self.x['primary']['trades']['0'] if t['report_number']==9)
        disclosure=datetime.fromisoformat(t['disclosure_utc'])
        self.assertEqual(disclosure.second,11)
        self.assertEqual(t['start']-disclosure.timestamp(),49)
    def test_beta_inputs_precede_every_entry(self):
        for scenario in [self.x['primary'],self.x['matched_original'],self.x['natural_exit']]:
            for t in scenario['trades']['0']:
                self.assertLess(E.dt(t['beta_max_bar_end_utc']).timestamp(),t['start'])
    def test_both_legs_and_costs_reconcile_independently(self):
        for key,trades in self.x['primary']['trades'].items():
            for t in trades:
                sign=1 if t['direction']=='LONG' else -1
                expected=sign*t['stock_notional']*(t['stock_exit']/t['stock_entry']-1)-sign*t['hedge_notional']*(t['etf_exit']/t['etf_entry']-1)
                self.assertAlmostEqual(expected,t['gross_pnl'],places=8)
                self.assertAlmostEqual(t['net_pnl'],expected-t['total_cost'],places=8)
                self.assertAlmostEqual(t['hedge_notional'],10000*t['beta']*float(key),places=8)
            self.assertAlmostEqual(sum(t['net_pnl'] for t in trades),self.x['primary']['summaries'][key]['net_pnl'])
    def test_previous_14_trade_results_reproduce_exactly(self):
        prior=json.loads((R.ROOT/'prior_results.json').read_text(encoding='utf8'))
        common=self.x['sensitivities']['original_14_matched']['summaries']
        for k in ['0','0.66','1.0']:
            self.assertAlmostEqual(common[k]['net_pnl'],prior['primary']['summaries'][k]['net_pnl'],places=8)
    def test_exit_rule_changes_only_the_expected_two_cases(self):
        a=self.x['primary']['trades']['0'];b=self.x['natural_exit']['trades']['0']
        self.assertEqual({u['report_number'] for u,v in zip(a,b) if u['end']!=v['end']},{8,11})
        for u,v in zip(a,b):self.assertEqual(u['start'],v['start'])
    def test_delay_applies_to_both_legs_and_has_no_later_beta_leak(self):
        a=self.x['primary']['trades']['0'];b=self.x['sensitivities']['one_minute_after_disclosure']['trades']['0']
        for u,v in zip(a,b):
            self.assertEqual(v['start']-u['start'],60)
            self.assertEqual(v['end'],u['end'])
            self.assertEqual(v['beta'],u['beta'])
            self.assertIsNotNone(E.bar(v['ticker'],v['start']))
            self.assertIsNotNone(E.bar(v['etf'],v['start']))
if __name__=='__main__':unittest.main()
