FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/core/requirements.txt .
# No PyTorch here. It used to be installed explicitly with the CPU index URL,
# which contradicted this service's own requirements.txt — that file removed
# sentence-transformers precisely to drop the torch chain, and nothing in
# services/core or services/common imports torch. The line was pulling a
# multi-hundred-MB wheel into the image, and its build time, for nothing.
#
# Under the Apple-native-first decision (docs/SPRINT_PLAN.md) it would be the
# wrong dependency even if something did need it: a container on macOS has no
# Metal, so torch there runs on virtualised CPU. Anything needing the GPU runs
# on the host — Ollama, STT and TTS all do.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY services/core /app/services/core
COPY services/common /app/services/common

# course_builder._record_syllabus_check does `from tools.syllabus_check import
# check_structure, MIN_COVERAGE`. Until now this image contained only
# services/core and services/common, so /app/tools did not exist, the import
# raised ImportError, and the handler swallowed it at log level INFO —
# criterion 6 has therefore NEVER run inside a container, while
# docs/MODE_A_STATUS.md records it as "runs on every skeleton". It is the only
# quality gate with external ground truth, so HELGA_SYLLABUS_GATE=1 was inert.
#
# Only syllabus_check.py is copied, not all of tools/. The rest of that
# directory is host-run harnesses (helgabench, model_gate, the probes) that
# depend on packages core deliberately does not install; copying them in would
# put modules in the image that cannot import. syllabus_check.py itself is
# stdlib-only at module scope and reaches services.common.fact_check lazily,
# which resolves because it computes _ROOT as the parent of its own directory
# — /app — which is already PYTHONPATH.
#
# docker-compose.yml additionally bind-mounts ./tools over this path so a host
# edit takes effect without a rebuild, matching how core and common are
# handled. This COPY is what makes the image correct on its own, for any run
# that does not come through that compose file.
COPY tools/syllabus_check.py /app/tools/syllabus_check.py

ENV PYTHONPATH=/app

WORKDIR /app/services/core

EXPOSE 5003

CMD ["python", "fsm_logic.py"]
