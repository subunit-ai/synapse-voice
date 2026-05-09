FROM pytorch/pytorch:2.3.1-cuda11.8-cudnn8-runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# faster-whisper >= 1.2 ships with ctranslate2 >= 4.5 which requires CUDA 12 libs.
# The pytorch base image only has CUDA 11.8, so we install the CUDA 12 cublas+cudnn
# wheels and prepend them to LD_LIBRARY_PATH.
RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.32" \
    "faster-whisper>=1.2" \
    "python-multipart>=0.0.9" \
    "numpy>=1.26" \
    "pydantic[email]>=2.5" \
    "httpx>=0.27" \
    "requests>=2.31" \
    "nvidia-cublas-cu12" \
    "nvidia-cudnn-cu12==9.*"

ENV LD_LIBRARY_PATH=/opt/conda/lib/python3.10/site-packages/nvidia/cublas/lib:/opt/conda/lib/python3.10/site-packages/nvidia/cudnn/lib:${LD_LIBRARY_PATH}

# Pre-download model on build so first request is fast
ARG WHISPER_MODEL=large-v3-turbo
ENV WHISPER_MODEL=${WHISPER_MODEL}
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')"

COPY main.py accounts.py cleanup.py email_send.py /app/

# Persistent storage for the accounts SQLite db
VOLUME ["/data"]

EXPOSE 8005

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005", "--workers", "1"]
