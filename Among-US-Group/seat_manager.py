"""
seat_manager.py

Core module for the seat check-in system.
Defines the SeatManager class which manages the full lifecycle of seats:
generating seats with unique QR codes, handling check-ins, tracking occupancy,
enforcing 2-hour reservations, and persisting state to a JSON file.

This is the main interface used by the Streamlit app all seat logic lives here.

Check latest progress
"""

import random
import datetime
import json

class SeatManager:
    def __init__(self, num_seats=100, state_file='seat_state.json'):
        self.num_seats = num_seats
        self.state_file = state_file
        self.seats = self.load_state()

    def generate_qr_code(self):
        """Generates a unique QR code for a seat."""
        return ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=8))

    def create_seats(self):
        """Initializes the seats with unique QR codes."""
        self.seats = []
        for i in range(1, self.num_seats + 1):
            seat = {
                'id': i,
                'qr_code': self.generate_qr_code(),
                'occupied': False,
                'check_in_time': None
            }
            self.seats.append(seat)
        self.save_state()

    def check_in(self, qr_code):
        """Checks in a user to a seat based on the provided QR code."""
        for seat in self.seats:
            if seat['qr_code'] == qr_code:
                if not seat['occupied']:
                    seat['occupied'] = True
                    seat['check_in_time'] = datetime.datetime.now().isoformat()
                    self.save_state()
                    return True, f"Checked in to seat {seat['id']}."
                else:
                    return False, "Seat is already occupied."
        return False, "Invalid QR code."

    def check_out(self, qr_code):
        """Checks out a user from a seat based on the provided QR code."""
        for seat in self.seats:
            if seat['qr_code'] == qr_code:
                if seat['occupied']:
                    seat['occupied'] = False
                    seat['check_in_time'] = None
                    self.save_state()
                    return True, f"Checked out from seat {seat['id']}."
                else:
                    return False, "Seat is not currently occupied."
        return False, "Invalid QR code."

    def save_state(self):
        """Saves the current state of seats to a JSON file."""
        with open(self.state_file, 'w') as f:
            json.dump(self.seats, f)

    def load_state(self):
        """Loads the state of seats from a JSON file."""
        try:
            with open(self.state_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return []