FROM python:3.12-alpine
RUN pip install --no-cache-dir flask gunicorn
WORKDIR /app
COPY app.py .
COPY panel.html .
CMD ["gunicorn", "-b", "0.0.0.0:5000", "-w", "1", "--threads", "4", "app:app"]
