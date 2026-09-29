FROM node:22-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ripgrep \
        python3 \
        python3-pip \
        python3-venv \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code
RUN npm install -g @anthropic-ai/claude-code

# Home owned by the uid the container runs as. dtl passes it as HOME_UID/HOME_GID:
# the host uid, or 0 under rootless Docker (container root = the host user).
ARG HOME_UID=1000
ARG HOME_GID=1000
RUN mkdir -p /home/claude/.claude && chown -R ${HOME_UID}:${HOME_GID} /home/claude

# Copy settings into Claude Code's config directory
COPY settings.json /home/claude/.claude/settings.json
RUN chown ${HOME_UID}:${HOME_GID} /home/claude/.claude/settings.json

ENV HOME=/home/claude
WORKDIR /workspace

ENTRYPOINT ["claude"]
