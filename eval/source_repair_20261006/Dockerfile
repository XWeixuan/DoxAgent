ARG BASE_IMAGE
FROM ${BASE_IMAGE}
LABEL io.doxagent.source-recovery="20261006-r3"
COPY staging/service.py /app/src/doxagent/site_strategy/service.py
COPY staging/runtime.py /app/src/doxagent/site_strategy/runtime.py
COPY staging/reuters_sources.py /app/src/doxagent/message_bus_v2/reuters_sources.py
