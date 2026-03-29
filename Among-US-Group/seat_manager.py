"""
seat_manager.py

Core module for the seat check-in system.
Defines the SeatManager class which manages the full lifecycle of seats:
generating seats with unique QR codes, handling check-ins, tracking occupancy,
enforcing 2-hour reservations, and persisting state to a JSON file.

This is the main interface used by the Streamlit app — all seat logic lives here.
"""

import random
import datetime
import json