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
    "nvidia-cudnn-cu12==9.*" \
    "diarize>=0.1.2" \
    # 2026-05-16 (v0.9.5): JWT verification for Subunit-Account Bearer
    # tokens issued by auth.subunit.ai. cryptography is the RSA backend.
    "PyJWT[crypto]>=2.8" \
    "cryptography>=42"

ENV LD_LIBRARY_PATH=/opt/conda/lib/python3.10/site-packages/nvidia/cublas/lib:/opt/conda/lib/python3.10/site-packages/nvidia/cudnn/lib:${LD_LIBRARY_PATH}

# Pre-download model on build so first request is fast
ARG WHISPER_MODEL=large-v3-turbo
ENV WHISPER_MODEL=${WHISPER_MODEL}
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')"

# 2026-05-16: Fast-mode model — multilingual Whisper `small`.
# `small` runs ~4x faster than large-v3-turbo on Pascal GPUs while
# keeping German + Umlaute reliable. Earlier picks: distil-large-v3
# (rejected: English-only) and medium (rejected: not snappy enough).
ARG WHISPER_FAST_MODEL=small
ENV WHISPER_FAST_MODEL=${WHISPER_FAST_MODEL}
RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_FAST_MODEL}', device='cpu', compute_type='int8')"

# Pre-download wespeaker (~25MB) so the first /v1/diarize call doesn't
# have to fetch from wespeaker-1256283475.cos.ap-shanghai.myqcloud.com
# at request time (which can be slow + flaky from EU servers).
RUN python3 -c "import wespeakerruntime as ws; ws.Speaker(lang='en')"

COPY main.py accounts.py cleanup.py email_send.py diarize_endpoint.py meet_endpoints.py jwt_verify.py /app/

# Persistent storage for the accounts SQLite db
VOLUME ["/data"]

EXPOSE 8005

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8005", "--workers", "1"]
