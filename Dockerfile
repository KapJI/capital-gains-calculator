FROM alpine:3.19

RUN apk --no-cache add \
    inkscape \
    make \
    ncurses \
    pandoc-cli \
    perl \
    py3-pygments \
    py3-boto3 \
    py3-requests \
    python3 \
    texlive \
    texlive-luatex \
    texmf-dist \
    texmf-dist-formatsextra \
    texmf-dist-latexextra \
    texmf-dist-pictures \
    texmf-dist-science \
    wget \
    curl \
    git


RUN luaotfload-tool --update
RUN apk --no-cache add py3-pandas

WORKDIR /build

RUN curl -sSL https://install.python-poetry.org | python3 -
RUN ln -s /root/.local/share/pypoetry/venv/bin/poetry /bin/
COPY . /build
RUN /bin/poetry build
RUN /bin/poetry install
RUN echo "/bin/poetry -C /build run cgt-calc \$@" > /bin/cgt-calc
RUN chmod +x /bin/cgt-calc

WORKDIR /data
ENTRYPOINT ["/bin/bash"]
