"""
interactive_map.py
Interactive library floor map with clickable seat dots.
Renders seats based on x,y coordinates and syncs with seat status.
"""

import streamlit as st
import json
import os

def load_map_data():
    """Load the library map data from JSON file."""
    try:
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(BASE_DIR, "library_map_data (1).json")
        with open(json_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        st.error(f"Error loading map data: {e}")
        return None

def get_seat_color(status):
    """Return color based on seat status."""
    colors = {
        "available": "#1db954",  # Green
        "reserved": "#ff9800",   # Orange
        "occupied": "#e53935",   # Red
        "maintenance": "#9ca3af" # Gray
    }
    return colors.get(status.lower(), "#9ca3af")

def render_interactive_map(seats, selected_seat_id=None):
    """
    Render interactive map with clickable seat dots.
    Clicking a seat scrolls down to seat details section.
    """
    if not seats:
        st.warning("No seat data available")
        return
    
    # Filter for ground floor only (floor "0" or "Ground Floor")
    ground_floor_seats = [s for s in seats if str(s.get("floor", "0")) == "0"]
    if not ground_floor_seats:
        # If no floor field, use all seats
        ground_floor_seats = seats
    
    # Find map dimensions
    max_x = max(s.get("x", 0) for s in ground_floor_seats)
    max_y = max(s.get("y", 0) for s in ground_floor_seats)
    
    # Add padding
    map_width = max_x + 100
    map_height = max_y + 100
    
    # Create seat data for JavaScript
    seats_data = []
    for seat in ground_floor_seats:
        seats_data.append({
            "id": seat["id"],
            "x": seat.get("x", 0),
            "y": seat.get("y", 0),
            "status": seat.get("status", "available"),
            "code": seat.get("code", f"Seat {seat['id']}"),
            "color": get_seat_color(seat.get("status", "available"))
        })
    
    # HTML/CSS/JavaScript for interactive map
    html_code = f"""
    <div id="map-container" style="
        position: relative;
        width: 100%;
        height: {map_height}px;
        background: #f8f9fa;
        border: 2px solid #1f4c66;
        border-radius: 8px;
        overflow: hidden;
        margin-bottom: 20px;
    ">
        <!-- Library floor plan image background -->
        <div style="
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            opacity: 0.3;
            background-image: url('Library_GFloor.jpg');
            background-size: contain;
            background-position: center;
            background-repeat: no-repeat;
        "></div>
        
        <!-- Seat dots container -->
        <div id="seats-layer" style="
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
        ">
    """
    
    # Add seat dots
    for seat in seats_data:
        is_selected = "selected" if seat["id"] == selected_seat_id else ""
        html_code += f"""
            <div class="seat-dot {is_selected}" 
                 data-seat-id="{seat['id']}" 
                 data-seat-code="{seat['code']}"
                 style="
                    position: absolute;
                    left: {seat['x']}px;
                    top: {seat['y']}px;
                    width: 16px;
                    height: 16px;
                    border-radius: 50%;
                    background-color: {seat['color']};
                    border: 2px solid white;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.2);
                    cursor: pointer;
                    transition: all 0.2s ease;
                    z-index: 10;
                 "
                 onmouseover="this.style.transform='scale(1.3)'; this.style.zIndex='100';"
                 onmouseout="this.style.transform='scale(1)'; if(!this.classList.contains('selected')) this.style.zIndex='10';"
                 onclick="selectSeat({seat['id']}, '{seat['code']}')"
                 title="{seat['code']} - {seat['status']}">
            </div>
        """
    
    html_code += """
        </div>
        
        <!-- Legend -->
        <div style="
            position: absolute;
            bottom: 10px;
            left: 10px;
            background: rgba(255,255,255,0.9);
            padding: 10px;
            border-radius: 6px;
            font-family: Arial, sans-serif;
            font-size: 12px;
            z-index: 100;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        ">
            <div style="margin-bottom: 5px; font-weight: bold;">Legend:</div>
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 3px;">
                <span style="width: 12px; height: 12px; border-radius: 50%; background: #1db954; display: inline-block;"></span>
                <span>Available</span>
            </div>
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 3px;">
                <span style="width: 12px; height: 12px; border-radius: 50%; background: #ff9800; display: inline-block;"></span>
                <span>Reserved</span>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
                <span style="width: 12px; height: 12px; border-radius: 50%; background: #e53935; display: inline-block;"></span>
                <span>Occupied</span>
            </div>
        </div>
    </div>
    
    <script>
    function selectSeat(seatId, seatCode) {
        // Update Streamlit session state via query params
        const url = new URL(window.location);
        url.searchParams.set('seat', seatId);
        window.history.pushState({}, '', url);
        
        // Scroll to seat details section
        const seatDetails = document.getElementById('seat-details-section');
        if (seatDetails) {
            seatDetails.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        
        // Highlight the selected seat
        document.querySelectorAll('.seat-dot').forEach(dot => {
            dot.classList.remove('selected');
            dot.style.zIndex = '10';
        });
        event.target.classList.add('selected');
        event.target.style.zIndex = '100';
        
        // Trigger Streamlit rerun with new query param
        window.parent.postMessage({
            type: 'set_query_params',
            data: { seat: seatId.toString() }
        }, '*');
    }
    
    // Check URL params on load
    window.addEventListener('load', function() {
        const urlParams = new URLSearchParams(window.location.search);
        const seatId = urlParams.get('seat');
        if (seatId) {
            setTimeout(function() {
                const seatDot = document.querySelector(`.seat-dot[data-seat-id="${seatId}"]`);
                if (seatDot) {
                    seatDot.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    seatDot.style.transform = 'scale(1.5)';
                    setTimeout(() => {
                        seatDot.style.transform = 'scale(1.3)';
                    }, 1000);
                }
            }, 500);
        }
    });
    </script>
    
    <style>
    .seat-dot.selected {{
        box-shadow: 0 0 0 4px rgba(26, 115, 232, 0.5), 0 4px 8px rgba(0,0,0,0.3);
        z-index: 100 !important;
    }}
    </style>
    """
    
    st.markdown(html_code, unsafe_allow_html=True)
    
    # Return info about selected seat
    return ground_floor_seats

def handle_seat_selection(all_seats):
    """
    Handle seat selection from URL parameters.
    Returns the selected seat object or None.
    """
    # Check query params
    query_params = st.query_params
    selected_id = query_params.get("seat")
    
    if selected_id:
        try:
            seat_id = int(selected_id)
            selected_seat = next((s for s in all_seats if s["id"] == seat_id), None)
            return selected_seat
        except (ValueError, TypeError):
            pass
    
    return None
