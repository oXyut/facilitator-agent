# select python image
FROM python:3.11-slim

# 必要なシステムツールをインストール
RUN apt-get -y update && \
    apt-get -y upgrade && \
    apt-get install -y ffmpeg

WORKDIR /root/app

COPY app/*.py ./
COPY requirements.txt ./

RUN pip install -r requirements.txt

# run
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]

