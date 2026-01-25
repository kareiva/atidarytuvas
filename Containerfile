# GSM Door Opener - Containerfile
# CentOS Stream 9 with PJSIP built from source

FROM quay.io/centos/centos:10

LABEL maintainer="GSM Door Opener"
LABEL description="Flask web application for SIP-based GSM door opener with PJSIP"
LABEL version="1.0"

ENV PJSIP_VERSION=2.16 \
    PYTHONUNBUFFERED=1 \
    FLASK_PORT=5000

RUN dnf config-manager --set-enabled crb && \
    dnf install -y epel-release

RUN dnf install -y \
    gcc \
    gcc-c++ \
    make \
    binutils \
    swig \
    python3 \
    python3-devel \
    python3-pip \
    python3-setuptools \
    python3-wheel \
    git \
    openssl-devel \
    && dnf clean all

WORKDIR /usr/src

RUN git clone --depth 1 --branch ${PJSIP_VERSION} https://github.com/pjsip/pjproject.git

WORKDIR /usr/src/pjproject

RUN ./configure \
    CFLAGS="-fPIC -DNDEBUG" \
    --prefix=/usr/local \
    --disable-video \
    --disable-sound \
    --disable-opencore-amr

RUN make dep && make && make install

RUN ldconfig

WORKDIR /usr/src/pjproject/pjsip-apps/src/swig/python
RUN make && python setup.py install

RUN python3 -c "import pjsua2; print('PJSIP Python bindings installed successfully')"

WORKDIR /app

COPY requirements.txt .

RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py sip_client.py logger.py ./
COPY templates/ templates/
COPY static/ static/
COPY .env.example .env.example

RUN dnf remove -y \
    gcc \
    gcc-c++ \
    make \
    git \
    && dnf clean all \
    && rm -rf /usr/src/pjproject \
    && rm -rf /var/cache/dnf/*

RUN useradd -r -u 1000 -s /bin/bash hamlab && \
    chown -R hamlab:hamlab /app

USER hamlab

RUN python3 -c "import pjsua2; print('✓ Non-root user can import pjsua2')"

EXPOSE 5000

CMD ["python3", "app.py"]
