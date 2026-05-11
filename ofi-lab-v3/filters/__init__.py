"""Five-layer filter pipeline:
1. Prediction generation (handled by paper_trader; not a layer here)
2. Calibration (handled by ProbabilityCalibrator)
3. Paper-trade filter
4. Live-eligibility filter
5. Platform-execution filter
"""
