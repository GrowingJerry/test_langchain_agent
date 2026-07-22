#
# ollma3
# docker build -f Dockerfile -t ollama5 .

FROM ubuntu_py3:20

ADD ollama-linux-amd64.tgz /usr

WORKDIR /home/user/
COPY entry.sh .

#EXPOSE map[11434/tcp:{}]
EXPOSE 11434

ENV OLLAMA_HOST=0.0.0.0

