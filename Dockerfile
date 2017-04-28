FROM ubuntu:xenial
WORKDIR /frotzlack

RUN apt-get update
RUN apt-get install -y curl gcc libffi-dev libssl-dev make python-dev uudeview
COPY ./shasums ./shasums
RUN curl -s -L https://github.com/DavidGriffith/frotz/archive/2.44.tar.gz > frotz-2.44.tar.gz
RUN curl -s http://www.infocom-if.org/downloads/zorki.hqx > zorki.hqx
RUN curl -s https://bootstrap.pypa.io/get-pip.py > get-pip.py
RUN sha256sum -c shasums
RUN tar zxf frotz-2.44.tar.gz
RUN uudeview -i zorki.hqx
RUN make --directory=frotz-2.44 install_dumb
RUN python get-pip.py
RUN pip install -U pip
COPY ./requirements.txt ./requirements.txt
RUN pip install -r requirements.txt
COPY . .
RUN mkdir -p log/
RUN mkdir -p save/

ENTRYPOINT python frotzlack.py
