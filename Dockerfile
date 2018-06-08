FROM ubuntu:18.04
# Get dependencies
RUN apt-get update && apt-get install -y \
    python-setuptools \
    python3-venv \
    gcc \
    python3-dev \
    python3-setuptools \
    libdpkg-perl \
    mariadb-server \
    mariadb-client \
    make \
    wget \
    unzip \
    git \
    npm
WORKDIR /apps
# Get the frontend source, clearcut, and bowtie2
RUN git clone https://github.com/arosenfeld/immunedb-frontend
RUN wget http://bioinformatics.hungry.com/clearcut/clearcut-1.0.9.tar.gz && \
    tar xzf clearcut-1.0.9.tar.gz && mv clearcut-1.0.9 clearcut
RUN wget https://github.com/BenLangmead/bowtie2/releases/download/v2.3.4.1/bowtie2-2.3.4.1-linux-x86_64.zip && \
    unzip bowtie2-2.3.4.1-linux-x86_64.zip && \
    mv bowtie2-2.3.4.1-linux-x86_64 bowtie2
# Build the frontend, clearcut, and bowtie2
WORKDIR /apps/clearcut
RUN make
WORKDIR /apps/immunedb-frontend
RUN npm install
# Copy ImmuneDB files and install
COPY requirements.txt setup.py /apps/immunedb/
COPY lib/ /apps/immunedb/lib
COPY bin/ /apps/immunedb/bin
COPY immunedb/ /apps/immunedb/immunedb
WORKDIR /apps/immunedb
RUN python3 setup.py install
# Make a directory for database configs
# Copy germlines and scripts
COPY docker/germlines/ /root/germlines
COPY docker/run.sh /root
COPY docker/mariadb/my.cnf /etc/mysql
COPY docker/serve_immunedb.sh /usr/local/sbin
COPY docker/setup_users.sql /tmp
ENV PATH "${PATH}:/apps/bowtie2"
# Expose API and frontend ports
EXPOSE 5000 8080
# Setup MySQL volume
RUN mkdir -p /share
VOLUME /share
WORKDIR /root
# Add the example data
COPY docker/example /example
CMD bash -C 'run.sh';'bash'
