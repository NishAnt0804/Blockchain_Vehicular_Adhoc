"""
bsm_data.py
-----------
Generates Basic Safety Message (BSM) data for VANET simulation.
BSM fields follow the SAE J2735 standard subset used in the paper:
  - speed (km/h)
  - position (latitude, longitude)
  - acceleration (m/s^2)
  - heading (degrees 0-360)
  - timestamp (epoch seconds)
  - vehicle_id (string)
"""

import random
import time


def generate_random_bsm_data(vehicle_id=None, corrupt=False):
    """
    Generate a single BSM data dictionary.

    Parameters
    ----------
    vehicle_id : str, optional
        Identifier for the vehicle. Auto-generated if None.
    corrupt : bool
        If True, inject anomalous values that should be caught by the
        intrusion-detection smart contract.

    Returns
    -------
    dict  –  BSM data payload
    """
    if corrupt:
        # At least one field will be out of valid range
        anomaly = random.choice(["speed", "acceleration", "heading", "position"])
        speed = random.uniform(201, 500) if anomaly == "speed" else random.uniform(0, 120)
        acceleration = random.uniform(11, 30) * random.choice([1, -1]) if anomaly == "acceleration" else random.uniform(-9.5, 9.5)
        heading = random.uniform(361, 720) if anomaly == "heading" else random.uniform(0, 360)
        if anomaly == "position":
            latitude = random.uniform(91, 180) * random.choice([1, -1])
            longitude = random.uniform(181, 360) * random.choice([1, -1])
        else:
            latitude = random.uniform(8.0, 37.0)   # India lat range approx
            longitude = random.uniform(68.0, 97.0)  # India lon range approx
    else:
        speed = random.uniform(0, 120)
        acceleration = random.uniform(-9.5, 9.5)
        heading = random.uniform(0, 360)
        latitude = random.uniform(8.0, 37.0)
        longitude = random.uniform(68.0, 97.0)

    return {
        "vehicle_id": vehicle_id or f"V{random.randint(1000, 9999)}",
        "speed": round(speed, 2),
        "position": {
            "latitude": round(latitude, 6),
            "longitude": round(longitude, 6),
        },
        "acceleration": round(acceleration, 2),
        "heading": round(heading, 2),
        "timestamp": round(time.time(), 3),
    }


def generate_batch_bsm(count=10, corrupt_ratio=0.0):
    """
    Generate a batch of BSM data entries.

    Parameters
    ----------
    count : int
        Number of BSM entries to generate.
    corrupt_ratio : float
        Fraction of entries that should be corrupt (0.0 – 1.0).

    Returns
    -------
    list[dict]
    """
    data = []
    num_corrupt = int(count * corrupt_ratio)
    for i in range(count):
        is_corrupt = i < num_corrupt
        data.append(generate_random_bsm_data(
            vehicle_id=f"V{i + 1}",
            corrupt=is_corrupt,
        ))
    random.shuffle(data)
    return data
