FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
EXPOSE 8000
# Demo database should be populated via mounted volume prior to running the container.
CMD ["rigpulse", "serve", "--db", "/app/data/rigpulse.db", "--host", "0.0.0.0", "--port", "8000"]
