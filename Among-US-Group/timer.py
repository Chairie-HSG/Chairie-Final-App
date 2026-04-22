from datetime import datetime, timedelta

def has_expired(check_in_time):
    """
    Returns True if 2 hours have passed since check_in_time, False if not
    """
    expiry_time = check_in_time + timedelta(hours=2)
    # calculates when the 2 hours end

    current_time = datetime.now()
    # gets the current time

    if current_time > expiry_time:
        # check if current time  has passed the expiry time
        return True
    else:
        return False

def free_expired_seats(seats):
    """Goes through all seats and sets occupied to False if their 2 hours are up"""

    for seat in seats:
        # go through every seat
        if seat ['occupied']:
            # only check seats that are occupied
            check_in_time = datetime.fromisoformat(seat['check_in_time'])
            # converts the string back into a real datetime so has_expired can use it

            if has_expired (check_in_time):
                # if 2 hours passed, free the seat

                seat['occupied'] = False
                seat ['check_in_time'] = None

hallo