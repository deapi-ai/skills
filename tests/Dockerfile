# Offline test image. Build once, then run with --network none so no test can
# reach the network:
#   docker build -t deapi-skills-test -f tests/Dockerfile tests
#   docker run --rm --network none -v "$PWD:/repo" -w /repo deapi-skills-test
ARG PYTHON=3.12
FROM python:${PYTHON}-slim
RUN pip install --no-cache-dir pytest==8.3.3
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1
CMD ["sh", "tools/run_checks.sh"]
