"""
ChairY – Library Seat Reservation System
New implementation replacing the old streamlit_app.py.

Uses:
  - supabase_client.py  → database connection
  - api.py              → all seat / auth business logic
  - indexnew.html       → design reference (palette, layout, map concept)
  - Library map images  → Library_Groundfloor.PNG / Library_First_Floor.PNG
"""

import base64
import os
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

from auth import init_auth_state, login_page, logout_button, is_logged_in, require_login
from api import (
    RESERVATION_MINUTES,
    RECHECK_HOURS,
    get_seats,
    get_user_status,
    reserve_seat,
    cancel_reservation,
    check_in_from_qr,
    release_current_seat,
)

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ChairY – HSG Study Spots",
    page_icon="🪑",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _img_b64(path: str) -> str:
    """Return a base64 data-URI for an image, or empty string if not found."""
    p = Path(path)
    if not p.exists():
        return ""
    ext = p.suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/png")
    data = base64.b64encode(p.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


def seconds_left(iso_value: str) -> int:
    if not iso_value:
        return 0
    target = datetime.fromisoformat(iso_value)
    now = datetime.now(timezone.utc)
    return max(int((target - now).total_seconds()), 0)


def countdown(iso_value: str) -> str:
    secs = seconds_left(iso_value)
    return f"{secs // 60:02d}:{secs % 60:02d}"


# ─────────────────────────────────────────────────────────────────────────────
# Global CSS  (mirrors indexnew.html palette and card style)
# ─────────────────────────────────────────────────────────────────────────────
GLOBAL_CSS = """
<style>
  #MainMenu, footer, header {visibility: hidden;}
  .block-container {padding-top: 1rem; padding-bottom: 2rem;}

  body, [data-testid="stApp"] {
    background: #f5f5f5;
    color: #222;
    font-family: Arial, sans-serif;
  }

  /* top bar */
  .chairy-logo {font-size: 28px; font-weight: bold; color: #0a8f4d;}
  .user-badge {
    background: #eef5f8; border-radius: 20px;
    padding: 5px 14px; font-size: 14px; font-weight: 500;
  }

  /* status banners */
  .banner {
    border-radius: 10px; padding: 12px 16px;
    margin-bottom: 12px; font-size: 14px;
  }
  .banner-warn {background:#fff3cd; border:1px solid #ffc107; color:#856404;}
  .banner-ok   {background:#d1e7dd; border:1px solid #0f5132; color:#0f5132;}

  /* floor summary box */
  .summary-box {
    background: white; border: 1px solid #d0d7de;
    border-radius: 12px; padding: 16px; font-size: 13px; line-height: 2;
  }

  /* detail panel */
  .detail-panel {
    background: white; border: 1px solid #d0d7de;
    border-radius: 12px; padding: 18px;
  }
  .detail-title {font-size: 20px; font-weight: 700; margin-bottom: 6px;}
  .status-badge {
    display: inline-block; border-radius: 20px;
    padding: 3px 12px; font-size: 12px; font-weight: 600; color: white;
  }
  .badge-free     {background: #1db954;}
  .badge-reserved {background: #ff9800;}
  .badge-occupied {background: #e53935;}
</style>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Map HTML  (indexnew.html concept: floor image + floating seat dots)
# ─────────────────────────────────────────────────────────────────────────────

def build_map_html(seats: list, floor: int, selected_id=None) -> str:
    # pick the best available image for the selected floor
    if floor == 0:
        map_src = _img_b64("Library_Groundfloor.PNG") or _img_b64("Library_GFloor.jpg")
    else:
        map_src = _img_b64("Library_First_Floor.PNG") or _img_b64("Library_1Floor.jpg")

    floor_seats = [s for s in seats if s["floor"] == floor]
    free_count  = sum(1 for s in floor_seats if s["status"] == "free")

    # build JS array
    items = []
    for s in floor_seats:
        color = {"free": "#1db954", "reserved": "#ff9800", "occupied": "#e53935"}.get(s["status"], "#9ca3af")
        items.append(
            f'{{id:{s["id"]},code:"{s["code"]}",status:"{s["status"]}",'
            f'color:"{color}",rMe:{str(s["reserved_by_me"]).lower()},'
            f'oMe:{str(s["occupied_by_me"]).lower()}}}'
        )
    seats_json = "[" + ",".join(items) + "]"
    sel_js = str(selected_id) if selected_id else "null"
    floor_label = "Ground Floor" if floor == 0 else "First Floor"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0;font-family:Arial,sans-serif;}}
body{{background:#f5f5f5;padding:8px;}}
.avail{{display:flex;align-items:center;gap:8px;font-size:16px;font-weight:600;margin-bottom:10px;}}
.dot{{width:12px;height:12px;border-radius:50%;display:inline-block;}}
.legend{{display:flex;gap:16px;font-size:13px;margin-bottom:12px;flex-wrap:wrap;}}
.leg-item{{display:flex;align-items:center;gap:5px;}}
.map-box{{border:2px solid #1f4c66;border-radius:10px;padding:14px;background:#fafafa;}}
.map-title{{font-size:16px;font-weight:600;color:#444;margin-bottom:10px;}}
.wrap{{position:relative;width:100%;}}
.wrap img{{width:100%;border-radius:8px;display:block;}}
.sdot{{
  position:absolute;width:18px;height:18px;border-radius:50%;
  border:2px solid white;cursor:pointer;
  transform:translate(-50%,-50%);
  transition:transform .12s;
  box-shadow:0 1px 4px rgba(0,0,0,.5);
}}
.sdot:hover{{transform:translate(-50%,-50%) scale(1.5);}}
.sdot.sel{{border:3px solid #1f4c66;transform:translate(-50%,-50%) scale(1.35);}}
.tip{{
  position:absolute;background:rgba(0,0,0,.82);color:#fff;
  padding:5px 9px;border-radius:6px;font-size:11px;
  pointer-events:none;white-space:nowrap;z-index:99;
  transform:translate(-50%,-160%);display:none;
}}
</style>
</head>
<body>
<div class="avail">
  <span class="dot" style="background:#1db954;"></span>
  <span><strong>{free_count}</strong> free seats on this floor</span>
</div>
<div class="legend">
  <span>Legend:</span>
  <div class="leg-item"><span class="dot" style="background:#1db954;"></span>Free</div>
  <div class="leg-item"><span class="dot" style="background:#ff9800;"></span>Reserved</div>
  <div class="leg-item"><span class="dot" style="background:#e53935;"></span>Occupied</div>
</div>
<div class="map-box">
  <div class="map-title">{floor_label}</div>
  <div class="wrap" id="wrap">
    <img src="{map_src}" alt="Library Map"/>
    <div id="tip" class="tip"></div>
  </div>
</div>
<script>
const seats={seats_json};
const selId={sel_js};

function autoPos(i,n){{
  const cols=6;
  const rows=Math.ceil(n/cols);
  const c=i%cols, r=Math.floor(i/cols);
  return {{
    x:8+c*84/Math.max(cols-1,1),
    y:10+r*80/Math.max(rows-1,1)
  }};
}}

function render(){{
  const wrap=document.getElementById('wrap');
  document.querySelectorAll('.sdot').forEach(d=>d.remove());
  const tip=document.getElementById('tip');
  seats.forEach((s,i)=>{{
    const p=autoPos(i,seats.length);
    const d=document.createElement('div');
    d.className='sdot'+(s.id===selId?' sel':'');
    d.style.background=s.color;
    d.style.left=p.x+'%';
    d.style.top=p.y+'%';
    d.addEventListener('mouseenter',()=>{{
      tip.style.display='block';
      tip.style.left=p.x+'%';
      tip.style.top=p.y+'%';
      let lbl=s.code+' · '+s.status.toUpperCase();
      if(s.rMe||s.oMe) lbl+=' (you)';
      tip.textContent=lbl;
    }});
    d.addEventListener('mouseleave',()=>{{tip.style.display='none';}});
    d.addEventListener('click',()=>{{
      window.parent.postMessage({{type:'seat_click',seatId:s.id}},'*');
    }});
    wrap.appendChild(d);
  }});
}}
render();
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# User status banners
# ─────────────────────────────────────────────────────────────────────────────

def render_user_banner(token: str):
    result = get_user_status(token)
    if not result["success"]:
        return
    reserved = result.get("reserved_seat")
    occupied = result.get("checked_in_seat")
    if reserved:
        st.markdown(
            f'<div class="banner banner-warn">⏳ You reserved <strong>{reserved["code"]}</strong>. '
            f'Scan QR within <strong>{countdown(reserved.get("reserved_until",""))}</strong>.</div>',
            unsafe_allow_html=True,
        )
    if occupied:
        st.markdown(
            f'<div class="banner banner-ok">✅ Checked in at <strong>{occupied["code"]}</strong>. '
            f'Expires in <strong>{countdown(occupied.get("occupied_until",""))}</strong>.</div>',
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Seat detail panel
# ─────────────────────────────────────────────────────────────────────────────

def render_detail_panel(token: str, seat):
    st.markdown("### Seat Details")
    if not seat:
        st.info("Select a seat on the map or from the list below.")
        return

    badge = {"free": "badge-free", "reserved": "badge-reserved", "occupied": "badge-occupied"}.get(seat["status"], "")
    st.markdown(f"""
    <div class="detail-panel">
      <div class="detail-title">{seat["code"]}</div>
      <p style="color:#666;font-size:13px;margin:2px 0 8px;">
        Floor {seat["floor"]} &nbsp;·&nbsp; {seat["building"]}
      </p>
      <span class="status-badge {badge}">{seat["status"].upper()}</span>
    </div>
    """, unsafe_allow_html=True)
    st.write("")

    status = seat["status"]

    if status == "free":
        if st.button("🪑 Reserve Seat", use_container_width=True, type="primary"):
            r = reserve_seat(token, seat["id"])
            st.success(r["message"]) if r["success"] else st.error(r["message"])
            if r["success"]:
                st.rerun()

    elif status == "reserved":
        if seat["reserved_by_me"]:
            remaining = countdown(seat.get("reserved_until", ""))
            st.warning(f"Reserved by you – {remaining} left to check in.")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Check In", use_container_width=True, type="primary"):
                    r = check_in_from_qr(token, seat["id"])
                    st.success(r["message"]) if r["success"] else st.error(r["message"])
                    if r["success"]:
                        st.rerun()
            with c2:
                if st.button("❌ Cancel", use_container_width=True):
                    r = cancel_reservation(token)
                    st.success(r["message"]) if r["success"] else st.error(r["message"])
                    if r["success"]:
                        st.rerun()
        else:
            st.warning("Reserved by someone else.")

    elif status == "occupied":
        if seat["occupied_by_me"]:
            st.success("You are checked in here.")
            st.info(f"Recheck in: **{countdown(seat.get('occupied_until',''))}**")
            if st.button("🚪 Release Seat", use_container_width=True):
                r = release_current_seat(token)
                st.success(r["message"]) if r["success"] else st.error(r["message"])
                if r["success"]:
                    st.rerun()
        else:
            st.error("Occupied by someone else.")


# ─────────────────────────────────────────────────────────────────────────────
# Main app
# ─────────────────────────────────────────────────────────────────────────────

def main_app():
    require_login()
    st_autorefresh(interval=5000, key="refresh")
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

    token    = st.session_state["token"]
    username = st.session_state.get("username", "")

    # ── top bar ──────────────────────────────────────────────────────────────
    col_logo, col_floor, col_user = st.columns([3, 2, 2])
    with col_logo:
        st.markdown('<div class="chairy-logo">🪑 ChairY</div>', unsafe_allow_html=True)
        st.caption("HSG Library – Study Seat Booking")
    with col_floor:
        floor = st.selectbox(
            "Floor",
            options=[0, 1],
            format_func=lambda x: "Ground Floor" if x == 0 else "First Floor",
            key="selected_floor",
            label_visibility="collapsed",
        )
    with col_user:
        st.markdown(
            f'<div class="user-badge" style="text-align:right;margin-top:4px;">👤 {username}</div>',
            unsafe_allow_html=True,
        )
        logout_button()

    st.markdown('<hr style="border:1px solid #2b6f95;margin:8px 0 16px;"/>', unsafe_allow_html=True)

    # ── user status banners ──────────────────────────────────────────────────
    render_user_banner(token)

    # ── fetch seats ──────────────────────────────────────────────────────────
    seats_result = get_seats(token)
    if not seats_result["success"]:
        st.error(f"Could not load seats: {seats_result['message']}")
        return
    seats = seats_result["seats"]

    selected_id   = st.session_state.get("selected_seat_id")
    selected_seat = next((s for s in seats if s["id"] == selected_id), None)

    # ── two-column layout: map + detail ──────────────────────────────────────
    map_col, detail_col = st.columns([3, 1])

    with map_col:
        map_html = build_map_html(seats, floor, selected_id)
        components.html(map_html, height=540, scrolling=False)

        # ── compact seat list below the map ──────────────────────────────────
        st.markdown("#### Select a seat")
        floor_seats = [s for s in seats if s["floor"] == floor]
        n_cols = 6
        for chunk_start in range(0, len(floor_seats), n_cols):
            row_seats = floor_seats[chunk_start:chunk_start + n_cols]
            cols = st.columns(n_cols)
            for col, seat in zip(cols, row_seats):
                with col:
                    emoji = {"free": "🟢", "reserved": "🟠", "occupied": "🔴"}.get(seat["status"], "⚪")
                    is_sel = seat["id"] == selected_id
                    btn_type = "primary" if is_sel else "secondary"
                    if st.button(f"{emoji} {seat['code']}", key=f"b_{seat['id']}",
                                 use_container_width=True, type=btn_type):
                        st.session_state["selected_seat_id"] = seat["id"]
                        st.rerun()

    with detail_col:
        render_detail_panel(token, selected_seat)

        # ── floor summary ─────────────────────────────────────────────────────
        st.markdown("---")
        fl_seats = [s for s in seats if s["floor"] == floor]
        free_n   = sum(1 for s in fl_seats if s["status"] == "free")
        res_n    = sum(1 for s in fl_seats if s["status"] == "reserved")
        occ_n    = sum(1 for s in fl_seats if s["status"] == "occupied")
        st.markdown(f"""
        <div class="summary-box">
          <strong>Floor Summary</strong><br>
          🟢 Free: <strong>{free_n}</strong><br>
          🟠 Reserved: <strong>{res_n}</strong><br>
          🔴 Occupied: <strong>{occ_n}</strong><br>
          📊 Total: <strong>{len(fl_seats)}</strong>
        </div>
        """, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    init_auth_state()
    if is_logged_in():
        main_app()
    else:
        st.markdown(GLOBAL_CSS, unsafe_allow_html=True)
        login_page()


if __name__ == "__main__":
    main()
