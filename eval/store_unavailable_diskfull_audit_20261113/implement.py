from pathlib import Path
b=Path('C:/DoxProjectionRecovery/src/doxagent/v2_read')
(b/'recovery.py').write_text('''"""Only explicitly transient storage failures may retry without operator action."""
import errno
import sqlite3


def retryable_storage(exc):
    if isinstance(exc, OSError):
        return exc.errno == errno.ENOSPC
    if isinstance(exc, sqlite3.OperationalError):
        return (getattr(exc, "sqlite_errorcode", 0) & 255) in (
            sqlite3.SQLITE_FULL, sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
        )
    return False


def retry_delay(attempt):
    return (1, 2, 5, 10, 30)[min(attempt, 4)]
''',encoding='utf8')
p=b/'projector.py';s=p.read_text();s=s.replace('from .outbox import SourceOutbox','from .recovery import retryable_storage\nfrom .outbox import SourceOutbox')
s=s.replace('                reason = str(exc) if', '                if retryable_storage(exc) or (isinstance(exc, sqlite3.OperationalError) and getattr(exc, "sqlite_errorcode", 0)):\n                    raise\n                reason = str(exc) if',1)
s=s.replace('except (ValueError, KeyError, TypeError, OSError, sqlite3.OperationalError) as exc:', 'except (OSError, sqlite3.OperationalError):\n                raise  # Infrastructure failure is never a successfully skipped receipt.\n            except (ValueError, KeyError, TypeError) as exc:',1)
old='''                with self.store.connect(write=True) as db:
                    db.execute("UPDATE gaps SET attempts=attempts+1,next_attempt_at=? WHERE source=? AND event=?",
                               (instant(now + timedelta(seconds=delay)),gap[0],gap[1]))
'''
s=s.replace(old,'')
s=s.replace('''                    if event is None:
                        continue
''','''                    if event is None:
                        raise ValueError("SOURCE_RECEIPT_MISSING")
''')
s=s.replace('''                except (ValueError, KeyError, TypeError, OSError, sqlite3.OperationalError):
                    continue
''','''                except (OSError, sqlite3.OperationalError):
                    raise
                except (ValueError, KeyError, TypeError):
                    with self.store.connect(write=True) as db:
                        db.execute("UPDATE gaps SET attempts=attempts+1,next_attempt_at=? WHERE source=? AND event=?",
                                   (instant(now + timedelta(seconds=delay)),gap[0],gap[1]))
''')
p.write_text(s,encoding='utf8')
p=b/'cli.py';s=p.read_text();s=s.replace('            next_report = time.monotonic() + 60','            from .recovery import retryable_storage, retry_delay\n            failures = 0\n            next_report = time.monotonic() + 60',1);s=s.replace('                    processed = worker.tick(limit=args.limit)','                    processed = worker.tick(limit=args.limit)\n                    failures = 0',1)
a=s.index('                except sqlite3.OperationalError as exc:',s.index('with WriterLock(Path(str(store.path)'))
z=s.index('                if args.once:',a)
s=s[:a]+'''                except (sqlite3.OperationalError, OSError) as exc:
                    if args.once or not retryable_storage(exc):
                        raise
                    # The tick has unwound all transactions/readers. Keep the sole
                    # writer lease while waiting; do not write health into a full DB.
                    import logging
                    delay = retry_delay(failures)
                    failures += 1
                    logging.getLogger(__name__).warning(
                        "projection storage deferred: code=%s errno=%s retry_in=%ss",
                        getattr(exc, "sqlite_errorcode", None), getattr(exc, "errno", None), delay)
                    time.sleep(delay)
                    continue
'''+s[z:];p.write_text(s,encoding='utf8')
