unset LD_LIBRARY_PATH
unset PYTHONPATH

if [ -d "/sdf/group/lcls/" ]
then
    # for s3df
    source /sdf/group/lcls/ds/ana/sw/conda2/inst/etc/profile.d/conda.sh
    export CONDA_ENVS_DIRS=/sdf/group/lcls/ds/ana/sw/conda2/inst/envs
    export DIR_PSDM=/sdf/group/lcls/ds/ana/
    export SIT_PSDM_DATA=/sdf/data/lcls/ds/
else
    # for psana
    source /cds/sw/ds/ana/conda2-v2/inst/etc/profile.d/conda.sh
    export CONDA_ENVS_DIRS=/cds/sw/ds/ana/conda2/inst/envs/
    export DIR_PSDM=/cds/group/psdm
    export SIT_PSDM_DATA=/cds/data/psdm
fi

conda activate /cds/sw/ds/dm/conda/envs/adm-0.2.0

