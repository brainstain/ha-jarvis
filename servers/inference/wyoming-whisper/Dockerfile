FROM rhasspy/wyoming-whisper:latest

# ctranslate2 (faster-whisper's GPU backend) needs CUDA 12 runtime
# libraries (libcublas, libcudnn) that this upstream image never bundled --
# only the NVIDIA driver library (libcuda.so, injected by the container
# toolkit) is present. Confirmed live: every real transcription crashed
# with "RuntimeError: Library libcublas.so.12 is not found or cannot be
# loaded" even though `nvidia-smi` worked fine inside the container --
# this silently broke real voice transcription entirely (text-only pipeline
# tests never exercised it). Standard fix for faster-whisper/ctranslate2 GPU
# Docker deployments that don't ship a full CUDA toolkit base image:
# pip-install the runtime as wheels instead.
RUN /usr/src/.venv/bin/pip install --no-cache-dir nvidia-cublas-cu12 nvidia-cudnn-cu12

ENV LD_LIBRARY_PATH=/usr/src/.venv/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/src/.venv/lib/python3.11/site-packages/nvidia/cudnn/lib
