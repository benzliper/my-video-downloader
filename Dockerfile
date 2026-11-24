FROM python:3.11-slim

COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/* && deno --version

WORKDIR /app
RUN chmod -R 777 /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir --upgrade --pre "yt-dlp[default]"

COPY . .

RUN mkdir -p downloads && chmod -R 777 downloads

EXPOSE 7860

CMD ["gunicorn", "--bind", "0.0.0.0:7860", "--timeout", "300", "app:app"]
