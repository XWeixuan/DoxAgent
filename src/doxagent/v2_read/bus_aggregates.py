"""Message-filter dimensions with independent message and body-attempt dates."""
import json
from collections import defaultdict
from .repository import encode


def contributions(db, members):
    result = []
    for ticker, identity in members:
        row = db.execute("SELECT day,source_id,route,json_extract(payload,'$.source.kind') FROM object_current WHERE kind='message' AND ticker=? AND id=?", (ticker,identity)).fetchone()
        dimensions = {"source_id": row[1], "route": row[2], "source_kind": row[3]} if row else {}
        common = {"ticker":ticker,"entity":identity,"dimensions":dimensions}
        result.append({**common,"metric":"bus_messages","day":row[0] if row else "", "value":1 if row else None})
        counts = defaultdict(lambda: [0,0])
        if row:
            for day, succeeded in db.execute("SELECT a.day,json_extract(a.payload,'$.succeeded') FROM object_current l JOIN object_current a ON a.kind='body_attempt' AND a.ticker=l.ticker AND a.parent=l.id WHERE l.kind='message_raw_link' AND l.ticker=? AND l.parent=?", (ticker,identity)):
                counts[day][0] += 1
                counts[day][1] += int(bool(succeeded))
        for metric,index in (("bus_body_attempts",0),("bus_body_succeeded",1)):
            if not counts:
                result.append({**common,"metric":metric,"day":"","value":None})
            for day, values in counts.items():
                result.append({**common,"metric":metric,"day":day,"value":values[index]})
    return result


def seed(db, seq):
    if db.execute("SELECT 1 FROM read_meta WHERE key='bus_aggregates_from'").fetchone():
        return
    totals = defaultdict(int)
    cursor = db.execute("SELECT ticker,id FROM object_current WHERE kind='message'")
    while rows := cursor.fetchmany(100):
        for item in contributions(db, rows):
            if item["value"] is None:
                continue
            dimensions = encode(item["dimensions"])
            db.execute("INSERT OR IGNORE INTO contributions(metric,ticker,entity,day,dimensions,value,valid_from,valid_to) VALUES(?,?,?,?,?,?,?,NULL)",
                       (item["metric"],item["ticker"],item["entity"],item["day"],dimensions,str(item["value"]),seq))
            for day in (item["day"],"*"):
                totals[(item["metric"],item["ticker"],day,dimensions)] += item["value"]
    for key, amount in totals.items():
        db.execute("INSERT OR IGNORE INTO metric_buckets(metric,ticker,day,dimensions,value,valid_from,valid_to) VALUES(?,?,?,?,?,?,NULL)", (*key,str(amount),seq))
    db.execute("INSERT INTO read_meta VALUES('bus_aggregates_from',?)", (str(seq),))
