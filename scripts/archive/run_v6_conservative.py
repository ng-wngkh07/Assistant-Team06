"""Test a smaller update after observed arithmetic regressions in Trial 12."""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import requests

from app.config import BASE_DIR, OLLAMA_BASE_URL
from app.prepare_v5 import write_json


def main():
    root=BASE_DIR;data=root/"data/training/v6"
    adapter=root/"data/adapters/trial-13-v6-lr1e-5-layers2-iters320"
    best=root/"data/adapters/trial-13-v6-conservative-best-val"
    status=data/"conservative_status.json"
    if adapter.exists() or best.exists():raise FileExistsError("Do not overwrite an earlier conservative trial")
    if not json.loads((root/"data/evaluation/trial-12-v6-suite.json").read_text())["complete"]:raise ValueError("Previous evaluation unfinished")
    if requests.get(OLLAMA_BASE_URL+"/api/ps",timeout=15).json()["models"]:raise RuntimeError("Evict Ollama GPU models first")
    def run(command,stage):
        write_json(status,{"stage":stage,"command":command,"rationale":"Trial 12 regressed on independently computed quantitative cases"})
        subprocess.run(command,cwd=root,check=True)
    run([str(root/".venv/bin/python"),"-u","-m","app.fine_tune","--data",str(data),"--adapter-dir",str(adapter),
         "--iters","320","--num-layers","2","--learning-rate","0.00001","--max-seq-length","1536"],"training")
    log=(root/"data/runtime/v6-conservative.log").read_text()
    losses=[(int(step),float(loss)) for step,loss in re.findall(r"Iter (\d+): Val loss ([0-9.]+)",log)]
    candidates=[p for p in losses if (adapter/f"{p[0]:07d}_adapters.safetensors").exists()]
    step,loss=min(candidates,key=lambda p:p[1]);weight=adapter/f"{step:07d}_adapters.safetensors"
    best.mkdir();shutil.copy2(weight,best/"adapters.safetensors");shutil.copy2(adapter/"adapter_config.json",best/"adapter_config.json")
    checksum=hashlib.sha256(weight.read_bytes()).hexdigest()
    if hashlib.sha256((best/"adapters.safetensors").read_bytes()).hexdigest()!=checksum:raise RuntimeError("Checkpoint hash mismatch")
    ready=json.loads((adapter/"ready.json").read_text());ready.update({"iterations":step,"num_layers":2,"selected_by":"minimum corrected internal validation loss","validation_loss":loss,"origin_adapter":str(adapter)})
    write_json(best/"ready.json",ready)
    write_json(data/"conservative_checkpoint_selection.json",{"losses":losses,"selected_step":step,"selected_loss":loss,"checkpoint_sha256":checksum,"external_evaluation_not_used_to_select":True})
    run([str(root/".train-venv/bin/python"),"-u","-m","app.evaluate_v6","--data",str(data),"--adapter",str(best),
         "--output","data/evaluation/trial-13-v6-suite.json"],"evaluating")
    write_json(status,{"stage":"evaluated_pending_content_review","adapter":str(adapter),"evaluated_checkpoint":str(best),"selected_step":step,"promoted":False})


if __name__=="__main__":main()
