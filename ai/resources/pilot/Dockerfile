FROM docker/sandbox-templates@sha256:c5ff6434753a739c74065804950328c27519ec7032bd31d94daba7fbe248a47d
USER root
COPY trust.pem /opt/cosmic-trust.pem
ENV SSL_CERT_FILE=/opt/cosmic-trust.pem
ENV REQUESTS_CA_BUNDLE=/opt/cosmic-trust.pem
ENV NODE_EXTRA_CA_CERTS=/opt/cosmic-trust.pem
COPY pyproject.toml uv.lock /opt/cosmic/
RUN uv sync --project /opt/cosmic --locked --no-dev
RUN uv pip install --python /opt/cosmic/.venv/bin/python 'veckit @ git+https://github.com/aristoteleo/veckit.git@cc3859ab2a2305efc4544815d3f44f660efa77d4'
RUN uv pip freeze --python /opt/cosmic/.venv/bin/python > /opt/cosmic/requirements-resolved.txt
RUN npm install --global --prefix /usr/local @openai/codex@0.163.0 && test "$(/usr/local/bin/codex --version)" = "codex-cli 0.163.0"
ENV PATH="/usr/local/bin:${PATH}"
USER agent
WORKDIR /research
