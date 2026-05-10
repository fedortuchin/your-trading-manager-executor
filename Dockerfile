FROM python:3.13-slim

ENV PYTHONHASHSEED=0 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY dist/*.whl /tmp/ytm-executor.whl
RUN pip install --no-cache-dir /tmp/ytm-executor.whl \
    && rm -f /tmp/ytm-executor.whl

USER nobody
ENTRYPOINT ["ytm-executor"]
