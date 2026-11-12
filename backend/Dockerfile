# Cine Agent 백엔드 - Google Cloud Run 배포용
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

# 1) PyTorch는 CPU 버전으로 먼저 설치 (GPU 버전은 수 GB라 이미지가 너무 커짐)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 2) 나머지 패키지
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3) 임베딩 모델을 빌드할 때 미리 받아 둔다 (첫 요청 때 다운로드 지연 방지)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('jhgan/ko-sroberta-multitask')"

# 4) 앱 코드
COPY . .

# Cloud Run은 PORT 환경변수(기본 8080)로 포트를 알려준다
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
