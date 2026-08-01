# vLLM + Ray for the two-Spark cluster (ADR 0071).
#
# The upstream vllm-openai image does not ship Ray, which multi-node
# tensor-parallel requires ("Failed to import Ray" ParallelConfig error).
# Build this SAME image on the head and the worker, pinning BASE to one
# digest, so Ray/vLLM/NCCL versions can never skew between the nodes:
#
#   docker build --build-arg BASE=vllm/vllm-openai@sha256:<digest> \
#     -t family-cfo/vllm-cluster:latest docker/vllm-cluster
ARG BASE=vllm/vllm-openai:latest
FROM ${BASE}
# Keep the pin in step with the vLLM release's ray>= constraint.
RUN pip install --no-cache-dir "ray[default,cgraph]==2.49.2"
