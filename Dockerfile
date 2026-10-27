FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY metadachs /app/metadachs
EXPOSE 8080
USER 99:100
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)"]
ENTRYPOINT ["python", "-m", "metadachs", "--config", "/config/config.toml"]
CMD ["serve"]