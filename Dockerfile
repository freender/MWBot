FROM python:3-slim

# Install cron
RUN apt-get update && rm -rf /var/lib/apt/lists/*

WORKDIR /code

COPY ./requirements.txt ./

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir -r requirements.txt

COPY ./src ./src

# Start Python app
CMD ["bash", "-c", "python -u /code/src/main.py --reload"]
