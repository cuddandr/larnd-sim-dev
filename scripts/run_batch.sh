#!/bin/bash

#SBATCH -N 1
#SBATCH -C gpu
#SBATCH -G 4
#SBATCH -q regular
#SBATCH -J larnd-sim
#SBATCH -A dune
#SBATCH -t 02:00:00

cd $SCRATCH/larnd-sim-dev
source install_larndsim.sh -a

# Simulation settings to use
CONFIG="configs/fsd_runner.yaml"
TOOL="run"
TASKS=4
SEED=8675309

echo "Config: ${CONFIG}"
echo "Tool  : ${TOOL}"
echo "Tasks : ${TASKS}"

# List of Git references to run
git_refs=(
    "8a5bcc83" #(float32)
    "83983632" #(hackathon)
    "d7baafc6" #(develop)
)

# Run each job sequentually since the larnd-sim code
# is changed for each Git ref
for ref in "${git_refs[@]}"; do
    echo -e "\nRunning profiling for ref: ${ref}"
    srun -n 1 -c 32 --cpu-bind=cores -G ${TASKS} --gpu-bind=none \
        ./scripts/run_profile.py -s ${SEED} -r "${ref}" -n "${TASKS}" -f -t "${TOOL}" "${CONFIG}"
done
echo "All files processed!"
