# Dockerfile — ReconAI on Google Cloud Run.
# Keeps app.py/agents.py unchanged; Cloud Run runs a full container with a
# persistent server, unlike static/serverless-only hosts, so Streamlit
# works here with zero rewrite. See RECONAI_ARCHITECTURE_ADDENDUM.md section B.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run injects $PORT (defaults to 8080 locally); Streamlit must bind to
# it and to 0.0.0.0, and must run headless (no local browser to open).
ENV PORT=8080
EXPOSE 8080

CMD streamlit run app.py \
    --server.port=$PORT \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false
