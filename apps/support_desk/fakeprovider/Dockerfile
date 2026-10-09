# The fake model provider, in its own small container.
#
# Built from the repository root:  docker build -f apps/support_desk/fakeprovider/Dockerfile .
# It needs only Starlette and uvicorn: it does not import omnicoreagent.

FROM python:3.12-slim
RUN pip install --no-cache-dir "starlette>=0.40" "uvicorn>=0.30" \
    && useradd --create-home appuser
WORKDIR /app
COPY apps/support_desk/fakeprovider/ /app/fakeprovider/
USER appuser
EXPOSE 9000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9000/health')" || exit 1
CMD ["uvicorn", "fakeprovider.server:app", "--host", "0.0.0.0", "--port", "9000", "--log-level", "warning"]
