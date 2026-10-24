"""Numerical invariants independent of the report's narrative conclusions."""
import copy,json,unittest
from datetime import datetime,UTC
from pathlib import Path
import backtest as b

class BacktestChecks(unittest.TestCase):
    def test_hedge_sign_and_relative_beta_not_dollar_neutral(self):
        r=dict(direction='SHORT',beta=2.,stock_entry=100.,stock_exit=100.,etf_entry=100.,etf_exit=101.,overnights=0)
        v=b.trade(r,1.)
        self.assertEqual(v['hedge_notional'],20000.)
        self.assertAlmostEqual(v['gross_pnl'],200.)
        self.assertAlmostEqual(v['net_pnl'],196.)
        r['direction']='LONG';v=b.trade(r,1.)
        self.assertAlmostEqual(v['gross_pnl'],-200.)
    def test_same_gross_preserves_capital_budget(self):
        r=dict(direction='LONG',beta=2.,stock_entry=100.,stock_exit=103.,etf_entry=100.,etf_exit=101.,overnights=0)
        for k in b.LEVELS:self.assertAlmostEqual(b.trade(r,k,equal_gross=True)['gross_notional'],10000.)
    def test_overnight_borrow_not_added_to_intraday_trades(self):
        r=dict(direction='SHORT',beta=2.,stock_entry=100.,stock_exit=100.,etf_entry=100.,etf_exit=100.,overnights=0)
        self.assertEqual(b.trade(r,1.)['borrow_cost'],0)
        r['overnights']=1
        self.assertAlmostEqual(b.trade(r,1.)['borrow_cost'],10000*.02/360)
    def test_drawdown_uses_intratrade_path(self):
        start=int(datetime(2026,10,6,15,tzinfo=UTC).timestamp());old=b.DATA
        b.DATA={'TEST':{start+60*i:{'open':p} for i,p in enumerate([100,120,90,100])},'BENCH':{start+60*i:{'open':100} for i in range(4)}}
        t=dict(start=start,end=start+180,ticker='TEST',etf='BENCH',stock_shares=100,etf_shares=0,stock_entry=100,etf_entry=100,stock_notional=10000,gross_notional=10000,entry_commission=0,borrow_cost=0,funding_cost=0,net_pnl=0,gross_pnl=0,commission=0,total_cost=0)
        try:
            s=b.portfolio([t]);self.assertAlmostEqual(s['maximum_drawdown_usd'],3000)
            self.assertEqual(s['net_pnl'],0)
        finally:b.DATA=old
    def test_beta_only_uses_completed_pre_entry_sessions(self):
        rows={r['intent_id']:r for r in b.AUDIT}
        for f in b.BETA['fits']:
            r=rows[f['intent_id']]
            for fit in [f['primary'],*f['alternates'].values()]:
                self.assertLess(datetime.fromisoformat(fit['max_bar_end_utc']),datetime.fromisoformat(r['entry_at']))
                self.assertGreaterEqual(fit['observations'],fit['sessions']*8)
        for selection in b.BETA['selection'].values():
            self.assertEqual(selection['frozen_before'],'2026-10-06')
            self.assertTrue(all(f['last_day']<'2026-10-06' for f in selection['candidates']))
    def test_real_trade_reconciliation_and_matched_times(self):
        x=json.loads((b.ROOT/'results.json').read_text(encoding='utf8'))
        for k,trades in x['primary']['trades'].items():
            self.assertEqual(len(trades),14)
            self.assertFalse({3,9}&{t['report_number'] for t in trades})
            for t in trades:
                direction=1 if t['direction']=='LONG' else -1
                independent=t['stock_notional']*direction*(t['stock_exit']/t['stock_entry']-1)-direction*t['hedge_notional']*(t['etf_exit']/t['etf_entry']-1)
                self.assertAlmostEqual(independent,t['gross_pnl'],places=8)
                self.assertAlmostEqual(t['net_pnl'],t['gross_pnl']-t['total_cost'],places=8)
                self.assertIsNotNone(b.bar(t['ticker'],t['start']))
                self.assertIsNotNone(b.bar(t['etf'],t['start']))
                self.assertIsNotNone(b.bar(t['ticker'],t['end']))
                self.assertIsNotNone(b.bar(t['etf'],t['end']))
            s=x['primary']['summaries'][k]
            self.assertAlmostEqual(sum(t['net_pnl'] for t in trades),s['net_pnl'])
            self.assertEqual(s['financing_debit_required'],0)

if __name__=='__main__':unittest.main()
