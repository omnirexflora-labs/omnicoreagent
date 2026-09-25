FROM python:3.12-slim
RUN pip install --quiet "mcp>=1.9,<2"
COPY server.py /srv/server.py
CMD ["python", "/srv/server.py"]
