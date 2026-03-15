FROM python:3.12-slim
WORKDIR /app
RUN pip install uv
COPY pyproject.toml .
RUN uv pip install --system fastapi "uvicorn[standard]" jinja2 python-dotenv "supabase>=2.10.0" python-multipart
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
