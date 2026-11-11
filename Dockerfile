FROM python:3.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -c requirements.lock . \
    && useradd --uid 10001 --create-home clouder \
    && mkdir -p /app/.data && chown clouder:clouder /app/.data
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["clouder"]
CMD ["serve", "--host", "0.0.0.0"]
