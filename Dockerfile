FROM alpine:edge

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
    wget


RUN luaotfload-tool --update
RUN apk --no-cache add py3-pandas
RUN apk --no-cache add pipx
RUN pipx install cgt-calc
RUN pipx ensurepath

WORKDIR /data

ENTRYPOINT ["/bin/bash"]
