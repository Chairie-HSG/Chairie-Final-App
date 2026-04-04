From datetime import datetime


def is_seat_available (check_in_time):
    now = datetime.now()
    expiry = check_in_time + datetime.timedelta (hours=2)
    if check_in_time == None:
        return True
    if now > expiry:
        return True
    else:
        return False
    
# linked to Occupied data function in seat_manager.py you should return occupied as true or false 
# and then save that change into the file.
