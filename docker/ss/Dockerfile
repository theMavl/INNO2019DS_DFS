FROM python:3.7

RUN mkdir /src
RUN mkdir /src/storage
RUN mkdir /src/config
WORKDIR /src
ADD . /src

RUN pip install -r requirements.txt
