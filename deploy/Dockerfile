FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SILVER_DATA_DIR=/var/lib/SilverResonance \
    SILVER_RULES_PATH=/etc/SilverResonance/trading-system.md

WORKDIR /app
COPY requirements.txt requirements-server.txt ./
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --index-url "$PIP_INDEX_URL" --no-cache-dir -r requirements-server.txt \
    && groupadd --gid 10001 silver-resonance \
    && useradd --uid 10001 --gid 10001 --no-create-home \
       --home-dir /var/lib/SilverResonance --shell /usr/sbin/nologin silver-resonance
# Reserve threads and memory for the feed and 24 HTTP workers. Keep this after
# dependency installation so adjusting runtime limits reuses the pip layer.
ENV OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1
# Check native wheels inside the actual Linux/Python image without credentials
# or TqApi construction. Detect missing shared libraries before deployment.
RUN python -c "import tqsdk, akshare, pandas, scipy; from py_mini_racer import MiniRacer; engine = MiniRacer(); assert engine.eval('1+1') == 2"
COPY . /app/
USER 10001:10001

# Linux host networking in compose keeps the listener on the host's loopback.
# No EXPOSE and no published ports: Nginx is the only external entry point.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8850/healthz', timeout=3).read()"
CMD ["python", "serve_workspace.py", "--port", "8850", "--threads", "24", "--behind-proxy"]
