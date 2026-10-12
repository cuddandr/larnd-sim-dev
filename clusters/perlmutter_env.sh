#!/bin/bash

module unload python 2>/dev/null
module load python/3.12
module load cudatoolkit/13.2

venv_name=${VENV_NAME:-env}
