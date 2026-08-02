"""One-shot stage-graph orchestration for historical-news BULK_EPOCH runs.

Import concrete modules directly; keeping this package initializer side-effect free prevents the
incremental business core from importing the bulk coordinator while it loads shared indexes.
"""
