FROM python:3-slim

# Install cron
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

WORKDIR /code

COPY ./requirements.txt ./

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir -r requirements.txt

COPY ./src ./src

# Copy the cron job file
COPY ./cronjob /etc/cron.d/custom-cron

# Give execution rights to the cron job file
RUN chmod 0644 /etc/cron.d/custom-cron

# Apply the cron job
RUN crontab /etc/cron.d/custom-cron

# Start cron service and your Python app
CMD ["bash", "-c", "cron && python -u /code/src/main.py --reload"]