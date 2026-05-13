import json """JSON module allows us to convert a dictionary into a string and vice versa,
which is useful for saving and loading data from a file - the same file that we access via 
the OS importing."""

import os """OS module allows us to interact with the operating system, 
such as checking if a file exists or deleting a file."""

# SAVE: Turn the dictionary into text and write it to a file
seats = {"seat_A1": "free", "seat_A2": "occupied", "seat_A3": "free", "seat_A4": "occupied",
        "seat_A5": "free", "seat_A6": "occupied", "seat_A7": "free", "seat_A8": "occupied", "seat_A9": "free", "seat_A10": "occupied"}
text = json.dumps(seats)
file = open("seats.json", "w")
file.write(text)
file.close()

# LOAD: Read the text from the file and turn it back into a dictionary
file = open("seats.json", "r")
text = file.read()
file.close()
seats = json.loads(text)

print(seats)
