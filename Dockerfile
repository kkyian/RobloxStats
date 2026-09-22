FROM python:3.14-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_NO_CACHE=1
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.7
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:$PATH"
COPY tests ./tests

FROM base AS test
RUN useradd --create-home tester
USER tester
CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]

FROM base AS build
COPY macos ./macos
COPY scripts ./scripts
COPY packaging ./packaging
COPY Dockerfile .dockerignore compose.yaml .env.example .gitignore ./
RUN python -m unittest discover -s tests -q
RUN uv build --out-dir /release
RUN python scripts/package_release.py /release

# Export artifacts directly with Docker BuildKit's local output.
FROM scratch AS release
COPY --from=build /release/ /

# Shareable image: extracts prebuilt artifacts without networking or credentials.
FROM python:3.14-slim AS packager
COPY --from=build /release/ /release/
VOLUME /out
CMD ["/bin/sh", "-c", "cp -R /release/. /out/ && echo 'Release files extracted to /out'"]
