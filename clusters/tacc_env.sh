#!/bin/bash

module reset
module load gcc/15.1.0
module load cuda/13.1
module load python3/3.11.8
module list

venv_name=${VENV_NAME:-env}
