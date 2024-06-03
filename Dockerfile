FROM python:3-slim

WORKDIR /code

COPY ./requirements.txt ./

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir -r requirements.txt

COPY ./src ./src


CMD ["python","-u", "/code/src/main.py", "--reload"]
