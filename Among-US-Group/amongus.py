"""
SEAT CHECK-IN SYSTEM
====================
A system where each physical seat has a QR code (a unique random number).
When someone scans their QR code, they "check in" and hold that seat for 2 hours.
 
CORE IDEA:
- Each seat gets a permanent QR code (just a unique number, e.g. 482910)
- When scanned, the system records WHO checked in and WHEN
- After 2 hours, the seat is automatically free again
"""
 
import random                  # Used to generate random QR codes
import datetime                # Used to track check-in times and calculate expiry
 
 
 