import asyncio
import time
import logging
import uuid
from services.live_state import LiveState

logger = logging.getLogger(__name__)

# Very basic alert definitions
def rolling_50_crossed_threshold(state, threshold):
    results = []
    for model, m_state in state.get("gate_status", {}).items():
        if m_state.get("rolling_50", 1.0) < threshold:
            results.append((model, m_state["rolling_50"]))
    return results

def predictions_per_hour_below(state, floor):
    results = []
    for model, count in state.get("predictions_per_hour", {}).items():
        if count < floor:
            results.append((model, count))
    return results

def get_alerts(state: dict) -> list[dict]:
    # Evaluate a rulebook
    new_alerts = []
    
    # Check 1: Gate threshold
    for model, val in rolling_50_crossed_threshold(state, 0.515):
        new_alerts.append({
            "type": "GATE_STATUS_CHANGE",
            "severity": "WARN",
            "message": f"Rolling-50 threshold for {model}: now {val:.1%}",
            "model": model
        })
        
    # Check 2: Pipeline stalls
    for model, val in predictions_per_hour_below(state, 5):
        new_alerts.append({
            "type": "COVERAGE_DROP",
            "severity": "WARN",
            "message": f"{model} predictions/hour dropped to {val} — pipeline may be stalled",
            "model": model
        })

    return new_alerts

async def _alert_worker():
    """ Runs every 60 seconds """
    logger.info("Alert worker started")
    # To avoid repeating instantly
    recent_alerts = {}
    
    while True:
        try:
            state_snapshot = LiveState.snapshot()
            triggered = get_alerts(state_snapshot)
            
            for alert in triggered:
                # Deduplication logic (30 min)
                key = f"{alert['type']}_{alert.get('model', '')}"
                last_time = recent_alerts.get(key, 0)
                now = time.time()
                
                if now - last_time > 1800:
                    alert_doc = {
                        "id": str(uuid.uuid4()),
                        "timestamp_ms": int(now * 1000),
                        "type": alert["type"],
                        "severity": alert["severity"],
                        "message": alert["message"],
                        "resolved": False,
                        "model": alert.get("model", None),
                        "symbol": alert.get("symbol", None)
                    }
                    LiveState.add_alert(alert_doc)
                    recent_alerts[key] = now
                    
        except Exception as e:
            logger.error(f"Alert worker error: {e}", exc_info=True)
            
        await asyncio.sleep(60)

def start_alert_worker():
    loop = asyncio.get_event_loop()
    loop.create_task(_alert_worker())
