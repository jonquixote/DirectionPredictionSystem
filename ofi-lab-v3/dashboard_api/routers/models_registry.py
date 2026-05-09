import os
import json
from fastapi import APIRouter, Depends, Query
from services.auth import verify_credentials

router = APIRouter(tags=["models"], dependencies=[Depends(verify_credentials)])
MODEL_DIR = os.environ.get("MODEL_DIR", "/data/models")

@router.get("/models")
async def list_models():
    """Registry of models from /data/models/."""
    models = []
    if not os.path.exists(MODEL_DIR):
        return {"data": [], "warnings": ["model_dir_missing"]}
        
    for name in os.listdir(MODEL_DIR):
        path = os.path.join(MODEL_DIR, name)
        if not os.path.isdir(path): continue
        
        meta_file = os.path.join(path, "metadata.json")
        gate_file = os.path.join(path, "gate_config.json")
        
        entry = {"name": name}
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r") as f:
                    entry["metadata"] = json.load(f)
            except: pass
            
        if os.path.exists(gate_file):
            try:
                with open(gate_file, "r") as f:
                    entry["gate_config"] = json.load(f)
            except: pass
            
        models.append(entry)
        
    # Sort by trained date descending if available
    models.sort(
        key=lambda m: m.get("metadata", {}).get("trained_date", ""),
        reverse=True
    )
    return {"data": models}

@router.get("/models/diff")
async def models_diff(version_a: str = Query(...), version_b: str = Query(...)):
    # Stub for computing the exact json keys diff of LGBM params
    return {
        "version_ab": [version_a, version_b],
        "diff": "Not implemented native diff yet. Compare metadata."
    }
