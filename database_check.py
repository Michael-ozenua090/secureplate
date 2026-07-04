import pandas as pd
import os
from datetime import datetime, timedelta

# Cache the allowed list data
_allowed_list_cache = None
_last_cache_update = None
_cache_duration = timedelta(minutes=5)

def _load_allowed_list():
    """Load the allowed list with caching mechanism."""
    global _allowed_list_cache, _last_cache_update
    
    current_time = datetime.now()
    
    # If cache is valid, return it
    if (_allowed_list_cache is not None and _last_cache_update is not None and
            current_time - _last_cache_update < _cache_duration):
        return _allowed_list_cache
    
    try:
        # Load and cache the data
        if os.path.exists('allowed_list.csv'):
            df = pd.read_csv('allowed_list.csv')
            # Normalize plates: strip whitespace and convert to uppercase for case-insensitive comparison
            df['plate'] = df['plate'].astype(str).str.strip().str.upper()
            _allowed_list_cache = df
            _last_cache_update = current_time
            return df
        else:
            print("Warning: allowed_list.csv not found")
            return pd.DataFrame(columns=['plate', 'owner', 'category'])
    except Exception as e:
        print(f"Error loading allowed list: {str(e)}")
        return pd.DataFrame(columns=['plate', 'owner', 'category'])

def check_plate(plate_text):
    """
    Check if a license plate is in the allowed list.
    
    Args:
        plate_text (str): The license plate text to check
        
    Returns:
        tuple: ('ALLOWED'/'NOT ALLOWED', dict with plate info or None)
    """
    if not plate_text:
        return 'NOT ALLOWED', None
        
    try:
        df = _load_allowed_list()
        row = df[df['plate'] == plate_text.upper()]
        
        if not row.empty:
            return 'ALLOWED', row.iloc[0].to_dict()
        return 'NOT ALLOWED', None
    except Exception as e:
        print(f"Error checking plate: {str(e)}")
        return 'NOT ALLOWED', None
