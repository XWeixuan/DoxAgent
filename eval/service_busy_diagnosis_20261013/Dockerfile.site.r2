FROM doxagent-site-access:lifecycle-20261013-r1
COPY eval/message_source_repair_20261012/staging/src/doxagent/site_strategy/external_runtime.py /app/src/doxagent/site_strategy/external_runtime.py
COPY eval/message_source_repair_20261012/staging/src/doxagent/site_strategy/runtime.py /app/src/doxagent/site_strategy/runtime.py
