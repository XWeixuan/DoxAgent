"""Small capacity samples and the existing Bus operational alert channel."""
from contextlib import closing
from datetime import UTC,datetime,timedelta
import json
import shutil


def capacity(source, *, now=None, free=None):
    from doxagent.message_bus_v2.schema import OperationalAlert,AlertSeverity
    now=now or datetime.now(UTC);free=shutil.disk_usage(source.path.parent).free if free is None else free
    key='storage:production-capacity'
    with closing(source.connection(write=True)) as db,db:
        db.execute('CREATE TABLE IF NOT EXISTS v2_storage_capacity_samples(day TEXT PRIMARY KEY,at TEXT,free INTEGER)')
        db.execute('INSERT OR IGNORE INTO v2_storage_capacity_samples VALUES(?,?,?)',(now.date().isoformat(),now.isoformat(),free))
        db.execute('DELETE FROM v2_storage_capacity_samples WHERE day<?',((now-timedelta(days=30)).date().isoformat(),))
        old=db.execute('SELECT at,free FROM v2_storage_capacity_samples WHERE at<=? ORDER BY at DESC LIMIT 1',((now-timedelta(days=7)).isoformat(),)).fetchone()
        rate=max(0,(old[1]-free)/(now-datetime.fromisoformat(old[0])).total_seconds()) if old else 0
        days_left=free/rate/86400 if rate else None
        low=free<20*1024**3 or days_left is not None and days_left<7
        row=db.execute('SELECT data_json FROM operational_alerts WHERE alert_key=?',(key,)).fetchone()
        previous=OperationalAlert.model_validate_json(row[0]) if row else None
        if low:
            value=OperationalAlert(alert_key=key,severity=AlertSeverity.ERROR if free<10*1024**3 else AlertSeverity.WARNING,
                code='PRODUCTION_DISK_CAPACITY',message=f'生产盘剩余 {free/1024**3:.1f} GiB；请查看存储 owner 回执与容量日样本。',
                metadata=dict(free_bytes=free,days_left=days_left),first_seen_at=previous.first_seen_at if previous else now,last_seen_at=now,
                alert_id=previous.alert_id if previous else 'alert-production-disk-capacity',repeat_count=previous.repeat_count+1 if previous else 1)
            db.execute('INSERT INTO operational_alerts(alert_id,alert_key,severity,resolved_at,last_seen_at,data_json) VALUES(?,?,?,?,?,?) ON CONFLICT(alert_key) DO UPDATE SET severity=excluded.severity,resolved_at=NULL,last_seen_at=excluded.last_seen_at,data_json=excluded.data_json',
                (value.alert_id,key,value.severity.value,None,now.isoformat(),value.model_dump_json()))
        elif previous and previous.resolved_at is None:
            value=previous.model_copy(update=dict(resolved_at=now,last_seen_at=now,metadata=dict(free_bytes=free,days_left=days_left)))
            db.execute('UPDATE operational_alerts SET resolved_at=?,last_seen_at=?,data_json=? WHERE alert_key=?',(now.isoformat(),now.isoformat(),value.model_dump_json(),key))
    return dict(free_bytes=free,days_left=days_left,alert=low)
