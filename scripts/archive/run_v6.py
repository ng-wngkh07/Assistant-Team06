"""Finish a local training cycle with sequential GPU work and persistent evidence."""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import requests

from app.config import BASE_DIR, OLLAMA_BASE_URL
from app.prepare_v5 import write_json

ROOT=BASE_DIR
DATA=ROOT/"data/training/v6"
ADAPTER=ROOT/"data/adapters/trial-12-v6-lr2e-5-iters400"
BEST=ROOT/"data/adapters/trial-12-v6-best-val"
STATUS=DATA/"pipeline_status.json"


def run(command, stage):
    write_json(STATUS,{"stage":stage,"command":command})
    print(stage,flush=True)
    subprocess.run(command,cwd=ROOT,check=True)


def main():
    if not (DATA/"approval.json").exists():raise ValueError("Audit not approved")
    if (ADAPTER/"adapters.safetensors").exists() or BEST.exists():
        raise FileExistsError("This trial already has saved weights; choose a new dataset/trial for a new run")
    # Evict every resident Ollama model before starting MLX on a 16 GB machine.
    resident=requests.get(OLLAMA_BASE_URL+"/api/ps",timeout=15).json()["models"]
    for model in resident:
        requests.post(OLLAMA_BASE_URL+"/api/generate",json={"model":model["name"],"keep_alive":0},timeout=60).raise_for_status()
    if requests.get(OLLAMA_BASE_URL+"/api/ps",timeout=15).json()["models"]:raise RuntimeError("Ollama still holds GPU models")
    py=str(ROOT/".train-venv/bin/python")
    evalcmd=[py,"-u","-m","app.evaluate_v6","--data",str(DATA)]
    run(evalcmd+["--output","data/evaluation/base-v6-suite.json","--reuse","data/evaluation/base-v5-suite.json"],"evaluating_base")
    run(evalcmd+["--adapter","data/adapters/trial-11-v5-lr3e-5-iters240","--output","data/evaluation/trial-11-v6-suite.json","--reuse","data/evaluation/trial-11-v5-suite.json"],"evaluating_previous")
    run([str(ROOT/".venv/bin/python"),"-u","-m","app.fine_tune","--data",str(DATA),"--adapter-dir",str(ADAPTER),
         "--iters","400","--learning-rate","0.00002","--max-seq-length","2048"],"training")
    # Internal validation selects the candidate; external cases never select its step.
    log=(ROOT/"data/runtime/v6-pipeline.log").read_text()
    losses=[(int(step),float(loss)) for step,loss in re.findall(r"Iter (\d+): Val loss ([0-9.]+)",log) if int(step)>0]
    available=[pair for pair in losses if (ADAPTER/f"{pair[0]:07d}_adapters.safetensors").exists()]
    step,loss=min(available,key=lambda p:p[1])
    weight=ADAPTER/f"{step:07d}_adapters.safetensors"
    if not weight.exists():raise FileNotFoundError(weight)
    if BEST.exists():raise FileExistsError("Do not overwrite previous checkpoint")
    BEST.mkdir()
    shutil.copy2(weight,BEST/"adapters.safetensors")
    shutil.copy2(ADAPTER/"adapter_config.json",BEST/"adapter_config.json")
    ready=json.loads((ADAPTER/"ready.json").read_text());ready.update({"iterations":step,"selected_by":"minimum corrected internal validation loss","validation_loss":loss,"origin_adapter":str(ADAPTER)})
    write_json(BEST/"ready.json",ready)
    checksum=hashlib.sha256(weight.read_bytes()).hexdigest()
    if hashlib.sha256((BEST/"adapters.safetensors").read_bytes()).hexdigest()!=checksum:raise RuntimeError("Checkpoint copy hash mismatch")
    write_json(DATA/"checkpoint_selection.json",{"losses":losses,"selected_step":step,"selected_loss":loss,"checkpoint_sha256":checksum,"external_evaluation_not_used_to_select":True})
    run(evalcmd+["--adapter",str(BEST),"--output","data/evaluation/trial-12-v6-suite.json"],"evaluating_new")
    write_json(STATUS,{"stage":"evaluated_pending_content_review","adapter":str(ADAPTER),"evaluated_checkpoint":str(BEST),"selected_step":step,"promoted":False})


if __name__=="__main__":
    try:main()
    except Exception as error:
        previous=json.loads(STATUS.read_text()) if STATUS.exists() else {}
        write_json(STATUS,{**previous,"stage":"failed","error":str(error)})
        raise
