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

# Set up home directory for host-mapped user (UID 1000)
RUN mkdir -p /home/claude/.claude && chown -R 1000:1000 /home/claude

# Copy settings into Claude Code's config directory
COPY settings.json /home/claude/.claude/settings.json
RUN chown 1000:1000 /home/claude/.claude/settings.json

ENV HOME=/home/claude
WORKDIR /workspace

ENTRYPOINT ["claude"]
