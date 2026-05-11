import numpy as np

def compute_psi(expected: list[float], actual: list[float], n_bins: int = 10) -> float:
    """
    Computes Population Stability Index (PSI)
    expected: List of values from the training distribution
    actual: List of values from the live test distribution
    n_bins: Number of quantiles to split expected distribution into
    """
    ex_arr = np.array(expected)
    act_arr = np.array(actual)

    if len(ex_arr) == 0 or len(act_arr) == 0:
        return 0.0

    # Create decile cutoffs based on the expected distribution
    try:
        quantiles = np.linspace(0, 100, n_bins + 1)
        bins = np.percentile(ex_arr, quantiles)
        
        # Ensure bins are unique to handle features with many zeros
        bins = np.unique(bins)
        if len(bins) < 2:
            return 0.0 # No variance
        
        # Add slight padding to capture edge effects
        bins[0] -= 0.0001
        bins[-1] += 0.0001
        
        # Compute frequencies in each bucket
        ex_counts, _ = np.histogram(ex_arr, bins=bins)
        act_counts, _ = np.histogram(act_arr, bins=bins)
        
        # Convert to percentages
        ex_pct = ex_counts / len(ex_arr)
        act_pct = act_counts / len(act_arr)
        
        # Avoid zero division and log(0) with small epsilon
        ex_pct = np.clip(ex_pct, 0.0001, None)
        act_pct = np.clip(act_pct, 0.0001, None)
        
        # (Act% - Exp%) * ln(Act% / Exp%)
        psi_sum = np.sum((act_pct - ex_pct) * np.log(act_pct / ex_pct))
        
        return float(psi_sum)
    except Exception as e:
        print(f"Error computing PSI: {e}")
        return 0.0
