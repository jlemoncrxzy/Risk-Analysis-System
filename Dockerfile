FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    USE_LOCAL_LLM=false

WORKDIR /app

# libgomp нужен scikit-learn и llama.cpp
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# GigaChat SDK и быстрый CPU-инференс для локального fallback.
# Готовые CPU-колёса берутся из официального индекса llama-cpp-python.
RUN pip install --no-cache-dir \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
    gigachat==0.1.10 \
    llama-cpp-python==0.3.36

COPY app ./app
COPY data ./data
COPY batches.csv ./
COPY readme.md ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
