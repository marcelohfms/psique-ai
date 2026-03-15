FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" jinja2 python-dotenv "supabase>=2.10.0" python-multipart
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
