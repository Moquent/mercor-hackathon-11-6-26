# RootProof: the console, the benchmark and the evaluation in one isolated container (standard library only).
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY rootproof ./rootproof
COPY tasks ./tasks
COPY attempts ./attempts
COPY results ./results
COPY tests ./tests
COPY docs ./docs
COPY cases ./cases
COPY README.md ./
RUN useradd --create-home rootproof && mkdir -p cases/custom && chown -R rootproof /app
USER rootproof
EXPOSE 8080
CMD ["python", "-m", "rootproof", "serve", "--host", "0.0.0.0", "--port", "8080"]
