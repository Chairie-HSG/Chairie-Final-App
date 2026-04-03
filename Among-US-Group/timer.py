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
    

