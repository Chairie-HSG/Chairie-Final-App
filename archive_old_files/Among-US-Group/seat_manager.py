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
# Gives us tools to generate random characters (used for QR codes or other codes)
import datetime
# Lets us record the current date and time when someone checks in
import json
# Lets us read and write seat data to a file so it stays saved between runs
from timer import free_expired_seats
# Imports the function that auto-frees seats after 2 hours (see timer.py file by Maira)


class SeatManager: # Defines the SeatManager class — the brain of Chairy that controls all seats
    def __init__(self, num_seats=100, state_file='seat_state.json'): # Runs automatically when you create a SeatManager; sets up 100 seats by default and names the save file
        self.num_seats = num_seats # Stores how many seats exist in the library (default 100)
        self.state_file = state_file # Stores the filename where seat data is saved ('seat_state.json')
        self.seats = self.load_state() # Immediately loads any previously saved seat data from the file so we don't lose info on restart


    def generate_qr_code(self): # Defines a helper function that creates a random unique code for each seat
        """Generates a unique QR code for a seat."""
        return ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=8)) 
    # Picks 8 random characters from letters and numbers and joins them into one string (e.g. "A3FX92KL")
    # this code is a simplified version of the same QR code conept


    def create_seats(self): # Defines the function that builds all seats from scratch (called once to set up the system)
        """Initializes the seats with unique QR codes."""
        self.seats = [] # Starts with an empty list — wipes any existing seats
        for i in range(1, self.num_seats + 1): # Loops from 1 up to and including the total number of seats (1 to 100 seats)
            seat = { 
                'id': i, # The seat's number (1, 2, 3... up to 100)
                'qr_code': self.generate_qr_code(), # Assigns a unique random code (called QR but isnt really) to this seat
                'occupied': False, # Marks the seat as free when first created
                'check_in_time': None # No check-in time yet given the seat is empty
            }
            self.seats.append(seat)  # Adds this seat's dictionary to the full list of seats
        self.save_state() # Saves the freshly created seats to the JSON file so the data isn't lost

    def check_in(self, qr_code):
        """Checks in a user to a seat based on the provided QR code."""

        free_expired_seats(self.seats)
        #go through all seats and free ones where 2 hours have passed
        self.save_state()
        #save that change to the JSON file

        for seat in self.seats:
            #loop through every seat
            if seat['qr_code'] == qr_code:
                #did we find the seat that matches the scanned QR?
                if not seat['occupied']:
                    #is that seat free?
                    seat['occupied'] = True
                    #mark it as taken now
                    seat['check_in_time'] = datetime.datetime.now().isoformat()
                    #save current time as string (for JSON)
                    self.save_state()
                    #save the change to the JSON file
                    return True, f"Checked in to seat {seat['id']}."
                    #tell the app it worked
                else:
                #seat is taken
                    return False, "Seat is already occupied."
                    #tell the app it failed
        return False, "Invalid QR code."
        #if no seat matched the QR code at all

    def check_out(self, qr_code):
        """Checks out a user from a seat based on the provided QR code."""
        for seat in self.seats:  # Loops through every seat to find the one with the matching QR code
            if seat['qr_code'] == qr_code: # Found the seat that matches the scanned QR code
                if seat['occupied']: # Checks that the seat is actually occupied (can't check out an empty seat)
                    seat['occupied'] = False  # Marks the seat as now free again
                    seat['check_in_time'] = None # Clears the check-in time since no one is sitting there
                    self.save_state() # Saves the updated (free) seat to the file
                    return True, f"Checked out from seat {seat['id']}." # Returns success and a confirmation message
                else:  # The seat matched but wasn't occupied — something is wrong
                    return False, "Seat is not currently occupied." # Returns failure with an error message
        return False, "Invalid QR code."  # No seat matched the QR code at all — the code is invalid or doesnt exist


    def save_state(self): # Defines the function that writes all seat data to the JSON file
        """Saves the current state of seats to a JSON file."""
        with open(self.state_file, 'w') as f: # Opens the JSON file in write mode ('w' overwrites existing content)
            json.dump(self.seats, f) # Converts the seats list into JSON format and writes it into the file


    def load_state(self): # Defines the function that reads previously saved seat data from the JSON file
        """Loads the state of seats from a JSON file."""
        try: # Attempts to read the file 
            with open(self.state_file, 'r') as f:  # Opens the JSON file in read mode
                return json.load(f)  # Reads the JSON data and returns it back into a Python list of seat dictionaries
        except FileNotFoundError: # If no save file exists yet (e.g. first time running the program), that error is caught
            return []   # Returns an empty list — no seats loaded, system starts fresh
print("SeatManager initialized. State loaded from file.") # Prints a confirmation message when this file is run directly (ready for testing)
