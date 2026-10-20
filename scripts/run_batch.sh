#!/bin/bash

#SBATCH -N 1
#SBATCH -C gpu
#SBATCH -G 4
#SBATCH -q regular
#SBATCH -J larnd-sim
#SBATCH -A dune
#SBATCH -t 01:00:00

cd $SCRATCH/larnd-sim-dev
source install_larndsim.sh -a

# Simulation settings to use
CONFIG="configs/fsd_runner.yaml"
TOOL="run"

echo "Config: ${CONFIG}"
echo "Tool  : ${TOOL}"

# List of Git references to run
git_refs=(
    "212e2ef7" #(rectangle clip)
    "58acd171" #(shared diffusion)
    "e9f50d81" #(shared track/pixel)
    "baec68a8" #(float32)
    "e38b21f0" #(reference/hackathon)
)

# Run each job sequentually since the larnd-sim code
# is changed for each Git ref
for ref in "${git_refs[@]}"; do
    echo -e "\nRunning profiling for ref: ${ref}"
    srun -n 1 -c 32 --cpu-bind=cores -G 1 --gpu-bind=single:1 \
        ./scripts/run_profile.py -r "${ref}" -f -t "${TOOL}" "${CONFIG}"
done
echo "All files processed!"
