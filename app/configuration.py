import os
from pathlib import Path

SAMPLE_PATH = Path(__file__).resolve().parents[1] / 'sample_data'
BOOK_FILE = os.getenv('FUEL_BOOK', 'var/fuel.sqlite')
RULESET = 'ramp-1.0'
TEMPERATURE_COEFFICIENT = '0.0008'
REFERENCE_TEMP_C = 15


def current_rules():
    return {'name':os.getenv('FUEL_RULESET',RULESET),
            'thermal_coefficient':os.getenv('FUEL_THERMAL_COEFFICIENT',TEMPERATURE_COEFFICIENT),
            'reference_temp_c':REFERENCE_TEMP_C}
