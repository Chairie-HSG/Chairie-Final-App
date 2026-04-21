from seat_manager import SeatManager

manager=SeatManager()
manager.create_seats()

print("seats created:", len(manager.seats))
print ("first seat:", manager.seats[0])