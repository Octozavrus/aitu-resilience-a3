FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt && useradd -u 10001 -r app
COPY service.py .
USER 10001
EXPOSE 8080
ENTRYPOINT ["python", "service.py"]
