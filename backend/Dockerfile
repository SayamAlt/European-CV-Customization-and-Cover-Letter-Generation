FROM python:3.11-slim

# Force unbuffered stdout/stderr so logs display immediately on Render
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application files (app.py, agent.py, templates/cv/*, templates/cover letter/*)
COPY . .

# Launch uvicorn bound to Render's dynamic $PORT (falls back to 7860 if PORT is unset)
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}"]