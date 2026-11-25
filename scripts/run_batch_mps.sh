#!/bin/bash

#SBATCH -N 1
#SBATCH -C gpu
#SBATCH -G 1
#SBATCH -q shared
#SBATCH -J larnd-sim
#SBATCH -A dune
#SBATCH -t 02:00:00

cd $SCRATCH/larnd-sim-dev
source install_larndsim.sh -a

# Simulation settings to use
CONFIG="configs/2x2_runner.yaml"
TOOL="run"
TASKS=4
SEED=8675309

# Create job-private MPS state on the compute node.
export CUDA_MPS_PIPE_DIRECTORY="/tmp/nvidia-mps-${SLURM_JOB_ID:-$$}"
export CUDA_MPS_LOG_DIRECTORY="/tmp/nvidia-log-${SLURM_JOB_ID:-$$}"
mkdir -p "${CUDA_MPS_PIPE_DIRECTORY}" "${CUDA_MPS_LOG_DIRECTORY}" || exit 1
chmod 700 "${CUDA_MPS_PIPE_DIRECTORY}" "${CUDA_MPS_LOG_DIRECTORY}" || exit 1
command -v nvidia-cuda-mps-control >/dev/null || {
    echo "Error: nvidia-cuda-mps-control is not available." >&2
    exit 1
}

# Stop this job's MPS daemon on success, failure, or shell termination.
trap 'echo quit | nvidia-cuda-mps-control' EXIT
CUDA_VISIBLE_DEVICES="${SLURM_JOB_GPUS:-${CUDA_VISIBLE_DEVICES:-0}}" nvidia-cuda-mps-control -d || exit 1

echo "Config: ${CONFIG}"
echo "Tool  : ${TOOL}"
echo "Tasks : ${TASKS}"
echo "MPS   : ${CUDA_MPS_PIPE_DIRECTORY}"

# List of Git references to run
git_refs=(
    "8a5bcc83" #(float32)
    "83983632" #(hackathon)
    # "d7baafc6" #(develop)
)

# Run each job sequentually since the larnd-sim code
# is changed for each Git ref
for ref in "${git_refs[@]}"; do
    echo -e "\nRunning profiling for ref: ${ref}"
    srun -n 1 -c 32 --cpu-bind=cores -G 1 --gpu-bind=none \
        ./scripts/run_profile.py -s "${SEED}" -r "${ref}" -n "${TASKS}" --single-gpu -f -t "${TOOL}" "${CONFIG}" --reinstall
done
echo "All files processed!"
