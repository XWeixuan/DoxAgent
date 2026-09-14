"""Seed derived graph path buckets without changing any public version identities."""
import json
from collections import defaultdict


def migrate(db, seq):
    if db.execute("SELECT 1 FROM read_meta WHERE key='graph_paths_from'").fetchone():
        return
    totals = defaultdict(int)
    rows = db.execute("SELECT m.ticker,m.parent,m.day,json_extract(m.payload,'$.case_id'),g.payload "
                      "FROM object_current m JOIN object_current g ON g.kind='graph_case' AND g.ticker=m.ticker "
                      "AND g.id=json_extract(m.payload,'$.case_id') WHERE m.kind='graph_member'")
    for ticker,node,day,case_id,raw in rows:
        for origin,target in json.loads(raw)["edges"]:
            dimensions = json.dumps({"node":node,"from":origin,"to":target},sort_keys=True,separators=(",",":"))
            entity = case_id+":"+node+":"+origin+":"+target
            db.execute("INSERT OR IGNORE INTO contributions(metric,ticker,entity,day,dimensions,value,valid_from,valid_to) VALUES('graph_paths',?,?,?,?, '1',?,NULL)",
                       (ticker,entity,day,dimensions,seq))
            for selected in (day,"*"):
                totals[(ticker,selected,dimensions)] += 1
    for (ticker,day,dimensions),amount in totals.items():
        db.execute("INSERT OR IGNORE INTO metric_buckets(metric,ticker,day,dimensions,value,valid_from,valid_to) VALUES('graph_paths',?,?,?,?,?,NULL)",
                   (ticker,day,dimensions,str(amount),seq))
    db.execute("INSERT INTO read_meta VALUES('graph_paths_from',?)", (str(seq),))


def contributions(db, cases):
    output = []
    for ticker,identity in cases:
        prefix = identity + ":"
        for (entity,) in db.execute("SELECT DISTINCT entity FROM contributions_current WHERE metric='graph_paths' AND ticker=? AND entity>=? AND entity<?", (ticker,prefix,identity+";")):
            output.append({"metric":"graph_paths","ticker":ticker,"entity":entity,"day":"","value":None})
        row = db.execute("SELECT payload FROM object_current WHERE kind='graph_case' AND ticker=? AND id=?", (ticker,identity)).fetchone()
        if not row:
            continue
        edges = json.loads(row[0])["edges"]
        for node,day in db.execute("SELECT parent,day FROM object_current WHERE kind='graph_member' AND ticker=? AND json_extract(payload,'$.case_id')=?", (ticker,identity)):
            for origin,target in edges:
                output.append({"metric":"graph_paths","ticker":ticker,"entity":identity+":"+node+":"+origin+":"+target,
                               "day":day,"dimensions":{"node":node,"from":origin,"to":target},"value":1})
    return output
