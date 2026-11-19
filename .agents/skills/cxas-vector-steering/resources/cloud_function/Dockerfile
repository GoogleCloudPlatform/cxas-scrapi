# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FUNCTION_HOST=127.0.0.1
ENV PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

RUN groupadd -r appgroup && useradd -r -g appgroup -m appuser \
    && chown -R root:appgroup /app \
    && chmod -R 0550 /app

USER appuser

EXPOSE 8080

CMD ["sh", "-c", "exec functions-framework --target=classify_intent --port=\"${PORT}\" --host=\"${FUNCTION_HOST}\""]
