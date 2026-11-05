FROM python:3.10-slim

WORKDIR /app

# Create a non-root user (recommended for Hugging Face Spaces)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user:user . .

ENV PORT=7860
EXPOSE 7860

CMD ["python3", "bot.py"]
