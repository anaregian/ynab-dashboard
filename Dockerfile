FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
ADD --chmod=644 https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.min.js app/static/chart.umd.min.js
USER 1000:1000
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
