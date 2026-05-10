FROM python:3.13-slim

ENV PYTHONHASHSEED=0 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/ytm-executor

WORKDIR /app
COPY dist/*.whl /tmp/ytm-executor-dist/
ARG T_BANK_PYPI_URL=https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple
RUN pip install --root-user-action=ignore --no-cache-dir \
      --extra-index-url "${T_BANK_PYPI_URL}" \
      /tmp/ytm-executor-dist/*.whl \
    && rm -rf /tmp/ytm-executor-dist \
    && groupadd --system ytm-executor \
    && useradd --system --create-home --home-dir /home/ytm-executor \
      --gid ytm-executor --shell /usr/sbin/nologin ytm-executor \
    && install -d -o ytm-executor -g ytm-executor -m 0700 /home/ytm-executor/.ytm-executor

USER ytm-executor
VOLUME ["/home/ytm-executor/.ytm-executor"]
ENTRYPOINT ["ytm-executor"]
CMD ["run"]
