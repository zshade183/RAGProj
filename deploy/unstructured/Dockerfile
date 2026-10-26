FROM python:3.11-slim
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
       libmagic1 poppler-utils tesseract-ocr tesseract-ocr-chi-sim \
       libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    torch==2.14.1 torchvision==0.29.1
RUN pip install --no-cache-dir 'unstructured[pdf]==0.18.32' 'fastapi>=0.115,<1' 'uvicorn>=0.34,<1' python-multipart && python -m nltk.downloader punkt_tab averaged_perceptron_tagger_eng
WORKDIR /app
COPY server.py /app/server.py
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
