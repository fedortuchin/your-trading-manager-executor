FROM python:3.13-slim

ENV PYTHONHASHSEED=0 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/ytm-executor

WORKDIR /app
COPY dist/*.whl /tmp/ytm-executor.whl
RUN pip install --no-cache-dir /tmp/ytm-executor.whl \
    && rm -f /tmp/ytm-executor.whl \
    && groupadd --system ytm-executor \
    && useradd --system --create-home --home-dir /home/ytm-executor \
      --gid ytm-executor --shell /usr/sbin/nologin ytm-executor \
    && install -d -o ytm-executor -g ytm-executor -m 0700 /home/ytm-executor/.ytm-executor

USER ytm-executor
VOLUME ["/home/ytm-executor/.ytm-executor"]
ENTRYPOINT ["ytm-executor"]
CMD ["run"]
