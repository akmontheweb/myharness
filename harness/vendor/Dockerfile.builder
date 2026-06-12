# Kitchen-sink builder image for myharness.
#
# Bakes every toolchain the harness's supported stacks need into a single
# image so the sandbox never has to swap images or apt-get inside the
# container at runtime (the latter is impossible under --user $UID:$GID
# mode, which is the default).
#
# Covers: Python 3.11 + pip + venv, Java JDK 21 + Maven + Gradle,
# Node 20 LTS + npm + yarn + pnpm + tsc, SQLite, Playwright + Chromium.
# Plus make, gcc, git, curl as the universal glue.
#
# Build (one-shot, until a CI pipeline exists):
#   docker buildx build \
#     --platform linux/amd64,linux/arm64 \
#     -t ghcr.io/<owner>/harness-builder:$(date +%Y-%m-%d) \
#     -t ghcr.io/<owner>/harness-builder:stable \
#     --push harness/vendor/
#
# Pin the resulting digest into harness/graph.py:_BUILDER_IMAGE so version
# drift is impossible.

FROM eclipse-temurin:21-jdk-jammy

# NodeSource for Node 20 LTS — Ubuntu Jammy's apt ships Node 12, too old
# for current web frameworks.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list \
 && apt-get update && apt-get install -y --no-install-recommends \
      python3.11 python3-pip python3-venv \
      maven gradle \
      nodejs \
      sqlite3 libsqlite3-dev \
      make gcc git \
 && npm install -g yarn pnpm typescript playwright \
 && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
 && python3 -m pip install --no-cache-dir --upgrade \
      pip setuptools wheel \
 && PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers \
      npx --yes playwright install --with-deps chromium \
 && chmod -R a+rX /opt/playwright-browsers \
 && rm -rf /var/lib/apt/lists/*

ENV PIP_ROOT_USER_ACTION=ignore \
    JAVA_HOME=/opt/java/openjdk \
    PATH=/opt/java/openjdk/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

WORKDIR /workspace
