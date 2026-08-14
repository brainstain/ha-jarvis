# Audit v2: ha-jarvis inference plan (full repo review)

Reviewed: `SYSTEM_SPEC.md`, `servers/inference/{SERVER_SPEC.md,docker-compose.yml}`,
`servers/gateway/SERVER_SPEC.md`, `servers/agent/docker-compose.yml`,
`scripts/{deploy.sh,ollama-restart.sh,backup-cron.sh,generate-pihole-dns.sh}`,
`config/{prometheus.yml,alerts.yml}`, `.env.template`, `custom-software/*`.

Context: building `vm-inference` as a Proxmox VM with VFIO passthrough of the
RTX 3090 on the `inference` node, Ubuntu 26.04 guest. The repo is substantially
more complete than SERVER_SPEC.md alone suggested — it already has the inference
compose file, a master deploy script, backup cron, Prometheus config, and alerts.
Several findings from the first audit pass are revised accordingly.

---

## Confirmed real bugs (fix before/at first launch)

### B1. STT protocol mismatch — Wyoming vs REST (cross-file contradiction)
`servers/gateway/SERVER_SPEC.md` says the HA voice pipeline uses the **Wyoming
protocol pointing to the Inference Engine**, and the inference spec's firewall
table says "STT via Wyoming" on 8443. But the actual service
(`fedirz/faster-whisper-server`, per both the spec and the compose file) exposes
an **OpenAI-compatible REST API — not Wyoming**. HA's Wyoming integration cannot
talk to it. The image is also unmaintained under that name (project renamed to
*speaches*).

**Fix:** switch the inference compose to `rhasspy/wyoming-faster-whisper`
(standard Wyoming STT, port 10300) so HA's assist pipeline plugs in natively —
same faster-whisper engine underneath. Keep a REST server only if some other
client actually needs it. Update both SERVER_SPECs and the firewall table.

### B2. SpeechBrain build context doesn't exist
`servers/inference/docker-compose.yml` builds speechbrain from
`../../custom-software/speechbrain-speaker-id` — that directory is not in the
repo (`custom-software/` contains only `agent-orchestrator` and `mcp-servers`).
Phase 2 will fail at `docker compose build`. Good news vs the first audit pass:
Qdrant's location IS defined (agent node, `QDRANT_HOST=agent.home.local:6333`)
and it's behind a `phase2` profile so Phase 1 comes up without it.

**Fix:** write the Dockerfile + FastAPI wrapper (ECAPA-TDNN; `/enroll`,
`/identify`, `/speakers`, `/health`) and commit it at that path. Add a
SpeechBrain → Qdrant (agent:6333) rule to the inference firewall table.

### B3. Prometheus scrapes an exporter that doesn't exist on inference
`config/prometheus.yml` has job `ollama-inference` targeting
`inference.home.local:9091`. On the agent node, 9091 is served by a dedicated
`ollama-metrics` exporter container — but the **inference compose has no service
on 9091**. That job will be permanently down, and the `OllamaInferenceDown`
critical alert (which keys on `up{job="ollama-inference"}`) will fire forever.

**Fix:** add the same `ollama-metrics` exporter (ghcr.io/norskhelsenett/
ollama-metrics) to the inference compose on 9091, or repoint the job at a
blackbox/HTTP check of :11434.

### B4. VRAM budget has zero headroom — and 18 GB for Q5_K_M is optimistic
Budget sums to exactly 24 GB on a 24 GB card. Not counted: per-process CUDA
context overhead (~300–500 MB × 3 GPU services), driver reserve, and KV growth —
`OLLAMA_NUM_PARALLEL=4` splits/multiplies KV across slots. Qwen3-30B-A3B Q5_K_M
commonly lands at 20–21 GB before cache, not 18.

**Fix:** adopt one "fallback" up front rather than after the first OOM: default
to **Q4_K_M** or run **SpeechBrain on CPU** (`DEVICE=cpu` — the ECAPA model is
small and speaker ID tolerates ~2 s). Record real `nvidia-smi` numbers after
full load and correct the spec's table.

### B5. Healthchecks depend on binaries the images may not ship
`curl` in the ollama healthcheck and `nc` in the Wyoming checks aren't reliably
present in those images — a missing binary makes the container permanently
"unhealthy" (or worse, masks real failures). Verify each check inside the actual
image; prefer bash built-ins (`exec 3<>/dev/tcp/localhost/10200`) or the
services' own endpoints.

---

## Doc drift (repo files disagree with each other — reconcile)

- **D1. VM vs bare-metal contradiction** in the inference SERVER_SPEC (header:
  Proxmox VM w/ passthrough; "GPU Passthrough Notes": Container Toolkit on host,
  "not VFIO"). Decision made: **VM + VFIO, Container Toolkit inside the guest**.
  Rewrite that section; note the daemon.json now lives in the guest.
- **D2. `ok_jarvis`** is in the spec doc but (correctly) absent from the compose —
  it's not a bundled openWakeWord model. Remove from spec or add a
  custom-model training/mount step.
- **D3. vLLM** is listed as an inference-server service in SYSTEM_SPEC ("Ollama/
  vLLM", "vLLM (secondary, robust tool calling)") but appears in neither the
  inference SERVER_SPEC services table nor the compose. Decide: cut it from
  SYSTEM_SPEC or spec it as a phase.
- **D4. Thermal story**: spec promises 80 °C warn / 85 °C crit alerts + automated
  `OLLAMA_NUM_PARALLEL=1` reduction at 85 °C. Reality: `alerts.yml` has only a
  single >85 °C *warning* (no 80 °C tier, no critical, no automation), and the
  only automation in the repo is `ollama-restart.sh` — a daily 4 AM OOM-mitigation
  restart, unrelated to temperature. Recommend: add the 80 °C tier; for the
  response, prefer an Alertmanager-triggered **`nvidia-smi -pl <watts>` power
  cap** (instant, no restart, no dropped requests) over the restart-based
  NUM_PARALLEL change the spec describes.
- **D5. Whisper port**: compose maps `8443:8000`; specs/firewall say "8443" as
  if native (and it's plain HTTP despite the TLS-conventional port). Goes away
  if B1 lands (Wyoming/10300); otherwise document the mapping.

## Gaps (specified nowhere, or specified but unimplemented)

- **G1. Speaker-data backup**: `backup-cron.sh` covers agent-node services
  (Qdrant, LangGraph, Open WebUI, Grafana, Prometheus) but nothing on the
  inference node — the spec's "speechbrain_data → Daily → NAS" has no
  implementation. Add an inference section to backup-cron.sh (rsync of the
  volume to the NAS) once Phase 2 exists.
- **G2. VM-level backup & pinning**: nothing covers vzdump of vm-inference.
  Decide whether to exclude the (huge, re-pullable) `ollama_models` volume.
  Document that GPU passthrough **pins the VM to this node** — no migration/HA;
  recovery = restore backup + re-pull models.
- **G3. Firewall enforcement point undefined**: the spec's flow table names no
  mechanism. Minimum: ufw in the VM allowing gateway/agent IPs to the listed
  ports; deny the rest. Note Ollama has no auth — anyone on the LAN can use or
  delete models until this lands.
- **G4. Guest GPU housekeeping**: driver install/pinning policy in the VM,
  DKMS for kernel updates, `nvidia-persistenced` enabled (avoids first-request
  latency after container restarts). The repo's DKMS note only covers the
  abandoned host-toolkit approach.
- **G5. DNS/deploy prerequisites**: `deploy.sh` assumes `inference.home.local`
  resolves (Pi-hole, `generate-pihole-dns.sh`), SSH as root, `/opt/homelab-ai`
  on each node, and a filled `.env` (AGENT_IP etc.). The VM needs its hostname,
  a DHCP reservation/static IP, and a Pi-hole entry before `deploy.sh` works.
- **G6. Resource limits**: only ollama has a memory limit (32G). Add limits to
  whisper/speechbrain at least, so a leak can't OOM the VM and take the model
  server down with it.

## Minor

- Verify the Ollama tag `qwen3:30b-a3b-q5_K_M` exists before scripting pulls.
- `version: "3.9"` in compose files is obsolete (harmless warning on modern
  Docker; remove when touching the files).
- Ollama's 120 s `start_period` is tight for a true cold start; 180–240 s safer.
- Add a "last verified" date to each SERVER_SPEC — drift between the four spec
  files is already the main failure mode this audit found.

---

## Improvement plan (ordered)

1. **B1** — swap STT to wyoming-faster-whisper; update gateway + inference specs
   and the firewall table.
2. **B4** — pick the VRAM headroom option (Q4_K_M or CPU speaker ID) and set it
   in compose env now.
3. **B3** — add ollama-metrics exporter (:9091) to the inference compose.
4. **B5 + G6 + minor** — one compose-hardening pass: verified healthchecks, mem
   limits, longer start_period, drop `version:` key.
5. **D1/D2/D3/D5** — one doc pass reconciling the four spec files with reality.
6. **G3** — ufw rule script for the VM matching the (corrected) firewall table.
7. **B2** — build the speechbrain-speaker-id container (the one real dev task);
   then enable `--profile phase2`.
8. **G1/G2** — extend backup-cron.sh to inference; set vzdump schedule + decide
   on the models-volume exclusion.
9. **D4** — add 80 °C alert tier + Alertmanager webhook → `nvidia-smi -pl` cap.
10. **G4/G5** — guest housekeeping (persistenced, driver pinning) and the
    DNS/.env/deploy prerequisites.

## Next steps (current build state → running Phase 1)

State: GPU on vfio-pci ✔, `vm-storage` on rpool ✔, Ubuntu 26.04 ISO in place ✔,
create-VM script corrected ✔.

1. Run `01-create-vm.sh` → `qm start 201` → install Ubuntu 26.04 via console
   (whole 500 GB disk, enable OpenSSH, hostname `inference-vm` or per your DNS
   plan; give it a static IP / reservation).
2. In the guest: run `02-bootstrap-guest.sh`; reboot; verify `nvidia-smi`, then
   `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi`.
3. Add the Pi-hole DNS entry for the VM (`generate-pihole-dns.sh` /
   `custom.list`) and fill `.env` (INFERENCE_IP etc.).
4. Apply plan items 1–4 to `servers/inference/docker-compose.yml` **in the repo**
   (use the repo's compose, not the standalone one I generated earlier — the
   repo version is the source of truth for `deploy.sh`).
5. Deploy Phase 1: `deploy.sh deploy inference` (or plain
   `docker compose up -d` in the VM), then `ollama pull` the model; record real
   VRAM from `nvidia-smi` and update the spec's budget table.
6. Wire HA: Wyoming STT :10300, Piper :10200, openWakeWord :10400; LiteLLM →
   Ollama :11434. Verify the voice pipeline end-to-end.
7. ufw rules (plan item 6); confirm Prometheus targets go green, including the
   new :9091 job.
8. Backups (plan item 8), then Phase 2 once the speechbrain container exists
   (plan item 7).
9. Commit all spec corrections back to the repo (plan items 5, 9, 10).
