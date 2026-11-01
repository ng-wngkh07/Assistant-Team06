"""CPU preflight for source separation, artifact hashes and token budgets."""

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from transformers import AutoTokenizer

from app.config import DB_PATH, MLX_MODEL_DIR
from app.fine_tune import verify_auxiliary_files
from app.merge_training import _source_is_current
from app.prepare_v5 import read_records, write_json


def main():
    data=Path("data/training/v6")
    approval=json.loads((data/"approval.json").read_text())
    actual=hashlib.sha256((data/"train.jsonl").read_bytes()+(data/"valid.jsonl").read_bytes()).hexdigest()
    if actual!=approval["dataset_sha256"]:raise ValueError("Dataset checksum mismatch")
    if hashlib.sha256((data/"approved_manifest.jsonl").read_bytes()).hexdigest()!=approval["approved_manifest_sha256"]:raise ValueError("Manifest checksum mismatch")
    verify_auxiliary_files(data,approval)
    summary=json.loads((data/"summary.json").read_text())
    records=read_records(data/"approved_manifest.jsonl")
    with sqlite3.connect(DB_PATH) as db:
        heldout_hashes={r[0] for r in db.execute("SELECT file_hash FROM documents WHERE filename IN ("+','.join('?' for _ in summary['held_out_books'])+")",summary['held_out_books'])}
        leaked=[]
        for r in records:
            if not _source_is_current(db,r["pair"]):raise ValueError("Source mismatch")
            if r["pair"]["filename"] in summary["held_out_books"]:continue
            for s in r["pair"]["sources"]:
                filename=s.get("filename",r["pair"]["filename"])
                file_hash=db.execute("SELECT file_hash FROM documents WHERE filename=?",(filename,)).fetchone()[0]
                if file_hash in heldout_hashes:leaked.append(filename)
        if leaked:raise ValueError("Held-out contents present in training")
    tokenizer=AutoTokenizer.from_pretrained(str(MLX_MODEL_DIR),local_files_only=True)
    lengths={}
    train_queries=set()
    for split in ("train","valid"):
        samples=read_records(data/f"{split}.jsonl")
        values=[]
        for sample in samples:
            if [m["role"] for m in sample["messages"]]!=["system","user","assistant"]:raise ValueError("Chat roles invalid")
            rendered=tokenizer.apply_chat_template(sample["messages"],tokenize=False)
            n=len(tokenizer.encode(rendered,add_special_tokens=False))
            if n>2048:raise ValueError("Training truncation would occur")
            if split=="train":train_queries.add(sample["messages"][1]["content"])
            values.append(n)
        lengths[split]={"count":len(values),"max_tokens":max(values),"total_tokens":sum(values),"lengths":values}
    suite=json.loads(Path("data/evaluation/v6-frozen-suite.json").read_text())
    if any(c["messages"][-1]["content"] in train_queries for c in suite):raise ValueError("Exact evaluation prompt appears in training")
    synthetic=read_records(data/"curriculum_manifest.jsonl")
    synthetic_chats=Counter(json.dumps({"messages":r["messages"]},ensure_ascii=False,sort_keys=True) for r in synthetic)
    train_chats=Counter(json.dumps(r,ensure_ascii=False,sort_keys=True) for r in read_records(data/"train.jsonl"))
    if any(train_chats[k]!=count for k,count in synthetic_chats.items()):raise ValueError("Curriculum/chat alignment invalid")
    previous=Path("data/training/v5")
    old=hashlib.sha256((previous/"train.jsonl").read_bytes()+(previous/"valid.jsonl").read_bytes()).hexdigest()
    if old!=json.loads((previous/"approval.json").read_text())["dataset_sha256"]:raise ValueError("Historical dataset changed")
    write_json(data/"preflight.json",{"dataset_sha256":actual,"v5_preserved":True,"heldout_hash_leaks":leaked,
                                       "source_alignment":True,"evaluation_prompt_overlap":0,"curriculum_chat_alignment":True,
                                       "token_lengths":lengths,"max_seq_length":2048})
    print(json.dumps({"max_train_tokens":lengths["train"]["max_tokens"],"max_valid_tokens":lengths["valid"]["max_tokens"],"source_alignment":True,"heldout_hash_leaks":leaked,"v5_preserved":True}))


if __name__=="__main__":main()
